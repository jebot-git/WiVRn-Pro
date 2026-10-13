// Copyright (c) 2025 Hans-Kristian Arntzen
// Copyright (c) 2026 Quest3-Pyrowave contributors
// SPDX-License-Identifier: MIT
//
// Multi-level inverse Haar (first written for a [3,2] split): one dispatch reconstructs one to
// three levels.
// Each invocation owns one root coefficient and rebuilds its subtree in registers, so there is
// no shared memory, no workgroup barrier and no apron. Haar synthesis is pair-local, and every
// level of the aligned wavelet pyramid is exactly twice the next coarser one, so every fetch
// below a valid root is in bounds; only the root and, for the final plane, the stores need
// guards. The arithmetic and FP16 rounding points are those of idwt.comp's pair-local path. On
// Adreno 740 the decoded planes still differ from the level-by-level decoder by one code value on
// about 1% of pixels at equal PSNR: the driver's float-to-half conversion rounds differently
// depending on code shape (two schedules of this shader with identical arithmetic disagree too).
//
//   LEVELS          levels reconstructed (1 to 3), sampled from uLevel0 (coarsest) downwards.
//   ROOT_BELOW_TOP  the root is a coefficient one level below uLevel0; its LL value is the
//                   matching quadrant of its parent, recomputed per invocation (four cached
//                   fetches) instead of exchanged through shared memory. Otherwise the root
//                   is in uLevel0, whose layer 0 holds its LL value.
//   FINAL           output is a decoded plane: add the DC shift and guard stores against the
//                   plane size, which may be smaller than the aligned pyramid.
//
// The output image has no format qualifier (shaderStorageImageWriteWithoutFormat, as in
// idwt.comp), so the same SPIR-V legally writes the R16F/R32F LL intermediates and R8 planes.
#if FP16
#extension GL_EXT_shader_explicit_arithmetic_types_float16 : require
#endif
#if !FP16 && PRECISION == 0
#undef PRECISION
#define PRECISION 1
#endif
#if FP16 && PRECISION == 0
#define FLOAT float16_t
#define VEC2 f16vec2
#define VEC4 f16vec4
#else
#define FLOAT float
#define VEC2 vec2
#define VEC4 vec4
#endif
// DUAL (a separate program): reconstruct two components at once, the second from bindings 4-6,
// into one two-channel output. The 4:2:0 chroma final pass writes Cb and Cr as one RG8 plane, half
// the stores of two R8 planes. ELEM is one value per component.
#if DUAL
#define ELEM VEC2
#else
#define ELEM FLOAT
#endif

layout(local_size_x = 8, local_size_y = 8) in;
layout(constant_id = 0) const int LEVELS = 2;
layout(constant_id = 1) const bool ROOT_BELOW_TOP = false;
layout(constant_id = 2) const bool FINAL = false;
// Cost probe (PYROWAVE_HAAR32_PROBE, never a decode mode): 1 drops the stores unless a value is
// impossible, 2 replaces band fetches by values derived from the coordinate, 3 drops only the
// scalar final-plane stores (the 4:2:0 chroma planes in mode 3).
layout(constant_id = 3) const int PROBE = 0;
// Bit i set: binding i holds quad-packed coefficients (RGBA = the 2x2 quad (0,0), (1,0), (0,1),
// (1,1) of one band, at half the level's size), as wavelet_dequant.comp writes them with PACKED.
layout(constant_id = 4) const int PACKED = 0;
// The output holds 2x2 quads in the same order, one per texel: the RGBA8 luma plane at half size
// for the final pass, or a quad-packed LL slot (RGBA16F) for the next pass. A two-level invocation
// writes four texels instead of sixteen values, a one-level invocation one texel instead of four.
layout(constant_id = 5) const bool PACK_OUTPUT = false;
// DUAL with PACK_OUTPUT for full-resolution (4:4:4) chroma, mode 8: the two pixel-pair rows of a
// quad go side by side, the second output_size.x texels right of the first, so the pairs fit in
// output_size.y = height/2 rows. output_size then counts quads.
layout(constant_id = 6) const bool ROW_SPLIT = false;

layout(set = 0, binding = 0) uniform mediump sampler2DArray uLevel0;
layout(set = 0, binding = 1) uniform mediump sampler2DArray uLevel1;
layout(set = 0, binding = 2) uniform mediump sampler2DArray uLevel2;
layout(set = 0, binding = 3) writeonly mediump uniform image2D uOutput;
#if DUAL
layout(set = 0, binding = 4) uniform mediump sampler2DArray uLevel0B;
layout(set = 0, binding = 5) uniform mediump sampler2DArray uLevel1B;
layout(set = 0, binding = 6) uniform mediump sampler2DArray uLevel2B;
#endif

