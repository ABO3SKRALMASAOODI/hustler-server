// The halation and bloom field: the highlights blurred once at quarter size, a tight glow and a
// wide one. The film pass reads it with one bilinear tap instead of 64 taps on every full-size pixel.
in vec2 v;
out vec4 o;
uniform sampler2D uHi;
void main() {
  vec2 hs = vec2(textureSize(uHi, 0));
  o = vec4(0.6 * gauss(uHi, v, hs, 3.0).rgb + 0.4 * gauss(uHi, v, hs, 12.0).rgb, 1.0);
}
