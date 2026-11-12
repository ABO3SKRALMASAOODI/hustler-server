// Float composite to 8 bits, with one least-significant bit of noise so gradients never band.
in vec2 v;
out vec4 o;
uniform sampler2D uBg;
uniform sampler2D uNoise;
uniform ivec2 uAt;
uniform float uDither;

void main() {
  ivec2 px = ivec2(gl_FragCoord.xy);
  vec3 c = texelFetch(uBg, px, 0).rgb;
  if (uDither > 0.0) {
    float n = texelFetch(uNoise, (px + uAt) & ivec2(1023), 0).r;
    c += n * 0.45 * uDither / 255.0;
  }
  o = vec4(clamp(c, 0.0, 1.0), 1.0);
}