layout(push_constant) uniform Registers
{
	ivec2 root_size;
	ivec2 output_size;
	// Added to every final-plane store (mode 5 places both planes in one image); bounds checks use
	// the unshifted coordinate.
	ivec2 output_offset;
};

ELEM haar_intermediate(ELEM v)
{
#if PRECISION == 2
	return v;
#elif FP16
	return v;
#elif DUAL
	return unpackHalf2x16(packHalf2x16(v));
#else
	return FLOAT(unpackHalf2x16(packHalf2x16(vec2(float(v), 0.0))).x);
#endif
}

vec4 fetch_plane(int plane, int level, ivec2 coord, int band)
{
	if (PROBE == 2 && band != 0)
		return vec4(float((coord.x ^ coord.y) & 7) * 0.125);
#if DUAL
	if (plane != 0)
	{
		if (level == 0)
			return texelFetch(uLevel0B, ivec3(coord, band), 0);
		else if (level == 1)
			return texelFetch(uLevel1B, ivec3(coord, band), 0);
		else
			return texelFetch(uLevel2B, ivec3(coord, band), 0);
	}
#endif
	if (level == 0)
		return texelFetch(uLevel0, ivec3(coord, band), 0);
	else if (level == 1)
		return texelFetch(uLevel1, ivec3(coord, band), 0);
	else
		return texelFetch(uLevel2, ivec3(coord, band), 0);
}

bool packed(int level)
{
	return ((PACKED >> level) & 1) != 0;
}

// One coefficient. A packed level holds it in the quad at coord / 2.
FLOAT fetch_one(int plane, int level, ivec2 coord, int band)
{
	if (!packed(level))
		return FLOAT(fetch_plane(plane, level, coord, band).x);
	vec4 quad = fetch_plane(plane, level, coord >> 1, band);
	return FLOAT(quad[(coord.x & 1) | ((coord.y & 1) << 1)]);
}

ELEM fetch(int level, ivec2 coord, int band)
{
#if DUAL
	return ELEM(fetch_one(0, level, coord, band), fetch_one(1, level, coord, band));
#else
	return fetch_one(0, level, coord, band);
#endif
}

// The four coefficients 2 * quad + (i & 1, i >> 1): one texel when packed.
void fetch_quad_one(int plane, int level, ivec2 quad, int band, out FLOAT v[4])
{
	if (packed(level))
	{
		vec4 t = fetch_plane(plane, level, quad, band);
		v[0] = FLOAT(t.x); v[1] = FLOAT(t.y); v[2] = FLOAT(t.z); v[3] = FLOAT(t.w);
	}
	else
	{
		for (int i = 0; i < 4; i++)
			v[i] = FLOAT(fetch_plane(plane, level, 2 * quad + ivec2(i & 1, i >> 1), band).x);
	}
}

void fetch_quad(int level, ivec2 quad, int band, out ELEM v[4])
{
#if DUAL
	FLOAT a[4], b[4];
	fetch_quad_one(0, level, quad, band, a);
	fetch_quad_one(1, level, quad, band, b);
	for (int i = 0; i < 4; i++)
		v[i] = ELEM(a[i], b[i]);
#else
	fetch_quad_one(0, level, quad, band, v);
#endif
}

// One 2x2 synthesis, as in idwt.comp's inverse_haar_pairs: vertical, round, horizontal, round.
// q[0] = (0,0), q[1] = (1,0), q[2] = (0,1), q[3] = (1,1).
void synthesize_values(ELEM a, ELEM horizontal, ELEM vertical, ELEM diagonal, out ELEM q[4])
{
	ELEM low_even = a - FLOAT(0.5) * vertical;
	ELEM low_odd = low_even + vertical;
	ELEM high_even = horizontal - FLOAT(0.5) * diagonal;
	ELEM high_odd = high_even + diagonal;
	low_even = haar_intermediate(low_even);
	low_odd = haar_intermediate(low_odd);
	high_even = haar_intermediate(high_even);
	high_odd = haar_intermediate(high_odd);
	ELEM even_even = low_even - FLOAT(0.5) * high_even;
	ELEM even_odd = low_odd - FLOAT(0.5) * high_odd;
	q[0] = haar_intermediate(even_even);
	q[1] = haar_intermediate(even_even + high_even);
	q[2] = haar_intermediate(even_odd);
	q[3] = haar_intermediate(even_odd + high_odd);
}

void synthesize(ELEM a, int level, ivec2 coord, out ELEM q[4])
{
	synthesize_values(a, fetch(level, coord, 1), fetch(level, coord, 2), fetch(level, coord, 3), q);
}

