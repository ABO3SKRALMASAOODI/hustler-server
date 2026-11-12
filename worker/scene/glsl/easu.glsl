// Edge-directed upscale after AMD FidelityFX FSR1 EASU (MIT). Reads 12 source texels, finds the local
// edge direction from luma, then takes a stretched Lanczos along the edge, so a low-resolution upload
// keeps clean diagonals at 2x. Clamped to the 4 nearest texels, so it cannot ring.
void easuSet(inout vec2 dir, inout float len, vec2 pp, int which, float lA, float lB, float lC, float lD, float lE) {
  float w = which == 0 ? (1.0 - pp.x) * (1.0 - pp.y) : which == 1 ? pp.x * (1.0 - pp.y) : which == 2 ? (1.0 - pp.x) * pp.y : pp.x * pp.y;
  float dc = lD - lC, cb = lC - lB;
  float lenX = 1.0 / max(max(abs(dc), abs(cb)), 1e-5);
  float dirX = lD - lB;
  dir.x += dirX * w;
  lenX = clamp(abs(dirX) * lenX, 0.0, 1.0);
  len += lenX * lenX * w;
  float ec = lE - lC, ca = lC - lA;
  float lenY = 1.0 / max(max(abs(ec), abs(ca)), 1e-5);
  float dirY = lE - lA;
  dir.y += dirY * w;
  lenY = clamp(abs(dirY) * lenY, 0.0, 1.0);
  len += lenY * lenY * w;
}

void easuTap(inout vec3 ac, inout float aw, vec2 off, vec2 dir, vec2 len, float lob, float clp, vec3 c) {
  vec2 q = vec2(off.x * dir.x + off.y * dir.y, -off.x * dir.y + off.y * dir.x) * len;
  float d2 = min(dot(q, q), clp);
  float wb = 0.4 * d2 - 1.0;
  float wa = lob * d2 - 1.0;
  wb *= wb;
  wa *= wa;
  wb = 1.5625 * wb - 0.5625;
  float w = wb * wa;
  ac += c * w;
  aw += w;
}

float easuL(vec3 c) { return c.b * 0.5 + (c.r * 0.5 + c.g); }

vec3 easu(sampler2D tex, vec2 uv, vec2 size) {
  vec2 sp = uv * size - 0.5;
  vec2 fp = floor(sp);
  vec2 pp = sp - fp;
  ivec2 p = ivec2(fp), m = ivec2(size) - 1;
  #define T(x, y) texelFetch(tex, clamp(p + ivec2(x, y), ivec2(0), m), 0).rgb
  vec3 b = T(0, -1), c = T(1, -1), e = T(-1, 0), f = T(0, 0), g = T(1, 0), h = T(2, 0);
  vec3 i = T(-1, 1), j = T(0, 1), k = T(1, 1), l = T(2, 1), n = T(0, 2), o = T(1, 2);
  #undef T
  float bL = easuL(b), cL = easuL(c), eL = easuL(e), fL = easuL(f), gL = easuL(g), hL = easuL(h);
  float iL = easuL(i), jL = easuL(j), kL = easuL(k), lL = easuL(l), nL = easuL(n), oL = easuL(o);
  vec2 dir = vec2(0.0);
  float len = 0.0;
  easuSet(dir, len, pp, 0, bL, eL, fL, gL, jL);
  easuSet(dir, len, pp, 1, cL, fL, gL, hL, kL);
  easuSet(dir, len, pp, 2, fL, iL, jL, kL, nL);
  easuSet(dir, len, pp, 3, gL, jL, kL, lL, oL);
  float d2 = dot(dir, dir);
  if (d2 < 1.0 / 32768.0) { dir = vec2(1.0, 0.0); } else { dir /= sqrt(d2); }
  len = len * 0.5;
  len *= len;
  float stretch = dot(dir, dir) / max(abs(dir.x), abs(dir.y));
  vec2 len2 = vec2(1.0 + (stretch - 1.0) * len, 1.0 - 0.5 * len);
  float lob = 0.5 + (0.21 - 0.5) * len;
  float clp = 1.0 / lob;
  vec3 ac = vec3(0.0);
  float aw = 0.0;
  easuTap(ac, aw, vec2(0.0, -1.0) - pp, dir, len2, lob, clp, b);
  easuTap(ac, aw, vec2(1.0, -1.0) - pp, dir, len2, lob, clp, c);
  easuTap(ac, aw, vec2(-1.0, 1.0) - pp, dir, len2, lob, clp, i);
  easuTap(ac, aw, vec2(0.0, 1.0) - pp, dir, len2, lob, clp, j);
  easuTap(ac, aw, vec2(0.0, 0.0) - pp, dir, len2, lob, clp, f);
  easuTap(ac, aw, vec2(-1.0, 0.0) - pp, dir, len2, lob, clp, e);
  easuTap(ac, aw, vec2(1.0, 1.0) - pp, dir, len2, lob, clp, k);
  easuTap(ac, aw, vec2(2.0, 1.0) - pp, dir, len2, lob, clp, l);
  easuTap(ac, aw, vec2(2.0, 0.0) - pp, dir, len2, lob, clp, h);
  easuTap(ac, aw, vec2(1.0, 0.0) - pp, dir, len2, lob, clp, g);
  easuTap(ac, aw, vec2(1.0, 2.0) - pp, dir, len2, lob, clp, o);
  easuTap(ac, aw, vec2(0.0, 2.0) - pp, dir, len2, lob, clp, n);
  vec3 mn = min(min(f, g), min(j, k));
  vec3 mx = max(max(f, g), max(j, k));
  return clamp(ac / max(aw, 1e-4), mn, mx);
}
