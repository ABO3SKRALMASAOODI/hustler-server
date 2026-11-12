// One full-frame or rect quad. p is 0..1. uRect is x,y,w,h in pixels, top-left origin.
// Row 0 of every target is the top of the picture, so nothing in the engine flips.
in vec2 p;
uniform vec4 uRect;
uniform vec2 uFrame;
out vec2 v;
void main() {
  v = p;
  vec2 q = (uRect.xy + p * uRect.zw) / uFrame;
  gl_Position = vec4(q * 2.0 - 1.0, 0.0, 1.0);
}
