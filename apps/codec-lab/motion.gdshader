shader_type spatial;
render_mode unshaded, cull_disabled, shadows_disabled;
uniform float phase = 0.0;
void fragment() {
    vec2 p = UV * vec2(1024.0, 768.0);
    vec3 ink;
    if (UV.y < 0.28) {
        float stripe = mod(floor((p.x + phase * 80.0 + p.y * 0.35) / 3.0), 2.0);
        // Linear shader value corresponding to an sRGB green level of 0.2973.
        ink = mix(vec3(1.0, 0.0, 0.0), vec3(0.0, 0.0719, 0.0), stripe);
    } else if (UV.y < 0.73) {
        vec2 d = (UV - vec2(0.5, 0.505)) * vec2(1.3333, 1.0);
        float angle = atan(d.y, d.x) - phase * 0.5235988;
        float spoke = step(0.0, sin(angle * 32.0));
        float ring = step(0.0, sin(length(d) * 650.0 - phase * 2.0943951));
        ink = vec3(mix(0.04, 0.95, mix(spoke, ring, step(0.17, length(d)))));
    } else {
        float checker = mod(floor((p.x + phase * 40.0) / 5.0) + floor((p.y + phase * 20.0) / 5.0), 2.0);
        ink = mix(vec3(0.02, 0.08, 0.1), vec3(0.0, 0.85, 1.0), checker);
    }
    ALBEDO = ink;
}
