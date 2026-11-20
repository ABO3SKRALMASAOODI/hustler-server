// One full-frame or rect quad. p is 0..1. uRect is x,y,w,h in pixels, top-left origin.
// Row 0 of every target is the top of the picture, so nothing in the engine flips.
in vec2 p;
uniform vec4 uRect;
uniform vec2 uFrame;
uniform int uHomOn;         // 1: the quad is a plane seen in perspective (a tilted word): uHomV takes p to frame pixels
uniform mat3 uHomV;
out vec2 v;
void main() {
  v = p;
  if (uHomOn == 1) {
    vec3 h = uHomV * vec3(p, 1.0);
    gl_Position = vec4(2.0 * h.x / uFrame.x - h.z, 2.0 * h.y / uFrame.y - h.z, 0.0, h.z);
    return;
  }
  vec2 q = (uRect.xy + p * uRect.zw) / uFrame;
  gl_Position = vec4(q * 2.0 - 1.0, 0.0, 1.0);
}
