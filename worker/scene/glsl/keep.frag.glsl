// The person again, over what was drawn behind him: the base picture exactly as it was drawn,
// through his matte. One fetch and one tap, where drawing the shot a second time cost a full picture pass.
in vec2 v;
out vec4 o;
uniform sampler2D uBase;
uniform sampler2D uMatte;   // the person, on the take's geometry
uniform vec4 uCrop;
uniform vec2 uFrame;
void main() {
  vec2 px = gl_FragCoord.xy;
  float a = texture(uMatte, mix(uCrop.xy, uCrop.zw, px / uFrame)).r;
  o = vec4(texelFetch(uBase, ivec2(px), 0).rgb * a, a);
}
