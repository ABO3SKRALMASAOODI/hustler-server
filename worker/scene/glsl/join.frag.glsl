// Junction: two pictures into one. A is what is already drawn; B is this item's picture, laid over
// the whole frame. A mask made from numbers decides, pixel by pixel, which one shows.
//   1 seam   B to the left of a leaning line that sits at x (the caret of a sentence being read)
//   2 weave  vertical strips of A and horizontal strips of B, over and under
//   3 glyph  B inside a glyph that grows from its own place (a period that swallows the frame)
in vec2 v;
out vec4 o;
uniform sampler2D uBg;
uniform sampler2D uNoise;
uniform sampler2D uMask;    // distance fields, 0.5 on the outline
uniform vec2 uFrame;
uniform int uJoin;
uniform vec4 uJ;            // seam: x px at mid height, tan(lean), edge px, edge alpha. weave: pitch px, t, falloff, shadow. glyph: noise px
uniform float uDim;         // A's level while the junction runs
uniform float uA;
uniform vec4 uMR;           // glyph: the mask's quad on the frame, x y w h px
uniform vec4 uMUV;          // glyph: its rect in the mask sheet, as 0..1
uniform float uMSpread;     // glyph: frame pixels from the outline to either end of the field
uniform ivec2 uNoiseAt;

void main() {
  vec2 px = gl_FragCoord.xy;
  vec2 tv = px / uFrame;
  vec3 a = texelFetch(uBg, ivec2(px), 0).rgb;
  vec2 uv = mix(uCrop.xy, uCrop.zw, warp(tv));
  float tpp = (uCrop.z - uCrop.x) * uTexSize.x / uFrame.x;
  vec3 b = sample_pic(uv, tpp);
  if (uTreat >= 20) b = rerender(uv, max(tpp, 0.25), b, px, uFrame);
  else if (uTreat != 0) b = treat(b, uTreat, uTreatC, uTreatHasC == 1, uTreatTh, px);
  if (uLook == 1) gWin = window_at(uv);
  b = grade(b);
  vec3 c = a;
  if (uJoin == 1) {
    float d = uJ.x + (px.y - 0.5 * uFrame.y) * uJ.y - px.x;      // positive to the left of the seam
    c = mix(a * uDim, b, clamp(d + 0.5, 0.0, 1.0));
    float e = 1.0 - smoothstep(0.5 * uJ.z, 0.5 * uJ.z + 1.0, abs(d));
    c = mix(c, vec3(1.0), e * uJ.w);
  } else if (uJoin == 2) {
    float t = uJ.y;
    float wa = min(1.0, 2.0 * (1.0 - t)), wb = min(1.0, 2.0 * t);
    vec2 cell = px / uJ.x;
    vec2 id = floor(cell);
    vec2 fr = fract(cell) - 0.5;
    bool inA = abs(fr.x) < 0.5 * wa, inB = abs(fr.y) < 0.5 * wb;
    bool bOver = mod(id.x + id.y, 2.0) < 0.5;
    bool showB = inB && (bOver || !inA);
    if (!inA && !inB) showB = wb >= wa;
    float amt = 1.0 - abs(2.0 * t - 1.0);                         // how woven the frame is: none at either end
    float ua = abs(fr.x) / max(0.5 * wa, 1e-4), ub = abs(fr.y) / max(0.5 * wb, 1e-4);
    vec3 ca = a * (1.0 - uJ.z * amt * ua * ua * ua);
    vec3 cb = b * (1.0 - uJ.z * amt * ub * ub * ub);
    float edge = 1.5 * uFrame.x / 1080.0;
    if (showB) {
      c = cb;
      if (inA == false && wa > 0.0 && !bOver) c *= 1.0 - uJ.w * amt * (1.0 - smoothstep(0.0, edge, (abs(fr.x) - 0.5 * wa) * uJ.x));
    } else {
      c = ca;
      if (inB == false && wb > 0.0 && bOver) c *= 1.0 - uJ.w * amt * (1.0 - smoothstep(0.0, edge, (abs(fr.y) - 0.5 * wb) * uJ.x));
    }
  } else if (uJoin == 3) {
    vec2 q = clamp((px - uMR.xy) / uMR.zw, 0.0, 1.0);
    float d = (texture(uMask, uMUV.xy + q * uMUV.zw).r - 0.5) * 2.0 * uMSpread;
    if (uJ.x > 0.0) d += texelFetch(uNoise, (ivec2(px) + uNoiseAt) & ivec2(1023), 0).r * uJ.x;
    c = mix(a * uDim, b, clamp(d + 0.5, 0.0, 1.0));
  }
  o = vec4(mix(a, c, uA), 1.0);
}
