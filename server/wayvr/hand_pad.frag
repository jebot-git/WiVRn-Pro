#version 310 es
precision highp float;
layout(location = 0) in vec2 in_uv;
layout(location = 0) out vec4 out_color;
layout(set = 0, binding = 0) uniform Params { vec4 data; } params;
void main() {
    vec2 p = (in_uv * 2.0 - 1.0) * vec2(1.2, -1.2);
    float r = length(p);
    float edge = 1.0 - smoothstep(0.985, 1.015, r);
    vec3 color = params.data.z < 0.5 ? vec3(0.1, 0.85, 1.0) : vec3(0.8, 0.4, 1.0);
    float grid = 1.0 - smoothstep(0.015, 0.035, min(abs(p.x), abs(p.y)));
    float ring = smoothstep(0.93, 0.96, r);
    float dot = 1.0 - smoothstep(0.08, 0.11, length(p - params.data.xy * 0.85));
    out_color = vec4(mix(color * 0.22, color, max(max(grid * 0.6, ring), dot)), edge * (0.48 + 0.5 * max(ring, dot)));
}
