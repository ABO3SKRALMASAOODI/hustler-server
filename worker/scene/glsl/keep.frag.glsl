// The person again, over what was drawn behind him: the base picture exactly as it was drawn,
// through his matte. One fetch and one tap, where drawing the shot a second time cost a full picture pass.
// A pass of shot.keep can draw him as something else: an offset clone, a flat silhouette, a halo round him,
// an outline sticker, a two-colour poster. Each is one premultiplied layer, blended as cards are.
in vec2 v;
out vec4 o;
uniform sampler2D uBase;
uniform sampler2D uMatte;   // the person, on the take's geometry
uniform vec4 uCrop;
uniform vec2 uFrame;
uniform int uKMode;         // 0 himself, 1 fill, 2 glow, 3 outline, 4 duo
uniform vec2 uKOff;         // frame px: where this pass stands against him (whole pixels)
uniform float uKA;          // the pass's opacity
uniform vec3 uKC;
uniform vec3 uKC2;
uniform float uKW;          // glow and outline: their reach, frame px

float matte_at(vec2 px) { return texture(uMatte, mix(uCrop.xy, uCrop.zw, px / uFrame)).r; }

void main() {
  vec2 px = gl_FragCoord.xy;
  vec2 s = px - uKOff;
  if (uKMode == 0) {
    float a = texture(uMatte, mix(uCrop.xy, uCrop.zw, s / uFrame)).r;
    o = vec4(texelFetch(uBase, clamp(ivec2(s), ivec2(0), ivec2(uFrame) - 1), 0).rgb * a, a) * uKA;
    return;
  }
  float m = matte_at(s);
  if (uKMode == 1) { o = vec4(uKC * m, m) * uKA; return; }
  if (uKMode == 4) {
    float l = luma(texelFetch(uBase, clamp(ivec2(s), ivec2(0), ivec2(uFrame) - 1), 0).rgb);
    o = vec4(mix(uKC, uKC2, smoothstep(0.10, 0.40, l)) * m, m) * uKA;
    return;
  }
  // glow and outline read the matte round the pixel: two rings of 16 taps
  float soft = 0.0, dil = 0.0;
  for (int i = 0; i < 16; i++) {
    float ang = float(i) * 0.39269908 + 0.19634954;
    vec2 e = vec2(cos(ang), sin(ang));
    float a1 = matte_at(s + e * uKW), a2 = matte_at(s + e * uKW * 0.5);
    soft += a1 + a2;
    dil = max(dil, max(a1, a2));
  }
  soft /= 32.0;
  float a = uKMode == 2 ? clamp(soft * 1.8 - 0.35 * m, 0.0, 1.0) : clamp(max(dil, m) * 1.15 - 0.15, 0.0, 1.0);
  o = vec4(uKC * a, a) * uKA;
}
