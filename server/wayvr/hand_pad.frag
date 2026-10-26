#version 310 es
precision highp float;
layout(location = 0) in vec2 in_uv;
layout(location = 0) out vec4 out_color;
layout(set = 0, binding = 0) uniform Params { vec4 data; vec4 limits; } params;
void main() {
    vec2 p = (in_uv * 2.0 - 1.0) * vec2(1.2, -1.2);
    float r = length(p);
    float edge = 1.0 - smoothstep(0.985, 1.015, r);
    vec3 color = params.data.z < 0.5 ? vec3(0.1, 0.85, 1.0) : vec3(0.8, 0.4, 1.0);
    if (params.limits.y > 0.5) {
        // A shaded sphere silhouette, billboarded at the tracked fingertip.
        vec3 normal = vec3(p, sqrt(max(0.0, 1.0 - dot(p, p))));
        float light = 0.25 + 0.75 * max(0.0, dot(normal, normalize(vec3(-0.4, 0.5, 1.0))));
        vec3 sphere = mix(color, vec3(1.0), params.data.w * 0.7) * light;
        out_color = vec4(sphere, edge);
        return;
    }
    float grid = 1.0 - smoothstep(0.015, 0.035, min(abs(p.x), abs(p.y)));
    float ring = smoothstep(0.93, 0.96, r);
    float dot = 1.0 - smoothstep(0.08, 0.11, length(p - params.data.xy));
    // A white centre target indicates the initial neutral-entry requirement.
    float target = (1.0 - params.data.w) * (1.0 - smoothstep(0.20, 0.25, r));
    color = mix(color, vec3(1.0), target);
    // The uniform comes from the same constant as the joystick axis deadzone.
    float neutral = 1.0 - smoothstep(params.limits.x - 0.006, params.limits.x + 0.006, r);
    float neutral_ring = 1.0 - smoothstep(0.006, 0.014, abs(r - params.limits.x));
    vec3 surface = mix(color * 0.22, color, max(max(grid * 0.6, ring), dot));
    surface = mix(surface, color * 0.42, neutral * (1.0 - dot) * 0.75);
    surface = mix(surface, vec3(0.93, 0.98, 1.0), neutral_ring * 0.95);
    float alpha = max(0.48 + 0.5 * max(ring, dot), neutral_ring * 0.95);
    out_color = vec4(surface, edge * alpha);
}
