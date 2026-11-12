// One sprite of the atlas, blurred once. While a word's blur holds from frame to frame, word.frag
// reads this back with one tap instead of taking the gaussian's 32 on every pixel of every frame.
in vec2 v;
out vec4 o;
uniform sampler2D uW;
uniform vec2 uAtlas;       // page size, texels
uniform vec4 uSpr;         // the sprite's rect in the page: x y w h, texels
uniform float uBlur;       // sigma, sprite texels
void main() {
  o = gauss(uW, (uSpr.xy + v * uSpr.zw) / uAtlas, uAtlas, uBlur);
}
