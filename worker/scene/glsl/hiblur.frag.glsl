// The halation and bloom field: the highlights blurred once, a tight glow and a wide one, so the
// film pass reads it with one bilinear tap instead of 64 taps on every full-size pixel.
//   step 0  the wide glow (sigma 48 frame pixels), drawn at a sixteenth size, where it is sigma 3
//   step 1  the tight glow (sigma 12 frame pixels) at an eighth size, where it is sigma 1.5, plus
//           the wide one read back with one tap
// A blur loses nothing on a grid that samples it this finely, and each pass costs its own size.
in vec2 v;
out vec4 o;
uniform sampler2D uHi;
uniform sampler2D uWide;
uniform int uStep;
void main() {
  vec3 g = gauss(uHi, v, vec2(textureSize(uHi, 0)), uStep == 1 ? 1.5 : 3.0).rgb;
  if (uStep == 1) g = 0.6 * g + 0.4 * textureLod(uWide, v, 0.0).rgb;
  o = vec4(g, 1.0);
}
