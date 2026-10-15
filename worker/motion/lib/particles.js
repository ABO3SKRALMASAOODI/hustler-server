/* MG.particles — deterministic particle fields (Wave 3, 3D).
 * A particle's state is a closed-form function of its seed and of t (no
 * simulation, no clocks): position = origin + v*a + g*a*a/2 + curl noise,
 * where a = age. So any frame renders alone, identically, in any order.
 *   const F = MG.particles.field({seed, count, emit: i => ({x, y, vx, vy,
 *     t0, life, size, hue}), gravity: [gx, gy], drag, swirl});
 *   F.draw(ctx, t, {glow, colour, fade})
 * Emitters: MG.particles.edge(samples, t, seed, i) picks a subject-edge
 * point (samples = MG.params._edge: [{t, p: [[x, y], ...]}]).
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  const TAU = Math.PI * 2;
  function field(o) {
    const seed = o.seed || 1, n = Math.max(1, Math.min(4000, o.count || 200));
    const g = o.gravity || [0, 0], drag = o.drag || 0, swirl = o.swirl || 0;
    const parts = [];
    for (let i = 0; i < n; i++) {
      const r = MG.rand(seed * 7919 + i * 104729);
      const p = o.emit(i, r) || null;
      if (p) { p.k = i; p.r1 = r(); p.r2 = r(); parts.push(p); }
    }
    function state(p, t) {
      const a = t - p.t0;
      if (a < 0 || a > p.life) return null;
      const k = drag > 0 ? (1 - Math.exp(-drag * a)) / drag : a;
      let x = p.x + (p.vx || 0) * k + 0.5 * g[0] * a * a;
      let y = p.y + (p.vy || 0) * k + 0.5 * g[1] * a * a;
      if (swirl) {
        x += swirl * MG.noise(p.r1 * 50 + a * 0.9) * a;
        y += swirl * MG.noise(p.r2 * 50 + a * 0.9 + 7) * a;
      }
      const life = a / p.life;
      return { x, y, life, a };
    }
    function draw(ctx, t, d) {
      d = d || {};
      ctx.save();
      ctx.globalCompositeOperation = d.blend || 'lighter';
      for (const p of parts) {
        const s = state(p, t);
        if (!s) continue;
        const fadeIn = Math.min(1, s.a / Math.max(0.001, d.fadeIn ?? 0.08));
        const fadeOut = Math.pow(1 - s.life, d.fadePow ?? 1.4);
        const alpha = (p.alpha ?? 1) * fadeIn * fadeOut;
        if (alpha <= 0.003) continue;
        const size = (p.size || 3) * (d.shrink ? (1 - 0.6 * s.life) : 1);
        const col = (d.colour && d.colour(p, s)) || p.col || '255,200,120';
        if (d.streak && (p.vx || p.vy)) {
          const sp = Math.hypot(p.vx || 0, p.vy || 0) || 1;
          const L = Math.min(d.streak, sp * 0.05);
          ctx.strokeStyle = `rgba(${col},${alpha.toFixed(3)})`;
          ctx.lineWidth = size;
          ctx.lineCap = 'round';
          ctx.beginPath();
          ctx.moveTo(s.x, s.y);
          ctx.lineTo(s.x - (p.vx / sp) * L, s.y - (p.vy / sp) * L);
          ctx.stroke();
          continue;
        }
        if (d.glow) {
          const gr = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, size * d.glow);
          gr.addColorStop(0, `rgba(${col},${(alpha * 0.55).toFixed(3)})`);
          gr.addColorStop(1, `rgba(${col},0)`);
          ctx.fillStyle = gr;
          ctx.fillRect(s.x - size * d.glow, s.y - size * d.glow, size * d.glow * 2, size * d.glow * 2);
        }
        ctx.fillStyle = `rgba(${col},${alpha.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, Math.max(0.4, size / 2), 0, TAU);
        ctx.fill();
      }
      ctx.restore();
    }
    return { parts, state, draw };
  }
  // a point on the subject's edge at time t (nearest sample), chosen by r()
  function edge(samples, t, r) {
    if (!samples || !samples.length) return null;
    let best = samples[0];
    for (const s of samples) if (Math.abs(s.t - t) < Math.abs(best.t - t)) best = s;
    const pts = best.p || [];
    if (!pts.length) return null;
    const p = pts[Math.floor(r() * pts.length) % pts.length];
    return { x: p[0], y: p[1], nx: p[2] || 0, ny: p[3] || -1 };
  }
  MG.particles = { field, edge };
})();
