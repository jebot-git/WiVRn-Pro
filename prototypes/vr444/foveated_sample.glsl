// SPDX-License-Identifier: MIT
// Integration helper, not yet connected to the OpenXR renderer.
// Maps rectilinear/source UV to decoded warped texture UV. Use the SAME bounds
// and density that were attached to this frame, never the latest gaze sample.
float vr444_warp_coordinate(float source, float lo, float hi, float density) {
    float integral = source + (density - 1.0) * clamp(source - lo, 0.0, hi - lo);
    return integral / (1.0 + (density - 1.0) * (hi - lo));
}
vec2 vr444_warp_uv(vec2 source, vec4 bounds, float density) {
    return vec2(vr444_warp_coordinate(source.x, bounds.x, bounds.y, density),
                vr444_warp_coordinate(source.y, bounds.z, bounds.w, density));
}
