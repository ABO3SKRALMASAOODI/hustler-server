// Shot and card: one picture or plate through crop, treat and grade, inside a rounded rect.
in vec2 v;
out vec4 o;
uniform sampler2D uNoise;
uniform vec2 uFrame;
uniform int uPlate;         // 0 picture, 1 lit paper, 2 static
uniform vec3 uC0;
uniform vec3 uC1;
uniform vec2 uLight;
uniform float uPlateGrain;
uniform ivec2 uNoiseAt;
uniform vec3 uStatic;       // cell px, scroll px, seed
// card geometry, pixels
uniform vec2 uCentre;
uniform vec2 uHalf;
uniform float uScale;
uniform vec2 uRot;          // cos, sin
uniform float uRadius;
uniform float uBorder;
uniform vec3 uBorderC;
uniform float uOp;

// The shutter. A picture that moves is drawn as a camera would record it: uTaps samples of the pose
// between two ends of the exposure (centre, scale and crop each go from A, the uniforms above, to B),
// averaged with their coverage. Every animated number of a shot or a card blurs through this one
// path: a pan, a zoom, a card flying in, and the edge of a card as it passes. Each pixel starts its
// samples at its own offset (the film's noise tile), so 8 samples read as a smear and not as 8 copies.
uniform int uTaps;          // 0: one sample, the picture as it always was
uniform vec2 uCentreB;
uniform float uScaleB;
uniform vec4 uCropB;

float sdRound(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

vec4 shutter(vec2 px) {
  float jit = clamp(texelFetch(uNoise, (ivec2(px) + uNoiseAt) & ivec2(1023), 0).r * 0.29, -0.5, 0.5);
  vec4 acc = vec4(0.0);
  for (int i = 0; i < uTaps; i++) {
    float t = (float(i) + 0.5 + jit) / float(uTaps);
    vec2 ce = mix(uCentre, uCentreB, t);
    float sc = mix(uScale, uScaleB, t);
    vec4 cr = mix(uCrop, uCropB, t);
    vec2 d = px - ce;
    vec2 q = vec2(uRot.x * d.x + uRot.y * d.y, -uRot.y * d.x + uRot.x * d.y) / sc;
    float cov = clamp(0.5 - sdRound(q, uHalf, uRadius) * sc, 0.0, 1.0);
    if (cov <= 0.0) continue;
    vec2 tv = q / (2.0 * uHalf) + 0.5;
    float tpp = (cr.z - cr.x) * uTexSize.x / (2.0 * uHalf.x * sc);
    vec4 s = textureLod(uTex, mix(cr.xy, cr.zw, tv), max(log2(max(tpp, 1.0)), 0.0));
    float ta = uAlpha == 1 ? s.a : 1.0;
    acc += vec4(s.rgb * cov, cov * ta);      // a picture with alpha is premultiplied already
  }
  acc /= float(uTaps);
  if (acc.a <= 0.0) discard;
  vec3 c = clamp(acc.rgb / max(acc.a, 1e-4), 0.0, 1.0);
  if (uTreat != 0 && uTreat < 20) c = treat(c, uTreat, uTreatC, uTreatHasC == 1, uTreatTh, px);
  if (uLook == 1) {          // the window, where the middle of the exposure puts this pixel
    vec2 dm = px - mix(uCentre, uCentreB, 0.5);
    vec2 qm = vec2(uRot.x * dm.x + uRot.y * dm.y, -uRot.y * dm.x + uRot.x * dm.y) / mix(uScale, uScaleB, 0.5);
    vec4 crm = mix(uCrop, uCropB, 0.5);
    gWin = window_at(mix(crm.xy, crm.zw, qm / (2.0 * uHalf) + 0.5));
  }
  c = grade(c);
  float a = acc.a * uOp;
  return vec4(c * a, a);
}

void main() {
  vec2 px = gl_FragCoord.xy;
  if (uTaps > 0) { o = shutter(px); return; }
  vec2 d = px - uCentre;
  vec2 q = vec2(uRot.x * d.x + uRot.y * d.y, -uRot.y * d.x + uRot.x * d.y) / uScale;
  float sd = sdRound(q, uHalf, uRadius);
  float cov = clamp(0.5 - sd * uScale, 0.0, 1.0);
  if (cov <= 0.0) discard;
  vec2 tv = q / (2.0 * uHalf) + 0.5;
  vec3 c;
  float ta = 1.0;
  if (uPlate == 1) {
    float r = length((tv - uLight) * vec2(uHalf.x / max(uHalf.x, uHalf.y), uHalf.y / max(uHalf.x, uHalf.y)) * 2.0);
    c = mix(uC0, uC1, smoothstep(0.0, 1.15, r));
    if (any(notEqual(uC0, uC1))) c *= 1.0 + 0.10 * exp(-r * r * 6.0);      // the pool of light itself. A plate of one colour stays one colour
    if (uPlateGrain > 0.0) {
      float n = texelFetch(uNoise, (ivec2(px) + uNoiseAt) & ivec2(1023), 0).r;
      c += n * uPlateGrain * 0.29;
    }
  } else if (uPlate == 2) {
    c = mix(uC0, uC1, static_bit(px, uStatic.x, uStatic.y, uStatic.z));
  } else {
    vec2 uv = mix(uCrop.xy, uCrop.zw, warp(tv));
    float texel_per_px = (uCrop.z - uCrop.x) * uTexSize.x / (2.0 * uHalf.x * uScale);
    if (uLook == 1) gWin = window_at(uv);
    c = sample_pic(uv, texel_per_px);
    if (uAlpha == 1) {
      ta = textureLod(uTex, uv, max(log2(max(texel_per_px, 1.0)), 0.0)).a;
      c = clamp(c / max(ta, 1e-3), 0.0, 1.0);
    }
    if (uTreat >= 20) c = rerender(uv, max(texel_per_px, 0.25), c, px, uFrame);
  }
  if (uTreat != 0 && uTreat < 20) {
    c = treat(c, uTreat, uTreatC, uTreatHasC == 1, uTreatTh, px);
    if (uTreat == 15) {        // night vision: sensor noise and a tube's vignette
      c *= 1.0 + 0.5 * texelFetch(uNoise, (ivec2(px) + uNoiseAt) & ivec2(1023), 0).r * 0.17;
      c *= 1.0 - 0.55 * smoothstep(0.25, 0.75, length(tv - 0.5));
    }
  }
  c = grade(c);
  if (uBorder > 0.0) {
    float inner = sdRound(q, uHalf - uBorder, max(uRadius - uBorder, 0.0));
    c = mix(c, uBorderC, clamp(0.5 + inner * uScale, 0.0, 1.0));
  }
  float a = cov * uOp * ta;
  o = vec4(c * a, a);
}
