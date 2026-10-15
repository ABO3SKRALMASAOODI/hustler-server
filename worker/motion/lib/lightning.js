/* MG.lightning — seeded fractal bolts (Wave 3, 3D).
 *   const segs = MG.lightning.bolt(seed, x0, y0, x1, y1, {gen, jag, branch})
 *   MG.lightning.draw(ctx, segs, {colour, width, glow, alpha})
 * Midpoint displacement with seeded branching: the same seed is the same
 * bolt on every frame (re-seed per strike for a new shape).
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  function bolt(seed, x0, y0, x1, y1, o) {
    o = o || {};
    const r = MG.rand(seed * 2654435761 + 17);
    const gen = Math.max(2, Math.min(9, o.gen || 6));
    const jag = o.jag ?? 0.32, branch = o.branch ?? 0.28;
    let segs = [[x0, y0, x1, y1, 1]];
    for (let g = 0; g < gen; g++) {
      const next = [];
      for (const [ax, ay, bx, by, w] of segs) {
        const mx = (ax + bx) / 2, my = (ay + by) / 2;
        const dx = bx - ax, dy = by - ay, L = Math.hypot(dx, dy) || 1;
        const off = (r() - 0.5) * jag * L;
        const px = mx - dy / L * off, py = my + dx / L * off;
        next.push([ax, ay, px, py, w], [px, py, bx, by, w]);
        if (w > 0.25 && r() < branch * (1 - g / gen)) {
          const ang = (r() - 0.5) * 1.1;
          const c = Math.cos(ang), s = Math.sin(ang);
          const ex = px + (dx * c - dy * s) * 0.55, ey = py + (dx * s + dy * c) * 0.55;
          next.push([px, py, ex, ey, w * 0.55]);
        }
      }
      segs = next;
    }
    return segs;
  }
  function draw(ctx, segs, d) {
    d = d || {};
    const col = d.colour || '200,225,255', w = d.width || 3, a = d.alpha ?? 1;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const passes = [[w * (d.glow || 7), 0.08], [w * 3, 0.22], [w, 1.0]];
    for (const [lw, k] of passes) {
      ctx.strokeStyle = k === 1 ? `rgba(255,255,255,${a.toFixed(3)})` : `rgba(${col},${(a * k).toFixed(3)})`;
      for (const [ax, ay, bx, by, sw] of segs) {
        ctx.lineWidth = Math.max(0.6, lw * sw);
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.stroke();
      }
    }
    ctx.restore();
  }
  MG.lightning = { bolt, draw };
})();
