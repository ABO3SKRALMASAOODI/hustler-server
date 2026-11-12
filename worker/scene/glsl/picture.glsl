// One picture through crop, re-render, treat and grade. Shared by pic (shot, card) and join.
uniform sampler2D uTex;
uniform vec2 uTexSize;
uniform vec4 uCrop;
uniform int uTreat;
uniform vec3 uTreatC;
uniform int uTreatHasC;
uniform float uTreatTh;
uniform vec4 uTreatP;       // per-treatment numbers: kaleidoscope zoom; pixel stretch origin, direction
uniform float uBlur;        // sigma, frame pixels
uniform float uSharp;
uniform vec3 uG0;           // lift, gamma, gain
uniform vec3 uG1;           // sat, contrast, temp
uniform float uTint;
uniform int uEdge;          // edge-directed upscale
uniform int uAlpha;         // the picture carries alpha, premultiplied: a logo on a card

vec3 sample_pic(vec2 uv, float texel_per_px) {
  if (uBlur > 0.01) return gauss(uTex, uv, uTexSize, uBlur * texel_per_px).rgb;
  if (texel_per_px > 1.05) return textureLod(uTex, uv, log2(texel_per_px)).rgb;
  vec3 c = uEdge == 1 ? easu(uTex, uv, uTexSize) : catrom(uTex, uv, uTexSize);
  if (uSharp > 0.0) {
    // Contrast-adaptive sharpening on the source grid: lifts soft phone footage without halos.
    vec2 e = 1.0 / uTexSize;
    vec3 n = textureLod(uTex, uv + vec2(0.0, -e.y), 0.0).rgb;
    vec3 s = textureLod(uTex, uv + vec2(0.0, e.y), 0.0).rgb;
    vec3 w = textureLod(uTex, uv + vec2(-e.x, 0.0), 0.0).rgb;
    vec3 x = textureLod(uTex, uv + vec2(e.x, 0.0), 0.0).rgb;
    vec3 mn = min(min(min(n, s), min(w, x)), c);
    vec3 mx = max(max(max(n, s), max(w, x)), c);
    vec3 amp = sqrt(clamp(min(mn, 1.0 - mx) / max(mx, vec3(1e-4)), 0.0, 1.0));
    vec3 k = -amp * uSharp * 0.18;
    c = clamp((c + (n + s + w + x) * k) / (1.0 + 4.0 * k), mn * 0.92, min(mx * 1.08, vec3(1.0)));
  }
  return c;
}

vec3 grade(vec3 c) {
  c = pow(max(c + uG0.x, vec3(0.0)), vec3(1.0 / uG0.y)) * uG0.z;
  c = mix(vec3(luma(c)), c, uG1.x);
  c = (c - 0.5) * uG1.y + 0.5;
  c.r += uG1.z; c.b -= uG1.z; c.g += uTint;
  return c;
}

// Re-renders that move the picture's pixels: they act on the item's own 0..1 space, before the crop.
vec2 warp(vec2 tv) {
  if (uTreat == 25) {          // kaleidoscope: the centre, mirrored four ways
    vec2 m = 1.0 - abs(tv * 2.0 - 1.0);
    return 0.5 + (m - 0.5) / (2.0 * max(uTreatP.x, 0.5));
  }
  if (uTreat == 26) {          // pixel stretch: one line of pixels pulled to the frame's edge
    float o = uTreatP.x;
    int d = int(uTreatP.y);    // 0 left, 1 right, 2 up, 3 down
    if (d == 0 && tv.x < o) tv.x = o;
    if (d == 1 && tv.x > o) tv.x = o;
    if (d == 2 && tv.y < o) tv.y = o;
    if (d == 3 && tv.y > o) tv.y = o;
  }
  return tv;
}

// Re-renders that read the neighbourhood: a drawing, a blueprint, a radiograph. k is source texels
// per output pixel, so a line is as wide on the frame whatever the source's size.
vec3 rerender(vec2 uv, float k, vec3 c, vec2 px, vec2 frame) {
  float l = luma(c);
  if (uTreat == 20) {          // line art: a difference of gaussians, inked, with a whisper of tone under it
    float a = luma(gauss(uTex, uv, uTexSize, 1.1 * k).rgb);
    float b = luma(gauss(uTex, uv, uTexSize, 2.5 * k).rgb);
    float lines = 1.0 - tanh(max(0.0, (b - a) * 255.0 - 0.8) * 0.42);
    float x = lines * (0.80 + 0.20 * l);
    return ramp3(x, vec3(22.0, 22.0, 30.0) / 255.0, vec3(150.0, 146.0, 140.0) / 255.0, vec3(245.0, 241.0, 233.0) / 255.0, 0.6);
  }
  if (uTreat == 21) {          // blueprint: edges and a drafting grid in white on blue
    vec2 e = 1.6 * k / uTexSize;
    float gx = luma(textureLod(uTex, uv + vec2(e.x, 0.0), 0.0).rgb) - luma(textureLod(uTex, uv - vec2(e.x, 0.0), 0.0).rgb);
    float gy = luma(textureLod(uTex, uv + vec2(0.0, e.y), 0.0).rgb) - luma(textureLod(uTex, uv - vec2(0.0, e.y), 0.0).rgb);
    float edge = smoothstep(0.035, 0.11, length(vec2(gx, gy)));
    float s = frame.x / 1080.0;
    vec2 g1 = abs(fract(px / (40.0 * s) + 0.5) - 0.5) * 40.0 * s;
    vec2 g2 = abs(fract(px / (200.0 * s) + 0.5) - 0.5) * 200.0 * s;
    float grid = max(0.16 * (1.0 - smoothstep(0.5, 1.5, min(g1.x, g1.y))), 0.34 * (1.0 - smoothstep(0.8, 2.0, min(g2.x, g2.y))));
    float v2 = max(edge, grid);
    vec2 q = px / frame - 0.5;
    v2 *= 1.0 - 0.35 * smoothstep(0.3, 0.8, length(q));
    return ramp4(v2, vec3(18.0, 54.0, 124.0) / 255.0, vec3(40.0, 86.0, 160.0) / 255.0, vec3(200.0, 222.0, 250.0) / 255.0,
                 vec3(246.0, 250.0, 255.0) / 255.0, 0.18, 0.6);
  }
  if (uTreat == 22) {          // x-ray: inverted, with the local contrast of a radiograph
    float y = pow(max(1.0 - l, 0.0), 1.35);
    float yb = pow(max(1.0 - luma(gauss(uTex, uv, uTexSize, 5.0 * k).rgb), 0.0), 1.35);
    y = clamp(y + (y - yb) * 1.6, 0.0, 1.0);
    return ramp4(y, vec3(2.0, 6.0, 14.0) / 255.0, vec3(40.0, 120.0, 170.0) / 255.0, vec3(150.0, 225.0, 245.0) / 255.0,
                 vec3(240.0, 252.0, 255.0) / 255.0, 0.45, 0.8);
  }
  return c;
}
