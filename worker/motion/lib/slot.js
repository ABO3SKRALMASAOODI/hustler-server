/* MG.slot — video slots (lib stem "slot": a page naming MG.slot loads it) (Wave 3, 3E; devices/INTERFACES_W3.md §6).
 * A page declares, every frame, the rects where REAL video goes:
 *   MG.slot(id, x, y, w, h, {r, z, opacity, fit, crop})     design px
 * ``crop`` [x0, y0, x1, y1] (fractions of the source) shows part of it; an
 * id "screen#ui" draws a crop of the "screen" source (a UI breakout).
 * The engine records them per frame (motion_engine: <clip>.slots.json) and
 * slots.py composites the slot sources (params) into them, rounded and
 * clipped, UNDER the page's own pixels. A page with slots calls
 * MG.always() so every frame is seeked (and recorded).
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  let cur = [];
  const seek = window.__mgSeek;
  if (typeof seek === 'function') {
    window.__mgSeek = function (t) { cur = []; return seek(t); };
  }
  MG.slot = (id, x, y, w, h, o) => {
    o = o || {};
    if (!(w > 0.5 && h > 0.5)) return;
    cur.push([String(id), +x.toFixed(2), +y.toFixed(2), +w.toFixed(2), +h.toFixed(2),
      +(o.r || 0).toFixed(2), o.z || 0, +(o.opacity ?? 1).toFixed(3), o.fit || 'cover']
      .concat(o.crop ? [o.crop.map(v => +(+v).toFixed(4))] : []));
  };
  MG._slots = { current: () => cur };
})();
