// A rectangle that acts on the pixels already drawn. Reads the composite at the same pixels, so
// everything here is registered to the frame. Edges are the scissor's, hard, except pool.
in vec2 v;
out vec4 o;
uniform sampler2D uBg;
uniform sampler2D uNoise;
uniform vec2 uFrame;
uniform int uMode;
uniform vec3 uC;
uniform int uHasC;
uniform float uA;
uniform float uTh;
uniform float uCell;
uniform vec4 uBox;   // x0,y0,x1,y1 pixels
uniform vec3 uKey;   // recolour: the colour that moves
uniform ivec2 uNoiseAt;

vec3 hsv(vec3 c) {
  vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
  vec4 p = c.g < c.b ? vec4(c.bg, K.wz) : vec4(c.gb, K.xy);
  vec4 q = c.r < p.x ? vec4(p.xyw, c.r) : vec4(c.r, p.yzx);
  float d = q.x - min(q.w, q.y);
  return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + 1e-6)), d / (q.x + 1e-6), q.x);
}
vec3 rgb(vec3 c) {
  vec3 p = abs(fract(c.xxx + vec3(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
  return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

void main() {
  vec2 px = gl_FragCoord.xy;
  vec3 bg = texelFetch(uBg, ivec2(px), 0).rgb;
  vec3 c = bg;
  float a = uA;
  if (uMode == 10) { c = uC; }                               // fill
  else if (uMode == 11) {                                    // pool
    vec2 q = (px - 0.5 * (uBox.xy + uBox.zw)) / (0.5 * (uBox.zw - uBox.xy));
    float g = (exp(-3.2 * dot(q, q)) - exp(-3.2)) / (1.0 - exp(-3.2));
    c = bg * (1.0 - uA * max(g, 0.0));
    a = 1.0;
  }
  else if (uMode == 13) {                                    // recolour: hue near the key moves, the light stays
    vec3 h = hsv(bg), k = hsv(uKey), t = hsv(uC);
    float d = abs(h.x - k.x);
    d = min(d, 1.0 - d);
    float m = (1.0 - smoothstep(uTh * 0.6, uTh, d)) * smoothstep(0.42, 0.62, h.y) * smoothstep(0.04, 0.12, h.z);
    if (k.y < 0.18)      // a white or grey key has no hue: it is found by its light. A white lamp turns amber
      m = (1.0 - smoothstep(uTh * 0.6, uTh, abs(h.z - k.z))) * (1.0 - smoothstep(0.2, 0.45, h.y));
    vec2 e = min(px - uBox.xy, uBox.zw - px);
    m *= smoothstep(0.0, 28.0, min(e.x, e.y) + (uBox.x <= 0.0 && uBox.y <= 0.0 ? 28.0 : 0.0));     // no hard window on a change meant to be invisible
    c = mix(bg, rgb(vec3(t.x, mix(h.y, t.y, 0.75), h.z * mix(1.0, t.z / max(k.z, 0.05), 0.6))), m);
  }
  else if (uMode == 12) {                                    // mosaic
    vec2 cc = (floor(px / uCell) + 0.5) * uCell;
    c = texture(uBg, cc / uFrame).rgb;
  }
  else if (uMode >= 20) {                                    // a re-render of the frame itself
    vec2 e = 1.6 / uFrame;
    vec2 uv = px / uFrame;
    float l = luma(bg);
    if (uMode == 20) {
      float x0 = luma(gauss(uBg, uv, uFrame, 1.1).rgb), x1 = luma(gauss(uBg, uv, uFrame, 2.5).rgb);
      float lines = (1.0 - tanh(max(0.0, (x1 - x0) * 255.0 - 0.8) * 0.42)) * (0.80 + 0.20 * l);
      c = ramp3(lines, vec3(22.0, 22.0, 30.0) / 255.0, vec3(150.0, 146.0, 140.0) / 255.0, vec3(245.0, 241.0, 233.0) / 255.0, 0.6);
    } else if (uMode == 21) {
      float gx = luma(textureLod(uBg, uv + vec2(e.x, 0.0), 0.0).rgb) - luma(textureLod(uBg, uv - vec2(e.x, 0.0), 0.0).rgb);
      float gy = luma(textureLod(uBg, uv + vec2(0.0, e.y), 0.0).rgb) - luma(textureLod(uBg, uv - vec2(0.0, e.y), 0.0).rgb);
      float edge = smoothstep(0.035, 0.11, length(vec2(gx, gy)));
      c = ramp4(edge, vec3(18.0, 54.0, 124.0) / 255.0, vec3(40.0, 86.0, 160.0) / 255.0, vec3(200.0, 222.0, 250.0) / 255.0,
                vec3(246.0, 250.0, 255.0) / 255.0, 0.18, 0.6);
    } else if (uMode == 22) {
      float y = pow(max(1.0 - l, 0.0), 1.35);
      float yb = pow(max(1.0 - luma(gauss(uBg, uv, uFrame, 5.0).rgb), 0.0), 1.35);
      c = ramp4(clamp(y + (y - yb) * 1.6, 0.0, 1.0), vec3(2.0, 6.0, 14.0) / 255.0, vec3(40.0, 120.0, 170.0) / 255.0,
                vec3(150.0, 225.0, 245.0) / 255.0, vec3(240.0, 252.0, 255.0) / 255.0, 0.45, 0.8);
    }
  }
  else {
    c = treat(bg, uMode, uC, uHasC == 1, uTh, px);
    if (uMode == 15) c *= 1.0 + 0.5 * texelFetch(uNoise, (ivec2(px) + uNoiseAt) & ivec2(1023), 0).r * 0.17;
  }
  o = vec4(mix(bg, c, a), 1.0);
}
