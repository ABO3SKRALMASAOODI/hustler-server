// Shared by every fragment program. Written in the subset GLSL ES 3.00 and GLSL 3.30 share.
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
float luma(vec3 c) { return dot(c, LUMA); }

// Catmull-Rom in 9 bilinear taps. Sharper than bilinear, no blocks on a 2x upscale.
vec3 catrom(sampler2D tex, vec2 uv, vec2 size) {
  vec2 sp = uv * size;
  vec2 t1 = floor(sp - 0.5) + 0.5;
  vec2 f = sp - t1;
  vec2 w0 = f * (-0.5 + f * (1.0 - 0.5 * f));
  vec2 w1 = 1.0 + f * f * (-2.5 + 1.5 * f);
  vec2 w2 = f * (0.5 + f * (2.0 - 1.5 * f));
  vec2 w3 = f * f * (-0.5 + 0.5 * f);
  vec2 w12 = w1 + w2;
  vec2 o12 = w2 / w12;
  vec2 t0 = (t1 - 1.0) / size;
  vec2 t3 = (t1 + 2.0) / size;
  vec2 t12 = (t1 + o12) / size;
  vec3 r = vec3(0.0);
  r += textureLod(tex, vec2(t0.x, t0.y), 0.0).rgb * w0.x * w0.y;
  r += textureLod(tex, vec2(t12.x, t0.y), 0.0).rgb * w12.x * w0.y;
  r += textureLod(tex, vec2(t3.x, t0.y), 0.0).rgb * w3.x * w0.y;
  r += textureLod(tex, vec2(t0.x, t12.y), 0.0).rgb * w0.x * w12.y;
  r += textureLod(tex, vec2(t12.x, t12.y), 0.0).rgb * w12.x * w12.y;
  r += textureLod(tex, vec2(t3.x, t12.y), 0.0).rgb * w3.x * w12.y;
  r += textureLod(tex, vec2(t0.x, t3.y), 0.0).rgb * w0.x * w3.y;
  r += textureLod(tex, vec2(t12.x, t3.y), 0.0).rgb * w12.x * w3.y;
  r += textureLod(tex, vec2(t3.x, t3.y), 0.0).rgb * w3.x * w3.y;
  return r;
}

// Cubic B-spline over one mip level, 4 bilinear taps. A smooth blur at any radius, no diamond pattern.
vec4 bspline(sampler2D tex, vec2 uv, vec2 size, float lod) {
  vec2 s = size / pow(2.0, floor(lod));
  vec2 sp = uv * s - 0.5;
  vec2 fl = floor(sp);
  vec2 f = sp - fl;
  vec2 w0 = (1.0 - f) * (1.0 - f) * (1.0 - f) / 6.0;
  vec2 w1 = (4.0 + 3.0 * f * f * (f - 2.0)) / 6.0;
  vec2 w2 = (1.0 + 3.0 * f * (1.0 + f - f * f)) / 6.0;
  vec2 w3 = f * f * f / 6.0;
  vec2 g0 = w0 + w1;
  vec2 g1 = w2 + w3;
  vec2 h0 = (fl + 0.5 - 1.0 + w1 / g0) / s;
  vec2 h1 = (fl + 0.5 + 1.0 + w3 / g1) / s;
  float l = floor(lod);
  vec4 r = textureLod(tex, h0, l) * g0.x * g0.y
         + textureLod(tex, vec2(h1.x, h0.y), l) * g1.x * g0.y
         + textureLod(tex, vec2(h0.x, h1.y), l) * g0.x * g1.y
         + textureLod(tex, h1, l) * g1.x * g1.y;
  return r;
}

// A true gaussian of sigma sig texels: 32 taps on a golden-angle spiral, with radii drawn from the
// gaussian's own distribution, so all taps weigh the same and no grid can form. Each tap reads a mip
// about a third of sigma wide, so the taps overlap. A held blurred plate stays smooth at any radius.
vec4 gauss(sampler2D tex, vec2 uv, vec2 size, float sig) {
  float lod = max(log2(max(sig, 1.0)) - 1.6, 0.0);
  vec4 r = vec4(0.0);
  for (int i = 0; i < 32; i++) {
    float k = (float(i) + 0.5) / 32.0;
    float rad = sqrt(-2.0 * log(1.0 - k * 0.985)) * sig;
    float th = float(i) * 2.39996323;
    r += textureLod(tex, uv + vec2(cos(th), sin(th)) * rad / size, lod);
  }
  return r / 32.0;
}

