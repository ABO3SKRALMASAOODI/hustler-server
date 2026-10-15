/* MG.typefx — per-word entrance, hold and exit states for solved type
 * (Wave 2 track 2B; INTERFACES_W2.md §3.5). A run is one word of a
 * LayoutDoc (params._layout): its onset is the spoken onset in composition
 * seconds, t_in the moment its verb starts so the word RESOLVES on the
 * onset frame, t_out the end of its hold. state(run, t) is a pure function
 * of t: {o, blur, dx, dy, sx, sy, glow, fillK, colour, chars, wipe,
 * mosaic, smear, echo}. chars, when set, is a per-character list of
 * {o, ch, dy}. No clocks, no randomness (MG.rand(seed) only).
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  const clamp = (v, a, b) => Math.min(b === undefined ? 1 : b, Math.max(a === undefined ? 0 : a, v));
  const E = MG.ease;
  const FR = () => 1 / (MG.fps || 30);
  const SCR = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&*+=?';
  const hashI = (a, b) => { let h = (a * 73856093) ^ (b * 19349663); h = (h ^ (h >>> 13)) * 1274126177; return ((h ^ (h >>> 16)) >>> 0); };

  const base = r => ({ o: 1, blur: 0, dx: 0, dy: 0, sx: 1, sy: 1, glow: 0, fillK: 1,
    colour: r.colour, chars: null, wipe: null, mosaic: 0, smear: 0, echo: 0, dim: 0 });

  const VERBS = {
    cut(r, t, s) { if (t < r.onset) s.o = 0; },
    fade(r, t, s) { s.o = MG.range(t, r.t_in, Math.max(FR(), r.onset - r.t_in)); },
    blur_resolve(r, t, s) {
      const p = E.inOutQuad(MG.range(t, r.t_in, Math.max(FR(), r.onset - r.t_in)));
      s.o = clamp(p * 1.6); s.blur = (1 - p) * 0.16 * r.size_px; s.sx = s.sy = 1 + 0.04 * (1 - p);
    },
    settle(r, t, s) {
      if (t < r.onset) { s.o = 0; return; }
      const k = MG.spring(t, r.onset, { stiffness: 260, damping: 19 });
      s.sx = s.sy = 1.14 - 0.14 * k; s.dy = (1 - k) * 0.05 * r.size_px;
    },
    drop(r, t, s) {
      const span = Math.max(FR(), r.onset - r.t_in);
      if (t < r.onset) {
        const p = E.inCubic(MG.range(t, r.t_in, span));
        s.o = clamp(p * 3); s.dy = -(1 - p) * 0.28 * r.size_px; return;
      }
      const u = t - r.onset;
      s.dy = -0.05 * r.size_px * Math.exp(-u * 12) * Math.sin(u * 30);
    },
    stretch_smear(r, t, s) {
      const p = E.outExpo(MG.range(t, r.t_in, Math.max(FR(), r.onset - r.t_in)));
      s.o = clamp(p * 2); s.sx = 1 + 1.8 * (1 - p); s.smear = (1 - p) * 0.35 * r.size_px;
    },
    mosaic_resolve(r, t, s) {
      const p = MG.range(t, r.t_in, Math.max(FR(), r.onset - r.t_in));
      s.o = clamp(p * 4); s.mosaic = t >= r.onset ? 0 : Math.max(0, Math.round((1 - p) * (1 - p) * 0.22 * r.size_px));
    },
    scramble_lock(r, t, s) {
      const n = r.text.length, span = Math.max(FR(), r.onset - r.t_in);
      if (t < r.t_in) { s.o = 0; return; }
      const f = Math.floor(t * (MG.fps || 30));
      s.chars = Array.from(r.text, (ch, i) => {
        const lock = r.t_in + span * (i + 1) / n;
        if (t >= lock || ch === ' ') return { o: 1, ch };
        return { o: 0.85, ch: SCR[hashI(f, i + 7 * n) % SCR.length] };
      });
    },
    typewriter(r, t, s) {
      if (t < r.onset) { s.o = 0; return; }
      const n = r.text.length, per = Math.min(0.06, Math.max(FR(), (r.dur || 0.3) * 0.85 / n));
      const k = Math.floor((t - r.onset) / per) + 1;
      s.chars = Array.from(r.text, (ch, i) => ({ o: i < k ? 1 : 0, ch }));
    },
    duration_wipe(r, t, s) {
      // a feathered character wipe across the spoken duration: its 50%
      // point is the word's spoken midpoint
      if (t < r.onset) { s.o = 0; return; }
      s.wipe = MG.range(t, r.onset, Math.max(FR(), r.dur || 0.3));
    },
    strobe_flip(r, t, s) {
      if (t < r.onset) { s.o = 0; return; }
      const f = Math.floor((t - r.onset) / FR());
      if (f < 8) { const on = (f >> 1) % 2 === 0; s.colour = on ? r.alt : r.colour; s.o = f % 4 === 3 ? 0.35 : 1; }
    },
    emboss_ink(r, t, s) {
      if (t < r.t_in) { s.o = 0; return; }
      s.fillK = E.outCubic(MG.range(t, r.onset, 0.18)); s.emboss = 1;
    },
    outline_fill(r, t, s) {
      if (t < r.t_in) { s.o = 0; return; }
      s.o = clamp(MG.range(t, r.t_in, 0.08)); s.fillK = E.outCubic(MG.range(t, r.onset, 0.14)); s.stroke = 1;
    },
    echo_stack(r, t, s) {
      if (t < r.onset) { s.o = 0; return; }
      s.echo = Math.min(4, Math.floor((t - r.onset) / (2 * FR())));
      const k = MG.spring(t, r.onset, { stiffness: 300, damping: 22 }); s.sx = s.sy = 1.08 - 0.08 * k;
    },
    glyph_morph(r, t, s) {
      if (t >= r.onset) return;
      s.morph = E.inOutCubic(MG.range(t, r.t_in, Math.max(FR(), r.onset - r.t_in)));
      if (t < r.t_in) s.o = 0;
    },
    lamp(r, t, s) {
      // the line waits dim; the word ignites as it is said, then glows down
      const dimFrom = r.lamp_from == null ? 0 : r.lamp_from;
      if (t < dimFrom) { s.o = 0; return; }
      if (t < r.onset) { s.dim = 1; s.o = 0.3; s.glow = 0; return; }
      const u = t - r.onset;
      s.o = clamp(0.3 + u / 0.05); s.dim = clamp(1 - u / 0.07);
      s.glow = u < 0.06 ? u / 0.06 : 0.28 + 0.72 * Math.exp(-(u - 0.06) / 0.32);
    },
    decay(r, t, s) { },
  };

  function exitState(r, t, s) {
    const xd = Math.min(0.5, Math.max(0.12, (r.t_out - r.onset) * 0.3));
    if (r.exit === 'hold') return;
    if (t >= r.t_out) { s.o = 0; return; }
    if (r.exit === 'fade' && t > r.t_out - 0.12) s.o *= clamp((r.t_out - t) / 0.12);
    if (r.exit === 'decay' && t > r.t_out - xd) {
      const n = r.text.length, rnd = MG.rand(r.text.length * 131 + Math.round(r.x));
      const order = Array.from(r.text, (_c, i) => [rnd(), i]).sort((a, b) => a[0] - b[0]).map(p => p[1]);
      const p = MG.range(t, r.t_out - xd, xd);
      const chars = s.chars || Array.from(r.text, ch => ({ o: 1, ch }));
      order.forEach((i, k) => {
        const q = clamp((p - k / n * 0.7) / 0.3);
        chars[i] = Object.assign({}, chars[i], { o: chars[i].o * (1 - q), dy: -q * 0.08 * r.size_px });
      });
      s.chars = chars; s.blur += p * 0.02 * r.size_px;
    }
  }

  MG.typefx = {
    verbs: Object.keys(VERBS),
    state(r, t) {
      const s = base(r);
      if (t < r.t_in && r.verb !== 'lamp') { s.o = 0; return s; }
      (VERBS[r.verb] || VERBS.cut)(r, t, s);
      if (r.flip_at != null && t >= r.flip_at) { const c = s.colour; s.colour = c === r.colour ? r.alt : r.colour; }
      exitState(r, t, s);
      return s;
    },
    /** composition-second windows in which the run can change */
    windows(r) {
      const w = [[Math.max(0, r.t_in - 0.02), r.onset + 0.5]];
      if (r.verb === 'lamp') w.push([r.onset, r.onset + 1.4]);
      if (r.verb === 'duration_wipe' || r.verb === 'typewriter') w.push([r.onset, r.onset + (r.dur || 0.3) + 0.1]);
      if (r.exit !== 'hold') w.push([Math.max(0, r.t_out - 0.6), r.t_out + 0.05]);
      if (r.flip_at != null) w.push([r.flip_at - 0.02, r.flip_at + 0.05]);
      if (r.material === 'neon') w.push([r.onset, r.onset + 0.35]);
      return w;
    },
  };
})();
