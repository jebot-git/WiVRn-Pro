shader_type spatial;
render_mode unshaded, cull_disabled, shadows_disabled;
void fragment() {
    float x = UV.x;
    float row = floor(UV.y * 6.0);
    vec3 ink = vec3(x);
    if (row == 1.0) ink = vec3(x * 0.04);
    if (row == 2.0) ink = vec3(x, 0.0, 0.0);
    if (row == 3.0) ink = vec3(0.0, x, 0.0);
    if (row == 4.0) ink = vec3(0.0, 0.0, x);
    if (row == 5.0) ink = vec3(0.5 + (x - 0.5) * 0.008);
    // Float gradients are generated on the GPU, with no bitmap ramp quantization.
    ALBEDO = ink;
}
