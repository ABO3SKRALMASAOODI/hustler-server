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

float sdRound(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

void main() {
  vec2 px = gl_FragCoord.xy;
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
