/*
 * WiVRn VR streaming
 * Copyright (C) 2022  Guillaume Meunier <guillaume.meunier@centraliens.net>
 * Copyright (C) 2022  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */
#version 450

layout(push_constant) uniform pc
{
	ivec4 rgb_rect;
	ivec4 a_rect;
	vec4 scale;
	vec4 bias;
	vec4 cutout_u;
	vec4 cutout_v;
	vec4 cutout_w;
};

#ifdef VERT_SHADER

layout (location = 0) in vec2 vPosition;
layout (location = 1) in uvec2 vUV;

layout(location = 0) out vec4 outUV;
layout(location = 1) out vec2 screen_position;

void main()
{
	gl_Position = vec4(vPosition, 0.0, 1.0);
	screen_position = vPosition;
	vec2 uv = vUV;
	outUV.xy = (uv + rgb_rect.xy) / rgb_rect.zw;
	outUV.zw = (uv + a_rect.xy) / a_rect.zw;
}
#endif

#ifdef FRAG_SHADER

layout(constant_id = 0) const int alpha = 1;
layout(constant_id = 1) const bool do_srgb = false;

layout(set = 0, binding = 0) uniform sampler2D rgb[alpha + 1];

layout(location = 0) in vec4 inUV;
layout(location = 1) in vec2 screen_position;

layout(location = 0) out vec4 outColor;

float sRGB_to_linear(float x)
{
	if (x <= 0.04045)
		return x / 12.92;
	return pow((x + 0.055) / 1.055, 2.4);
}

vec4 sRGB_to_linear_rgba(vec4 x)
{
	return vec4(
	        sRGB_to_linear(x.r),
	        sRGB_to_linear(x.g),
	        sRGB_to_linear(x.b),
	        x.a);
}

void main()
{
	if (alpha == 1)
	{
		// Avoid sampling between the eyes
		vec2 a = inUV.zw;
		float d = a.x - 0.5;
		if (abs(d) *a_rect.z < 1)
			a.x += (d > 0 ? 1 : -1)  / float(a_rect.z);
		outColor = vec4(texture(rgb[0], inUV.xy).rgb, texture(rgb[1], a).r);
	}
	else
		outColor = texture(rgb[0], inUV.xy).rgba;

	if (do_srgb)
	{
		outColor = sRGB_to_linear_rgba(outColor);
	}

	if (alpha == 0)
	{
		outColor = outColor * scale + bias;
	}
	else if (outColor.a < 0.02)
	{
		outColor = vec4(0);
	}
	else
	{
		outColor.rgb /= outColor.a;
		outColor = outColor * scale + bias;
		outColor.rgb *= outColor.a;
	}
	if (cutout_w.w > 0.5 && alpha == 0) outColor.a = 1.0;
	if (cutout_u.w > 0.5)
	{
		// The RGB-only stream has no meaningful alpha; keep it opaque outside
		// the aperture. Preserve received alpha for native MR streams.
		if (alpha == 0) outColor.a = 1.0;
		vec3 ray = vec3(screen_position, 1.0);
		float w = dot(cutout_w.xyz, ray);
		if (w > 0.00001) // positive ray intersection; reject geometry behind the eye
		{
			vec2 uv = vec2(dot(cutout_u.xyz, ray), dot(cutout_v.xyz, ray)) / w;
			float edge = max(abs(uv.x), abs(uv.y));
			float aa = max(fwidth(edge), 0.0001);
			float coverage = 1.0 - smoothstep(1.0 - aa, 1.0, edge);
			outColor *= 1.0 - coverage; // erase both premultiplied RGB and alpha
			if (cutout_v.w > 0.5 && edge > 0.97 && edge < 1.0)
				outColor = vec4(0.05, 0.8, 0.65, 1.0); // live design outline
		}
	}

}

#endif
