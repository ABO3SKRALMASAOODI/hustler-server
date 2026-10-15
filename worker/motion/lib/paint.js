/* MG.paint — stroke-only paint mode (track 1C, the ghost pass).
 * A composition rendered with params._paint = {mode: "stroke", width: px}
 * draws only the OUTLINES of its type: every glyph keeps its own colour on
 * a stroke of `width` design px, its fill transparent; shadows, glows,
 * plates, backgrounds, images and canvases are not drawn. The compositor
 * keeps that stroke only where the subject covers the letters of a
 * behind-subject graphic, so hidden letters still read. Pure CSS applied
 * once the page has loaded: the page's own choreography (positions,
 * scales, opacity, blur) is untouched, so the outlines sit exactly on the
 * letters of the normal render. Without _paint nothing changes.
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  const opts = () => (MG.params || {})._paint || null;
  const css = w => [
    '*,*::before,*::after{',
    '-webkit-text-fill-color:transparent!important;',
    '-webkit-text-stroke:' + w + 'px currentColor!important;',
    'text-shadow:none!important;box-shadow:none!important;',
    'background:transparent!important;border-color:transparent!important;',
    'outline:none!important;text-decoration-color:transparent!important}',
    'img,canvas,video,svg image{visibility:hidden!important}',
    'svg text,svg tspan{fill:transparent!important;stroke:currentColor!important;',
    'stroke-width:' + w + 'px!important}',
    'svg path,svg rect,svg circle,svg line,svg polygon,svg ellipse{',
    'fill:transparent!important;stroke:transparent!important}'].join('');
  MG.paint = {
    mode: () => (opts() || {}).mode || null,
    width: () => Math.max(0.5, +((opts() || {}).width) || 2.5),
    apply: () => {
      if (MG.paint.mode() !== 'stroke' || MG.paint._done) return;
      const st = document.createElement('style');
      st.id = 'mg-paint';
      st.textContent = css(MG.paint.width());
      (document.head || document.documentElement).appendChild(st);
      MG.paint._done = true;
    },
    _done: false,
  };
  // MG.params arrives after this file runs (the next script): apply once
  // the document is parsed — before the renderer seeks any frame, and
  // without registering a frame function (static-frame reuse stays on)
  window.addEventListener('DOMContentLoaded', () => MG.paint.apply());
})();
