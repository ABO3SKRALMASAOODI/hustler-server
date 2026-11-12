// The highlights alone, at a quarter size. Threshold first, then blur, so a small lamp still blooms.
in vec2 v;
out vec4 o;
uniform sampler2D uBg;
void main() {
  vec3 c = textureLod(uBg, v, 2.0).rgb;
  o = vec4(max(c - 0.62, vec3(0.0)) / 0.38, 1.0);
}
