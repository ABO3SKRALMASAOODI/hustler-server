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
// How the picture is read, decided once per draw from the same numbers the shader has (so a software
// rasteriser, which runs every branch, compiles only the one in use):
//   0 magnified: edge-directed or Catmull-Rom, then sharpened    1 minified: through the mips
//   2 blurred                                                    3 one texel a pixel, on the grid: the texel itself
uniform int uSamp;

vec3 sample_pic(vec2 uv, float texel_per_px) {
  if (uSamp == 2) return gauss(uTex, uv, uTexSize, uBlur * texel_per_px).rgb;
  if (uSamp == 1) return textureLod(uTex, uv, log2(texel_per_px)).rgb;
  if (uSamp == 3) return texelFetch(uTex, clamp(ivec2(uv * uTexSize), ivec2(0), ivec2(uTexSize) - 1), 0).rgb;
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

// The look: what a colourist does after the balance (scene/looks.py holds the numbers, measured on the
// reference films). A tone curve that is an S about a pivot with a floor and a ceiling, colour kept or
// drained, one hue for the shadows and one for the highlights, and skin left as it came: a key on the
// direction of skin's chroma takes skin out of the hues, holds its colour and gives it part of the contrast.
uniform int uLook;          // 0: none, and none of this is compiled in on a software rasteriser
uniform vec4 uLk0;          // contrast (slope at the pivot), pivot, floor, ceiling
uniform vec4 uLk1;          // saturation, skin keep 0..1, shadow hue amount, highlight hue amount
uniform vec3 uLkSh;         // the shadow hue, as an rgb offset of no luma
uniform vec3 uLkHi;         // the highlight hue, the same
uniform vec4 uSk;           // the skin key: axis cos, axis sin, cos of the half width, cos of half width plus feather
uniform float uLkDrop;      // the level of the room against the face's (1: the same): outside the window the picture goes down
uniform vec4 uLkWin;        // the window on the face, in the picture's own 0..1: centre x, y, radius x, y (radius 0: no window)
float gWin = 0.0;           // how far inside the window this pixel is, 0..1: set by the caller, which knows where in the picture it is

// A colourist's window: 1 inside an ellipse round the face, falling to 0 over as much again. Wide and soft, so
// there is no edge to see: it is the room that goes down, not a spot that goes up.
float window_at(vec2 uv) {
  if (uLkWin.z <= 0.0) return 0.0;
  return 1.0 - smoothstep(0.8, 1.9, length((uv - uLkWin.xy) / uLkWin.zw));
}

float skin_key(vec3 c, float y) {
  float yy = max(y, 0.05);
  vec2 k = vec2(c.r - y, y - c.b) / yy;
  float m = length(k);
  float along = dot(k, uSk.xy) / max(m, 1e-4);
  return smoothstep(uSk.w, uSk.z, along) * smoothstep(0.07, 0.15, m) * (1.0 - smoothstep(0.95, 1.25, m)) * smoothstep(0.06, 0.16, y);
}

vec3 look(vec3 c) {
  c = clamp(c, 0.0, 1.0);
  float y = luma(c);
  float keep = skin_key(c, y) * uLk1.y;
  c *= mix(uLkDrop, 1.0, gWin);
  y = luma(c);
  float p = uLk0.y;
  float k = mix(uLk0.x, 1.0 + (uLk0.x - 1.0) * 0.5, keep);
  float t = y < p ? p * pow(y / p, k) : 1.0 - (1.0 - p) * pow((1.0 - y) / (1.0 - p), k);
  c *= min(t / max(y, 1e-3), 4.0);
  float s = mix(uLk1.x, clamp(uLk1.x, 0.92, 1.08), keep);
  c = mix(vec3(t), c, s);
  c = uLk0.z + (uLk0.w - uLk0.z) * c;
  float free_ = 1.0 - keep;
  c += uLkSh * (uLk1.z * (1.0 - smoothstep(0.0, 0.5, t)) * free_) + uLkHi * (uLk1.w * smoothstep(0.5, 1.0, t) * (1.0 - 0.5 * keep));
  return c;
}

vec3 grade(vec3 c) {
  c = pow(max(c + uG0.x, vec3(0.0)), vec3(1.0 / uG0.y)) * uG0.z;
  c = mix(vec3(luma(c)), c, uG1.x);
  c = (c - 0.5) * uG1.y + 0.5;
  c.r += uG1.z; c.b -= uG1.z; c.g += uTint;
  if (uLook == 1) c = look(c);
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
