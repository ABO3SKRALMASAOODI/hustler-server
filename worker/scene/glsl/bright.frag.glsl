// The highlights of the composite, for the lens field, by box averages (bilinear taps on texel
// corners, so no mip chain is built):
//   uStep 0  composite to an eighth size. Each pixel here is four cells of the quarter-size grid:
//            each cell is a 4x4 box of the composite with the cut taken (threshold first, then
//            blur, so a small lamp still blooms), and the four are averaged.
//   uStep 1  that field at half its size again: one tap, a 2x2 box.
in vec2 v;
out vec4 o;
uniform sampler2D uBg;
uniform float uCut;
uniform int uStep;
void main() {
  vec2 e = 1.0 / vec2(textureSize(uBg, 0));
  if (uStep == 1) {
    o = vec4(textureLod(uBg, v, 0.0).rgb, 1.0);
    return;
  }
  vec3 a = vec3(0.0);
  for (int j = 0; j < 2; j++) {
    for (int i = 0; i < 2; i++) {
      vec2 q = v + (vec2(float(i), float(j)) * 4.0 - 2.0) * e;
      vec3 c = 0.25 * (textureLod(uBg, q + vec2(-e.x, -e.y), 0.0).rgb + textureLod(uBg, q + vec2(e.x, -e.y), 0.0).rgb
                     + textureLod(uBg, q + vec2(-e.x, e.y), 0.0).rgb + textureLod(uBg, q + vec2(e.x, e.y), 0.0).rgb);
      a += max(c - uCut, vec3(0.0)) / (1.0 - uCut);
    }
  }
  o = vec4(0.25 * a, 1.0);
}
