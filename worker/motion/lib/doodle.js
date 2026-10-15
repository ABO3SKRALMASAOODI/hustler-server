/* MG.doodle — hand-drawn marks with boil (Wave 3, 3D).
 *   const pts = MG.doodle.path(kind, box, seed, boilFrame)
 *   MG.doodle.stroke(ctx, pts, {colour, width, progress})
 * kind: arrow | underline | circle | scribble | burst | star | check | cross
 * | bracket | spiral. box = [x0, y0, x1, y1] (design px). Boil: the shape
 * re-jitters every few frames (pass floor(t * fps / 3) as boilFrame), the
 * way a hand-animated line does; the same frame is always the same line.
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  function jitter(pts, seed, amp) {
    const r = MG.rand(seed);
    return pts.map(([x, y]) => [x + (r() - 0.5) * amp, y + (r() - 0.5) * amp]);
  }
  function path(kind, box, seed, boil) {
    const [x0, y0, x1, y1] = box, w = x1 - x0, h = y1 - y0;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, P = [];
    const amp = Math.max(1.2, Math.min(w, h) * 0.025);
    const s = (seed || 1) * 131 + (boil || 0) * 977;
    if (kind === 'underline') {
      for (let i = 0; i <= 24; i++) P.push([x0 + w * i / 24, y1 - h * 0.15 * Math.sin(i / 24 * Math.PI) * 0.4]);
    } else if (kind === 'circle' || kind === 'spiral') {
      const turns = kind === 'spiral' ? 2.6 : 1.12;
      for (let i = 0; i <= 64; i++) {
        const a = -Math.PI * 0.6 + i / 64 * Math.PI * 2 * turns;
        const k = kind === 'spiral' ? (0.25 + 0.75 * i / 64) : (1 + 0.06 * Math.sin(i / 7));
        P.push([cx + Math.cos(a) * w / 2 * k, cy + Math.sin(a) * h / 2 * k]);
      }
    } else if (kind === 'arrow') {
      // tail at the box's start corner, head at (x1, y1); a gentle curve
      for (let i = 0; i <= 24; i++) {
        const u = i / 24;
        P.push([x0 + w * u, y0 + h * u - Math.sin(u * Math.PI) * h * 0.25]);
      }
      const [ax, ay] = P[P.length - 1], [bx, by] = P[P.length - 4];
      const ang = Math.atan2(ay - by, ax - bx), L = Math.hypot(w, h) * 0.12;
      P.push([NaN, NaN], [ax - L * Math.cos(ang - 0.5), ay - L * Math.sin(ang - 0.5)], [ax, ay],
             [ax - L * Math.cos(ang + 0.5), ay - L * Math.sin(ang + 0.5)]);
    } else if (kind === 'scribble') {
      const r = MG.rand(seed * 7 + 3);
      for (let i = 0; i <= 18; i++) P.push([x0 + w * (i / 18), cy + (i % 2 ? -1 : 1) * h * (0.3 + 0.2 * r())]);
    } else if (kind === 'burst' || kind === 'star') {
      const n = kind === 'star' ? 5 : 12;
      for (let i = 0; i < n; i++) {
        const a = i / n * Math.PI * 2;
        if (kind === 'burst') {
          P.push([NaN, NaN], [cx + Math.cos(a) * w * 0.28, cy + Math.sin(a) * h * 0.28], [cx + Math.cos(a) * w * 0.5, cy + Math.sin(a) * h * 0.5]);
        } else {
          P.push([cx + Math.cos(a - Math.PI / 2) * w / 2, cy + Math.sin(a - Math.PI / 2) * h / 2]);
          P.push([cx + Math.cos(a - Math.PI / 2 + Math.PI / n) * w / 5, cy + Math.sin(a - Math.PI / 2 + Math.PI / n) * h / 5]);
        }
      }
      if (kind === 'star') P.push(P[0]);
    } else if (kind === 'check') {
      P.push([x0, cy], [x0 + w * 0.35, y1], [x1, y0]);
    } else if (kind === 'cross') {
      P.push([x0, y0], [x1, y1], [NaN, NaN], [x1, y0], [x0, y1]);
    } else if (kind === 'bracket') {
      P.push([x0 + w * 0.2, y0], [x0, y0], [x0, y1], [x0 + w * 0.2, y1], [NaN, NaN], [x1 - w * 0.2, y0], [x1, y0], [x1, y1], [x1 - w * 0.2, y1]);
    }
    return jitter(P, s, amp);
  }
  function stroke(ctx, pts, d) {
    d = d || {};
    const prog = MG.clamp(d.progress ?? 1);
    const runs = [[]];
    for (const p of pts) { if (Number.isNaN(p[0])) runs.push([]); else runs[runs.length - 1].push(p); }
    let total = 0;
    const lens = runs.map(r => { let L = 0; for (let i = 1; i < r.length; i++) L += Math.hypot(r[i][0] - r[i - 1][0], r[i][1] - r[i - 1][1]); total += L; return L; });
    let budget = total * prog;
    ctx.save();
    ctx.strokeStyle = d.colour || '#ffffff';
    ctx.lineWidth = d.width || 6;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (d.shadow) { ctx.shadowColor = d.shadow; ctx.shadowBlur = (d.width || 6) * 2; }
    runs.forEach((r, k) => {
      if (budget <= 0 || r.length < 2) return;
      ctx.beginPath();
      ctx.moveTo(r[0][0], r[0][1]);
      let used = 0;
      for (let i = 1; i < r.length; i++) {
        const seg = Math.hypot(r[i][0] - r[i - 1][0], r[i][1] - r[i - 1][1]);
        if (used + seg > budget) {
          const u = (budget - used) / (seg || 1);
          ctx.lineTo(r[i - 1][0] + (r[i][0] - r[i - 1][0]) * u, r[i - 1][1] + (r[i][1] - r[i - 1][1]) * u);
          used = budget;
          break;
        }
        ctx.lineTo(r[i][0], r[i][1]);
        used += seg;
      }
      ctx.stroke();
      budget -= lens[k];
    });
    ctx.restore();
  }
  MG.doodle = { path, stroke };
})();