void store(ivec2 pixel, ELEM value, bool guarded)
{
	if (FINAL)
		value += FLOAT(0.5);
#if DUAL
	if ((PROBE == 1 || (PROBE == 3 && FINAL)) && value.x != FLOAT(-12345.0))
		return;
	if (!guarded || all(lessThan(pixel, output_size)))
		imageStore(uOutput, pixel + output_offset, VEC4(value, FLOAT(0.0), FLOAT(0.0)));
#else
	if ((PROBE == 1 || (PROBE == 3 && FINAL)) && value != FLOAT(-12345.0))
		return;
	if (!guarded || all(lessThan(pixel, output_size)))
		imageStore(uOutput, pixel + output_offset, VEC4(value));
#endif
}

// DUAL with PACK_OUTPUT (modes 6 and 8): the pixel quad at texel goes out as two texels of
// horizontal pixel pairs, (Cb, Cr) of the left pixel in RG and of the right one in BA.
void store_quad(ivec2 texel, ELEM q[4], bool guarded)
{
#if DUAL
	for (int row = 0; row < 2; row++)
	{
		ivec2 pair = ROW_SPLIT ? ivec2(texel.x + row * output_size.x, texel.y) : ivec2(texel.x, 2 * texel.y + row);
		VEC4 value = VEC4(q[2 * row], q[2 * row + 1]) + VEC4(FINAL ? 0.5 : 0.0);
		if ((PROBE == 1 || (PROBE == 3 && FINAL)) && value.x != FLOAT(-12345.0))
			continue;
		if (!guarded || all(lessThan(ROW_SPLIT ? texel : pair, output_size)))
			imageStore(uOutput, pair + output_offset, value);
	}
#else
	VEC4 value = VEC4(q[0], q[1], q[2], q[3]) + VEC4(FINAL ? 0.5 : 0.0);
	if (PROBE == 1 && value.x != FLOAT(-12345.0))
		return;
	if (!guarded || all(lessThan(texel, output_size)))
		imageStore(uOutput, texel + output_offset, value);
#endif
}

void main()
{
	ivec2 root = ivec2(gl_GlobalInvocationID.xy);
	if (any(greaterThanEqual(root, root_size)))
		return;

	const int first = ROOT_BELOW_TOP ? 1 : 0;
	const int depth = LEVELS - first;
	// Workgroup-uniform: interior groups of the final plane store without per-pixel checks.
	// output_size counts texels, which are 2x2 pixel quads when PACK_OUTPUT (horizontal pairs
	// when DUAL too).
#if DUAL
	ivec2 shift = PACK_OUTPUT ? (ROW_SPLIT ? ivec2(depth - 1) : ivec2(depth - 1, depth)) : ivec2(depth);
#else
	ivec2 shift = ivec2(PACK_OUTPUT ? depth - 1 : depth);
#endif
	ivec2 group_end = ((ivec2(gl_WorkGroupID.xy) + 1) * ivec2(gl_WorkGroupSize.xy)) << shift;
	bool guarded = FINAL && any(greaterThan(group_end, output_size));

	ELEM a;
	if (ROOT_BELOW_TOP)
	{
		ELEM parent[4];
		synthesize(fetch(0, root >> 1, 0), 0, root >> 1, parent);
		int quadrant = (root.x & 1) | ((root.y & 1) << 1);
		a = parent[quadrant];
	}
	else
		a = fetch(0, root, 0);

	ELEM children[4];
	synthesize(a, first, root, children);
	if (depth == 1)
	{
		// The root's four children are the quad at root.
		if (PACK_OUTPUT)
		{
			store_quad(root, children, guarded);
			return;
		}
		for (int i = 0; i < 4; i++)
			store(2 * root + ivec2(i & 1, i >> 1), children[i], guarded);
		return;
	}

	// The children's bands form the quad at root of the next level.
	ELEM horizontal[4], vertical[4], diagonal[4];
	fetch_quad(first + 1, root, 1, horizontal);
	fetch_quad(first + 1, root, 2, vertical);
	fetch_quad(first + 1, root, 3, diagonal);
	for (int i = 0; i < 4; i++)
	{
		ivec2 child = 2 * root + ivec2(i & 1, i >> 1);
		ELEM pixels[4];
		synthesize_values(children[i], horizontal[i], vertical[i], diagonal[i], pixels);
		if (PACK_OUTPUT)
			store_quad(child, pixels, guarded);
		else
			for (int j = 0; j < 4; j++)
				store(2 * child + ivec2(j & 1, j >> 1), pixels[j], guarded);
	}
}
