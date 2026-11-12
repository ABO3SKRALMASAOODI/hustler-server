// Soft shadow of a rounded rect: the true gaussian convolution in x, integrated over y (E. Wallace).
in vec2 v;
out vec4 o;
uniform vec2 uCentre;
uniform vec2 uHalf;
uniform float uRadius;
uniform float uSigma;
uniform float uA;
uniform vec2 uRot;

float gauss(float x, float s) { return exp(-(x * x) / (2.0 * s * s)) / (2.5066283 * s); }
vec2 erf2(vec2 x) {
  vec2 s = sign(x), a = abs(x);
  x = 1.0 + (0.278393 + (0.230389 + 0.078108 * (a * a)) * a) * a;
  x *= x;
  return s - s / (x * x);
}
float rowX(float x, float y, float s, float corner, vec2 half_) {
  float dc = min(half_.y - corner - abs(y), 0.0);
  float cur = half_.x - corner + sqrt(max(0.0, corner * corner - dc * dc));
  vec2 i = 0.5 + 0.5 * erf2((x + vec2(-cur, cur)) * (0.7071068 / s));
  return i.y - i.x;
}
void main() {
  vec2 d = gl_FragCoord.xy - uCentre;
  vec2 p = vec2(uRot.x * d.x + uRot.y * d.y, -uRot.y * d.x + uRot.x * d.y);
  float low = p.y - uHalf.y, high = p.y + uHalf.y;
  float start = clamp(-3.0 * uSigma, low, high), end = clamp(3.0 * uSigma, low, high);
  float step_ = (end - start) / 4.0, y = start + step_ * 0.5, val = 0.0;
  for (int i = 0; i < 4; i++) {
    val += rowX(p.x, p.y - y, uSigma, uRadius, uHalf) * gauss(y, uSigma) * step_;
    y += step_;
  }
  float a = clamp(val * uA, 0.0, 1.0);
  o = vec4(0.0, 0.0, 0.0, a);
}
