// One sprite of type from the atlas. R is coverage at twice the target size, G the shadow copy,
// B the glow copy, A a distance field. The sprite is minified 2:1 through the mip chain and sits on
// the atlas's 8-texel grid, so a static word lands exactly on the pixel grid.
in vec2 v;
out vec4 o;
uniform sampler2D uW;
uniform sampler2D uBg;
uniform vec2 uAtlas;       // page size, texels
uniform vec4 uSpr;         // the sprite's rect in the page: x y w h, texels
uniform float uLod;
uniform vec3 uInk;
uniform float uOp;
uniform float uShadow;
uniform float uGlow;
uniform float uBlur;       // sigma, sprite texels
uniform int uPre;          // 1: this sprite at this blur was drawn once into uPreTex (wblur.frag); read it there
uniform sampler2D uPreTex;
uniform int uBlend;        // 0 normal, 1 difference, 2 multiply
uniform int uFade;         // 1: a reflection, strongest at the baseline, gone at the top of the sprite
uniform int uFill;         // 1: the letters hold a picture, fitted to their block. 2: static. 3: a window onto a picture
                           //    laid over the whole frame, sampled and graded as a shot is, so it registers to that shot
uniform sampler2D uFillTex;
uniform vec4 uFillCrop;
uniform vec2 uFrame;
uniform vec2 uFillSize;    // 3: the picture's size, texels
uniform vec3 uFG0;         // 3: lift, gamma, gain
uniform vec3 uFG1;         // 3: sat, contrast, temp
uniform float uFTint;

vec3 window(vec2 px) {
  vec2 uv = mix(uFillCrop.xy, uFillCrop.zw, px / uFrame);
  float tpp = (uFillCrop.z - uFillCrop.x) * uFillSize.x / uFrame.x;
  vec3 c = tpp > 1.05 ? textureLod(uFillTex, uv, log2(tpp)).rgb : catrom(uFillTex, uv, uFillSize);
  c = pow(max(c + uFG0.x, vec3(0.0)), vec3(1.0 / uFG0.y)) * uFG0.z;
  c = mix(vec3(luma(c)), c, uFG1.x);
  c = (c - 0.5) * uFG1.y + 0.5;
  c.r += uFG1.z; c.b -= uFG1.z; c.g += uFTint;
  return c;
}
uniform vec3 uStatic;      // cell px, scroll px, seed
uniform vec3 uFillC0;
uniform vec3 uFillC1;
uniform int uHard;         // 1: no soft edge at all. A letter of static must not show its outline in a still
uniform vec4 uFx;          // distance-field effects: stroke width px, weight shift px, field reach in texels, texels per px
uniform vec3 uStrokeC;

void main() {
  vec2 st = (uSpr.xy + v * uSpr.zw) / uAtlas;
  vec4 t;
  if (uPre == 1) {
    t = textureLod(uPreTex, v, 0.0);
  } else if (uBlur > 0.0) {
    t = gauss(uW, st, uAtlas, uBlur);
  } else {
    float l0 = floor(uLod);
    t = mix(textureLod(uW, st, l0), textureLod(uW, st, l0 + 1.0), uLod - l0);
  }
  float cov = t.r;
  float stroke = 0.0;
  if (uFx.x > 0.0 || uFx.y != 0.0) {
    // signed distance to the outline in output pixels, positive inside
    float d = (textureLod(uW, st, 0.0).a - 0.5) * 2.0 * uFx.z / uFx.w;
    if (uFx.y != 0.0) cov = clamp(d + uFx.y + 0.5, 0.0, 1.0);
    if (uFx.x > 0.0) stroke = clamp(d + uFx.x + 0.5, 0.0, 1.0);
  }
  if (uHard == 1) cov = step(0.5, cov);
  if (uFade == 1) cov *= smoothstep(0.1, 1.0, v.y) * smoothstep(0.1, 1.0, v.y);
  if (uBlend == 0) {
    float sh = uShadow * t.g * uOp;
    float gl = uGlow * t.b * uOp;
    vec3 ink = uInk;
    if (uFill == 1) ink = texture(uFillTex, mix(uFillCrop.xy, uFillCrop.zw, v)).rgb;
    if (uFill == 2) ink = mix(uFillC0, uFillC1, static_bit(gl_FragCoord.xy, uStatic.x, uStatic.y, uStatic.z));
    if (uFill == 3) ink = window(gl_FragCoord.xy);
    vec3 rgb = ink * clamp(cov * uOp + gl * (1.0 - cov * uOp), 0.0, 1.0);
    float a = 1.0 - (1.0 - cov * uOp) * (1.0 - gl) * (1.0 - sh);
    if (stroke > 0.0) {      // the stroke sits under the letter and over its shadow
      float sa = stroke * uOp * (1.0 - cov * uOp);
      rgb += uStrokeC * sa;
      a = 1.0 - (1.0 - a) * (1.0 - stroke * uOp);
    }
    o = vec4(rgb, a);
  } else {
    vec3 bg = texelFetch(uBg, ivec2(gl_FragCoord.xy), 0).rgb;
    vec3 c = uBlend == 1 ? abs(bg - uInk) : bg * uInk;
    o = vec4(mix(bg, c, cov * uOp), 1.0);
  }
}