// A piecewise ramp through up to 7 stops: the palettes of the re-renders, as finish.py draws them.
vec3 ramp3(float t, vec3 a, vec3 b, vec3 c, float tb) {
  return t < tb ? mix(a, b, t / tb) : mix(b, c, (t - tb) / (1.0 - tb));
}
vec3 ramp4(float t, vec3 a, vec3 b, vec3 c, vec3 d, float tb, float tc) {
  if (t < tb) return mix(a, b, t / tb);
  if (t < tc) return mix(b, c, (t - tb) / (tc - tb));
  return mix(c, d, (t - tc) / (1.0 - tc));
}
vec3 iron(float t) {      // thermal, iron palette: 7 stops
  const vec3 s0 = vec3(6.0, 2.0, 24.0) / 255.0, s1 = vec3(40.0, 6.0, 110.0) / 255.0, s2 = vec3(140.0, 10.0, 140.0) / 255.0;
  const vec3 s3 = vec3(222.0, 40.0, 60.0) / 255.0, s4 = vec3(255.0, 120.0, 10.0) / 255.0, s5 = vec3(255.0, 214.0, 40.0) / 255.0;
  const vec3 s6 = vec3(255.0, 255.0, 228.0) / 255.0;
  if (t < 0.18) return mix(s0, s1, t / 0.18);
  if (t < 0.38) return mix(s1, s2, (t - 0.18) / 0.20);
  if (t < 0.56) return mix(s2, s3, (t - 0.38) / 0.18);
  if (t < 0.72) return mix(s3, s4, (t - 0.56) / 0.16);
  if (t < 0.88) return mix(s4, s5, (t - 0.72) / 0.16);
  return mix(s5, s6, (t - 0.88) / 0.12);
}

// An integer hash: the same bits on every driver, so static and seeded patterns match across tiers.
uint hash3(uvec3 v) {
  uint h = v.x * 0x9E3779B1u ^ v.y * 0x85EBCA77u ^ v.z * 0xC2B2AE3Du;
  h ^= h >> 15; h *= 0x2C1B3C6Du; h ^= h >> 12; h *= 0x297A2D39u; h ^= h >> 15;
  return h;
}
// Black and white blocks of `cell` pixels that move down `scroll` pixels: one bit a block.
float static_bit(vec2 px, float cell, float scroll, float seed) {
  vec2 c = floor(vec2(px.x, px.y - scroll) / cell);
  return float(hash3(uvec3(uint(int(c.x) + 65536), uint(int(c.y) + 1048576), uint(int(seed)))) >> 31);
}

// Treatments shared by shot.treat and flash, the same maths in both.
// 0 none, 1 neg, 2 threshold, 3 burn, 4 mono, 5 tint, 6 complement, 7 posterise, 8 halftone, 9 solarise,
// 14 thermal, 15 night vision (its noise and vignette are added by the caller, which knows the frame)
vec3 sat(vec3 c, float s) { return mix(vec3(luma(c)), c, s); }
vec3 treat(vec3 c, int mode, vec3 tc, bool has_c, float th, vec2 px) {
  if (mode == 1) { c = 1.0 - c; if (has_c) c *= tc; }
  else if (mode == 2) { c = luma(c) > th ? vec3(1.0) : (has_c ? tc : vec3(0.0)); }
  else if (mode == 3) {
    float b = smoothstep(th - 0.15, th + 0.15, luma(c));
    c = mix(clamp(sat(c, 3.0), 0.0, 1.0), vec3(1.0), b);
  }
  else if (mode == 4) { c = vec3(luma(c)); if (has_c) c *= tc; }
  else if (mode == 5) { c = has_c ? tc * (0.25 + 0.75 * luma(c)) : c; }
  else if (mode == 6) { float m = max(c.r, max(c.g, c.b)) + min(c.r, min(c.g, c.b)); c = m - c; }
  else if (mode == 7) { float n = max(th * 12.0, 2.0); c = floor(c * n + 0.5) / n; }
  else if (mode == 8) {
    float pitch = 9.0;
    mat2 r = mat2(0.7071, -0.7071, 0.7071, 0.7071);
    vec2 q = r * px / pitch;
    vec2 g = fract(q) - 0.5;
    float l = luma(c);
    float d = length(g) - sqrt(max(1.0 - l, 0.0)) * 0.62;
    float dot_ = 1.0 - smoothstep(-0.08, 0.08, d);
    c = mix(vec3(1.0), has_c ? tc : vec3(0.0), dot_);
  }
  else if (mode == 9) {
    c = pow(sin(3.14159265 * min(pow(max(c, vec3(0.0)), vec3(0.85)), vec3(1.0))), vec3(0.9));
    c = clamp((sat(c, 1.35) - 0.5) * 1.08 + 0.5, 0.0, 1.0);
  }
  else if (mode == 14) { c = iron(pow(clamp((luma(c) - 0.04) / 0.92, 0.0, 1.0), 0.85)); }
  else if (mode == 15) {
    float y = min(1.0, 1.15 * pow(max(luma(c), 0.0), 0.62));
    c = ramp4(y, vec3(2.0, 10.0, 4.0) / 255.0, vec3(40.0, 170.0, 60.0) / 255.0, vec3(150.0, 250.0, 140.0) / 255.0,
              vec3(225.0, 255.0, 220.0) / 255.0, 0.5, 0.85);
  }
  return c;
}
