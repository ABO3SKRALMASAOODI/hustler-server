/* MG.paths — path text helpers (Wave 2 track 2B). The typesetter solves
 * glyph positions on a path in Python (exact parity); these helpers move
 * them over time: orbit an arc (words circling a head or a disc), ride a
 * wave, or walk a polyline (a matte contour). Pure functions of t.
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  const ell = (cx, cy, rx, ry, a) => ({ x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a), rot: a * 180 / Math.PI + 90 });
  MG.paths = {
    ellipse: ell,
    /** glyph g of an arc run, advanced by an orbit of w rad/s since t0 */
    orbit(path, g, t, w, t0) {
      if (!path || path.cx == null) return { x: g[1], y: g[2], rot: g[3] };
      const a0 = Math.atan2((g[2] - path.cy) / path.ry, (g[1] - path.cx) / path.rx);
      return ell(path.cx, path.cy, path.rx, path.ry, a0 + w * Math.max(0, t - (t0 || 0)));
    },
    /** y offset of a wave at x (px), amplitude amp, wavelength len, phase */
    wave: (x, amp, len, ph) => amp * Math.sin((x / len) * 2 * Math.PI + (ph || 0)),
    /** point and tangent angle at arc length s along polyline pts [[x,y],..] */
    along(pts, s) {
      let acc = 0;
      for (let i = 1; i < pts.length; i++) {
        const dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1];
        const L = Math.hypot(dx, dy);
        if (acc + L >= s || i === pts.length - 1) {
          const u = L > 0 ? Math.min(1, Math.max(0, (s - acc) / L)) : 0;
          return { x: pts[i - 1][0] + dx * u, y: pts[i - 1][1] + dy * u, rot: Math.atan2(dy, dx) * 180 / Math.PI };
        }
        acc += L;
      }
      return { x: pts[0][0], y: pts[0][1], rot: 0 };
    },
  };
})();
