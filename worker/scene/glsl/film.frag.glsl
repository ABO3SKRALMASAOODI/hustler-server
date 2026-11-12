// Film layer. Reads the composite, adds what a lens and a stock would. Baked numbers only.
in vec2 v;
out vec4 o;
uniform sampler2D uBg;
uniform sampler2D uNoise;
uniform sampler2D uHi;      // highlights, quarter size, already blurred
uniform vec2 uFrame;
uniform ivec2 uAt;
uniform float uGrain;
uniform float uVig;
uniform float uHal;         // halation
uniform float uBloom;
uniform vec2 uMesh;         // pitch px, amount
uniform vec2 uRaster;       // pitch px, amount

void main() {
  vec2 px = gl_FragCoord.xy;
  vec2 uv = px / uFrame;
  vec3 c = texelFetch(uBg, ivec2(px), 0).rgb;
  if (uHal > 0.0 || uBloom > 0.0) {
    vec3 hi = textureLod(uHi, uv, 0.0).rgb;      // blurred once at quarter size (hiblur), read with one tap
    if (uBloom > 0.0) c = 1.0 - (1.0 - c) * (1.0 - clamp(hi, 0.0, 1.0) * uBloom);
    if (uHal > 0.0) c += vec3(1.0, 0.32, 0.12) * luma(hi) * uHal * (1.0 - luma(c));
  }
  if (uVig > 0.0) {
    float r = length((uv - 0.5) * vec2(0.9, 1.0));
    c *= 1.0 - uVig * smoothstep(0.35, 0.85, r);
  }
  if (uRaster.y > 0.0) {
    float l = 0.5 + 0.5 * cos(px.x * 6.2831853 / uRaster.x);
    c *= 1.0 - uRaster.y * l * l;
  }
  if (uMesh.y > 0.0) {
    mat2 r = mat2(0.7071, -0.7071, 0.7071, 0.7071);
    vec2 g = fract(r * px / uMesh.x) - 0.5;
    float d = 1.0 - smoothstep(0.20, 0.40, length(g));
    c *= 1.0 - uMesh.y * d;
  }
  if (uGrain > 0.0) {
    // grain is the amount design-a meant: the width of a uniform noise, so sd = amount * 0.29
    float l = luma(c);
    float n = texelFetch(uNoise, (ivec2(px) + uAt) & ivec2(1023), 0).r;
    c += n * uGrain * 0.29 * (0.6 + 1.6 * l * (1.0 - l));
  }
  o = vec4(c, 1.0);
}
