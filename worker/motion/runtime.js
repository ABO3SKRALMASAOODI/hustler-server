/* Valmera motion runtime (MG) — deterministic, frame-seeked animation.
 *
 * The renderer never lets real time pass inside the page. For every output
 * frame it calls window.__mgSeek(t) with the item-local time in seconds and
 * screenshots the transparent result. Everything a template draws must be a
 * pure function of t:
 *   - CSS @keyframes / transitions / Web Animations are paused and seeked to
 *     t automatically (animation-delay is honoured, so a stagger is just a
 *     delay);
 *   - JavaScript choreography registers MG.frame(fn) and computes styles from
 *     t with the helpers below (tweens, springs, staggers, counters, paths).
 * Nothing may read Date.now(), performance.now() or Math.random(): use MG.t
 * and MG.rand(seed). Network access is blocked; fonts are bundled.
 *
 * Static-frame reuse: __mgSeek returns whether anything can still change at
 * t. CSS animations are measured exactly. JS choreography is treated as
 * always-active unless the template declares its moving windows with
 * MG.active([[a, b], ...]) — then frames outside every window are re-used
 * from the previous capture, which is what makes long holds nearly free.
 */
(function () {
  'use strict';
  const MG = {
    t: 0, duration: 1, fps: 30, W: 1080, H: 1920,
    params: {}, theme: {},
    _frames: [], _windows: null, _alwaysActive: false,
  };

  // ── easing ──────────────────────────────────────────────────────────────
  const c1 = 1.70158, c3 = c1 + 1, c4 = (2 * Math.PI) / 3;
  const ease = {
    linear: x => x,
    inQuad: x => x * x,
    outQuad: x => 1 - (1 - x) * (1 - x),
    inOutQuad: x => x < .5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2,
    inCubic: x => x * x * x,
    outCubic: x => 1 - Math.pow(1 - x, 3),
    inOutCubic: x => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2,
    outQuart: x => 1 - Math.pow(1 - x, 4),
    inOutQuart: x => x < .5 ? 8 * x ** 4 : 1 - Math.pow(-2 * x + 2, 4) / 2,
    outQuint: x => 1 - Math.pow(1 - x, 5),
    inQuint: x => x ** 5,
    outExpo: x => x >= 1 ? 1 : 1 - Math.pow(2, -10 * x),
    inExpo: x => x <= 0 ? 0 : Math.pow(2, 10 * x - 10),
    inOutExpo: x => x <= 0 ? 0 : x >= 1 ? 1 : x < .5
      ? Math.pow(2, 20 * x - 10) / 2 : (2 - Math.pow(2, -20 * x + 10)) / 2,
    outCirc: x => Math.sqrt(1 - Math.pow(x - 1, 2)),
    outBack: x => 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2),
    inBack: x => c3 * x * x * x - c1 * x * x,
    inOutBack: x => {
      const c2 = c1 * 1.525;
      return x < .5 ? (Math.pow(2 * x, 2) * ((c2 + 1) * 2 * x - c2)) / 2
        : (Math.pow(2 * x - 2, 2) * ((c2 + 1) * (x * 2 - 2) + c2) + 2) / 2;
    },
    outElastic: x => x <= 0 ? 0 : x >= 1 ? 1
      : Math.pow(2, -10 * x) * Math.sin((x * 10 - .75) * c4) + 1,
    smooth: x => x * x * (3 - 2 * x),
    smoother: x => x * x * x * (x * (x * 6 - 15) + 10),
    // Motion-design "snappy" curve: fast start, long soft settle (≈ AE 85% ease-out).
    snap: x => 1 - Math.pow(1 - x, 6),
  };
  MG.ease = ease;
  const E = name => typeof name === 'function' ? name : (ease[name] || ease.outCubic);

  // ── math ────────────────────────────────────────────────────────────────
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, p) => a + (b - a) * p;
  MG.clamp = clamp; MG.lerp = lerp;
  /** progress of t through [start, start+dur], clamped 0..1 */
  MG.range = (t, start, dur) => dur <= 0 ? (t >= start ? 1 : 0) : clamp((t - start) / dur);
  /** eased value between a and b over [start, start+dur] */
  MG.tween = (t, start, dur, a, b, e = 'outCubic') => lerp(a, b, E(e)(MG.range(t, start, dur)));
  /** Damped spring from 0 to 1 started at `start` (seconds). Overshoots when
   *  damping is low. stiffness/damping/mass follow the usual physics names. */
  MG.spring = (t, start = 0, o = {}) => {
    const k = o.stiffness || 170, d = o.damping || 14, m = o.mass || 1;
    const x = t - start;
    if (x <= 0) return 0;
    const w0 = Math.sqrt(k / m), zeta = d / (2 * Math.sqrt(k * m));
    if (zeta < 1) {
      const wd = w0 * Math.sqrt(1 - zeta * zeta);
      return 1 - Math.exp(-zeta * w0 * x) * (Math.cos(wd * x) + (zeta * w0 / wd) * Math.sin(wd * x));
    }
    return 1 - Math.exp(-w0 * x) * (1 + w0 * x);
  };
  /** Duration (s) after which a spring is visually settled (|1-v| < 0.002). */
  MG.springSettle = (o = {}) => {
    for (let x = 0.05; x < 4; x += 0.02) {
      let ok = true;
      for (let y = x; y < x + 0.3; y += 0.02) if (Math.abs(1 - MG.spring(y, 0, o)) > 0.002) { ok = false; break; }
      if (ok) return x;
    }
    return 4;
  };
  MG.stagger = (i, each = 0.05, from = 0) => from + i * each;
  // deterministic PRNG + smooth 1D noise (for shake, wiggle, particles)
  MG.rand = seed => { let s = (seed * 2654435761) >>> 0; return () => { s = (s + 0x6D2B79F5) >>> 0; let r = Math.imul(s ^ (s >>> 15), 1 | s); r ^= r + Math.imul(r ^ (r >>> 7), 61 | r); return ((r ^ (r >>> 14)) >>> 0) / 4294967296; }; };
  const hash = n => { const s = Math.sin(n * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s); };
  MG.noise = x => { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return lerp(hash(i), hash(i + 1), u) * 2 - 1; };

  // ── declaring motion windows (static-frame reuse) ──────────────────────
  MG.active = windows => { MG._windows = (MG._windows || []).concat(windows); };
  MG.always = () => { MG._alwaysActive = true; };
  MG.frame = fn => { MG._frames.push(fn); };

  // ── DOM helpers ─────────────────────────────────────────────────────────
  MG.$ = (sel, root = document) => root.querySelector(sel);
  MG.$$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  MG.el = (tag, cls, text, parent) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  };
  /** Split an element's text into word (and optionally char) spans.
   *  Returns [{el, chars:[el...]}...]. Whitespace is preserved between words. */
  MG.split = (el, mode = 'words') => {
    const text = el.textContent;
    el.textContent = '';
    const words = [];
    text.split(/(\s+)/).forEach(tok => {
      if (!tok) return;
      if (/^\s+$/.test(tok)) { el.appendChild(document.createTextNode(' ')); return; }
      const w = MG.el('span', 'mg-w', null, el);
      const chars = [];
      if (mode === 'chars') {
        Array.from(tok).forEach(ch => chars.push(MG.el('span', 'mg-c', ch, w)));
      } else w.textContent = tok;
      words.push({ el: w, text: tok, chars });
    });
    return words;
  };
  /** Apply a transform/opacity/blur state. Keys: x, y (px), scale, sx, sy,
   *  rotate, rx, ry (deg), z (px), skewX, opacity, blur (px), bright. */
  MG.set = (el, s) => {
    if (!el) return;
    const tr = [];
    if (s.perspective) tr.push(`perspective(${s.perspective}px)`);
    if (s.x || s.y || s.z) tr.push(`translate3d(${s.x || 0}px,${s.y || 0}px,${s.z || 0}px)`);
    if (s.rotate) tr.push(`rotate(${s.rotate}deg)`);
    if (s.rx) tr.push(`rotateX(${s.rx}deg)`);
    if (s.ry) tr.push(`rotateY(${s.ry}deg)`);
    if (s.skewX) tr.push(`skewX(${s.skewX}deg)`);
    if (s.scale != null) tr.push(`scale(${s.scale})`);
    if (s.sx != null || s.sy != null) tr.push(`scale(${s.sx ?? 1},${s.sy ?? 1})`);
    el.style.transform = tr.join(' ');
    if (s.opacity != null) el.style.opacity = clamp(s.opacity);
    const f = [];
    if (s.blur) f.push(`blur(${Math.max(0, s.blur).toFixed(2)}px)`);
    if (s.bright != null && s.bright !== 1) f.push(`brightness(${s.bright})`);
    el.style.filter = f.join(' ');
  };
  /** Interpolate between two state objects with an easing over a window. */
  MG.anim = (el, t, start, dur, from, to, e = 'outCubic') => {
    const p = E(e)(MG.range(t, start, dur)), s = {};
    const keys = new Set([...Object.keys(from), ...Object.keys(to)]);
    keys.forEach(k => {
      const a = from[k] ?? (k === 'opacity' || k === 'scale' ? 1 : 0);
      const b = to[k] ?? (k === 'opacity' || k === 'scale' ? 1 : 0);
      s[k] = (typeof a === 'number' && typeof b === 'number') ? lerp(a, b, p) : (p < 1 ? a : b);
    });
    MG.set(el, s);
    return p;
  };
  /** Count from a to b with formatting. */
  MG.count = (t, start, dur, a, b, o = {}) => {
    const p = E(o.ease || 'outExpo')(MG.range(t, start, dur));
    const v = lerp(a, b, p), d = o.decimals ?? (Math.abs(b) < 10 && b % 1 ? 1 : 0);
    let s = v.toFixed(d);
    if (o.separator !== false) s = s.replace(/\B(?=(\d{3})+(?!\d))/g, o.separator || ',');
    return (o.prefix || '') + s + (o.suffix || '');
  };
  /** Draw an SVG path/line/polyline progressively (0..1). */
  MG.draw = (pathEl, p) => {
    const L = pathEl.getTotalLength ? pathEl.getTotalLength() : 1000;
    pathEl.style.strokeDasharray = `${L} ${L}`;
    pathEl.style.strokeDashoffset = `${L * (1 - clamp(p))}`;
  };
  /** Fit text inside a box by shrinking font-size (px) between min and max. */
  MG.fit = (el, o = {}) => {
    const maxW = o.width || el.parentElement.clientWidth, maxH = o.height || 1e9;
    let lo = o.min || 24, hi = o.max || 220, best = lo;
    el.style.whiteSpace = o.nowrap ? 'nowrap' : el.style.whiteSpace;
    for (let i = 0; i < 18; i++) {
      const mid = (lo + hi) / 2;
      el.style.fontSize = mid + 'px';
      const r = el.getBoundingClientRect();
      if (r.width <= maxW + 0.5 && r.height <= maxH + 0.5 && el.scrollWidth <= maxW + 1) { best = mid; lo = mid; } else hi = mid;
    }
    el.style.fontSize = best + 'px';
    return best;
  };
  /** Typewriter: visible prefix of `text` at chars-per-second from start. */
  MG.typed = (text, t, start, cps = 28) => {
    const n = Math.floor(Math.max(0, t - start) * cps);
    return Array.from(text).slice(0, n).join('');
  };
  /** Small hand-held camera wiggle (px / deg) from smooth noise. */
  MG.wiggle = (t, amp = 4, freq = 1.3, seed = 1) => ({
    x: MG.noise(t * freq + seed * 10) * amp, y: MG.noise(t * freq + seed * 20 + 5) * amp,
    rotate: MG.noise(t * freq * .7 + seed * 30) * amp * .08,
  });
  /** Theme color with fallback. */
  MG.color = (name, fallback) => (MG.theme && MG.theme[name]) || fallback;
  /** Escape text for safe innerHTML use. */
  // Whitespace tokens with *starred* accents; a *multi word run* marks every
  // word in it (trailing punctuation after the closing star stays plain text).
  MG.starWords = s => {
    let run = false;
    return String(s ?? '').split(/\s+/).filter(Boolean).map(tok => {
      let t = tok, acc = run;
      if (/^\*/.test(t)) { acc = true; run = true; t = t.replace(/^\*/, ''); }
      if (/\*[.,!?:;"')\u2019\u201d]*$/.test(t)) { acc = true; run = false; t = t.replace(/\*([.,!?:;"')\u2019\u201d]*)$/, '$1'); }
      return { t: t.replace(/\*/g, ''), acc };
    }).filter(w => w.t);
  };
  MG.esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ── legibility: the plate under the graphic ────────────────────────────
  // MG.plate is the program picture the renderer measured under this item
  // (worker/plate.py): {c, r, dq, s: [{t, g, d}]} — a c x r grid of luma
  // (0-255, row-major; base64 of its bytes, or a plain array) over the
  // design space at composition seconds t, and (optional, ``d``) the same
  // grid of each cell's luma deviation — how busy the plate is there — as
  // 4-bit levels of ``dq`` luma packed two to a byte. It is null
  // when nothing was measured, and every helper below then answers "nothing
  // to do": a template must render exactly as it did before plates existed,
  // and on a dark plate it must not change at all.
  MG.plate = null;
  const toLin = v => v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  const toGamma = L => L <= 0.0031308 ? L * 12.92 : 1.055 * Math.pow(L, 1 / 2.4) - 0.055;
  /** WCAG relative luminance of '#RGB', '#RRGGBB' or 'rgb(a)(...)' (white when unparsable). */
  MG.luminance = col => {
    let r = 255, g = 255, b = 255;
    const s = String(col || '').trim();
    let m = s.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (m) {
      const h = m[1].length === 3 ? m[1].replace(/./g, x => x + x) : m[1];
      r = parseInt(h.slice(0, 2), 16); g = parseInt(h.slice(2, 4), 16); b = parseInt(h.slice(4, 6), 16);
    } else if ((m = s.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i))) { r = +m[1]; g = +m[2]; b = +m[3]; }
    return 0.2126 * toLin(r / 255) + 0.7152 * toLin(g / 255) + 0.0722 * toLin(b / 255);
  };
  MG.contrast = (L1, L2) => (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
  /** The plate under rect [x0, y0, x1, y1] (design px) over composition
   *  seconds [t0, t1] (the nearest moment when none falls inside):
   *  {mean, lo, hi} as 0-1 luma — the area-weighted mean and 15th / 85th
   *  percentiles of the cells it covers — or null when unknown. */
  MG.plateAt = (rect, t0, t1) => {
    const P = MG.plate;
    if (!P || !Array.isArray(P.s) || !P.s.length || !rect || !(P.c > 0) || !(P.r > 0)) return null;
    const dec = v => { try { const b = atob(v); return Uint8Array.from(b, ch => ch.charCodeAt(0)); } catch (e) { return []; } };
    // a detail grid comes as 4-bit levels of P.dq luma, two cells a byte
    const decD = v => {
      const b = dec(v), q = +P.dq || 0;
      if (!q) return b;
      const n = P.c * P.r, out = new Uint8Array(n);
      for (let i = 0; i < n; i++) out[i] = q * ((b[i >> 1] >> (i & 1 ? 0 : 4)) & 15);
      return out;
    };
    P.s.forEach(s => {                 // base64 grids decode once, on first use
      if (typeof s.g === 'string') s.g = dec(s.g);
      if (typeof s.d === 'string') s.d = decD(s.d);
    });
    const a = t0 == null ? -Infinity : t0, b = t1 == null ? Infinity : t1;
    let ss = P.s.filter(s => s.t >= a - 1e-6 && s.t <= b + 1e-6);
    if (!ss.length) {
      let bd = Infinity;
      P.s.forEach(s => { const d = s.t < a ? a - s.t : s.t - b; if (d < bd) { bd = d; ss = [s]; } });
    }
    const cw = MG.W / P.c, ch = MG.H / P.r;
    const x0 = clamp(rect[0], 0, MG.W), x1 = clamp(rect[2], 0, MG.W);
    const y0 = clamp(rect[1], 0, MG.H), y1 = clamp(rect[3], 0, MG.H);
    if (x1 - x0 < 1 || y1 - y0 < 1) return null;
    const cells = [];
    let sw = 0, sv = 0, dw = 0, dv = 0;
    for (let j = Math.floor(y0 / ch); j <= Math.min(P.r - 1, Math.floor((y1 - 1e-6) / ch)); j++) {
      const wy = Math.min(y1, (j + 1) * ch) - Math.max(y0, j * ch);
      if (wy <= 0) continue;
      for (let i = Math.floor(x0 / cw); i <= Math.min(P.c - 1, Math.floor((x1 - 1e-6) / cw)); i++) {
        const wx = Math.min(x1, (i + 1) * cw) - Math.max(x0, i * cw);
        if (wx <= 0) continue;
        for (const s of ss) {
          const v = +s.g[j * P.c + i];
          if (!isFinite(v)) continue;
          cells.push([v, wx * wy]); sw += wx * wy; sv += v * wx * wy;
          const d = s.d && s.d.length ? +s.d[j * P.c + i] : NaN;
          if (isFinite(d)) { dw += wx * wy; dv += d * wx * wy; }
        }
      }
    }
    if (!cells.length || sw <= 0) return null;
    cells.sort((p, q) => p[0] - q[0]);
    const pct = f => { let acc = 0; for (const [v, w] of cells) { acc += w; if (acc >= f * sw) return v; } return cells[cells.length - 1][0]; };
    // detail: the mean in-cell luma deviation (0-1) where the plate carries
    // a detail grid (worker/plate.py v2), else null
    return { mean: sv / sw / 255, lo: pct(0.15) / 255, hi: pct(0.85) / 255,
             detail: dw > 0 ? dv / dw / 255 : null };
  };
  /** How much black (an alpha 0-0.9) the plate under rect needs behind
   *  light `ink` so the ink reaches `ratio`:1 over the BRIGHT part of the
   *  plate (its 85th percentile). 0 when unknown, when the plate is already
   *  dark enough, or for dark ink (darkening is the wrong fix for it).
   *  o: {ink = '#FFFFFF', ratio = 4.5 (body; 3 for display type), t0, t1,
   *  have = darkening the template already lays under the ink}. */
  MG.need = (rect, o = {}) => {
    const pl = MG.plateAt(rect, o.t0, o.t1);
    if (!pl) return 0;
    const Li = MG.luminance(o.ink || '#FFFFFF');
    if (Li < 0.3) return 0;
    const Lt = (Li + 0.05) / (o.ratio || 4.5) - 0.05;   // brightest plate the ink reads on
    const v = pl.hi;
    if (toLin(v) <= Lt + 1e-9) return 0;
    let need = Lt <= 0 ? 0.9 : 1 - toGamma(Lt) / v;
    const have = clamp(+o.have || 0, 0, 0.95);
    if (have > 0) need = need <= have ? 0 : 1 - (1 - need) / (1 - have);
    return clamp(need, 0, 0.9);
  };
  /** '#RRGGBB' mixed toward white by k (0-1): same hue, lighter. */
  MG.lift = (col, k) => {
    const m = String(col || '').trim().match(/^#([0-9a-f]{6})$/i);
    if (!m) return col;
    const n = parseInt(m[1], 16), f = c => Math.round(c + (255 - c) * clamp(k, 0, 1));
    return '#' + [n >> 16 & 255, n >> 8 & 255, n & 255].map(c => f(c).toString(16).padStart(2, '0')).join('').toUpperCase();
  };
  /** APCA |Lc| of luminance Lt on Lb. */
  MG.apca = (Lt, Lb) => {
    const soft = Y => (Y < 0.022 ? Y + Math.pow(0.022 - Y, 1.414) : Y);
    const t = soft(Math.max(0, Lt)), b = soft(Math.max(0, Lb));
    if (b > t) { const S = (Math.pow(b, 0.56) - Math.pow(t, 0.57)) * 1.14; return S < 0.1 ? 0 : (S - 0.027) * 100; }
    const S = (Math.pow(b, 0.65) - Math.pow(t, 0.62)) * 1.14;
    return S > -0.1 ? 0 : -(S + 0.027) * 100;
  };
  MG.DARK_PLATE = 0.12;
  MG.LIT_PLATE = 0.7;
  /** The accent as it reads on the plate (README: MG.accentInk). */
  MG.accentInk = (rect, accent, o = {}) => {
    const out = { color: accent, lifted: 0, short: false };
    const pl = MG.plateAt(rect, o.t0, o.t1);
    if (!pl || pl.mean <= MG.DARK_PLATE) return out;
    const lc = o.lc || 35, max = o.max ?? 0.6;
    const keep = 1 - clamp(+o.have || 0, 0, 0.95);
    const bg = toLin(pl.mean * keep);
    // lifting toward white only helps an accent brighter than the plate:
    // where the plate is as bright as the accent, or its bright part is lit
    // (a lit wall, a projector screen around a head), a paler accent just
    // washes out into it — the template's pocket / dark-ink pass handles it
    if (bg >= MG.luminance(accent) || pl.hi * keep >= MG.LIT_PLATE) return out;
    const reads = c => MG.apca(MG.luminance(c), bg) >= lc;
    if (reads(accent)) return out;
    for (let k = 0.04; k <= max + 1e-9; k += 0.04) {
      const c = MG.lift(accent, k);
      if (MG.luminance(c) > bg && reads(c)) return { color: c, lifted: +k.toFixed(2), short: false };
    }
    return { color: MG.lift(accent, max), lifted: max, short: true };
  };
  /** Contrast a translucent dark panel (glass card, pill, plate) aims for
   *  over a bright plate: about what the same glass shows over dark footage,
   *  so it stays dark glass instead of turning into a muddy grey card. */
  MG.PANEL_RATIO = 11;
  /** Dark ink for light type over a bright plate (MG.darkInk); true when it
   *  reaches `ratio`:1 over the DARK part of the plate under rect (its 15th
   *  percentile), i.e. the plate is bright all the way across. o.have: the
   *  darkening a template keeps under the type even as dark ink (a wash). */
  MG.DARK_INK = '#141414';
  MG.darkInkOK = (rect, o = {}) => {
    const pl = MG.plateAt(rect, o.t0, o.t1);
    if (!pl) return false;
    const keep = 1 - clamp(+o.have || 0, 0, 0.95);
    return MG.contrast(MG.luminance(o.ink || MG.DARK_INK), toLin(pl.lo * keep)) >= (o.ratio || 4.5);
  };
  /** A dark pocket behind type over a bright plate: full `alpha` over rect
   *  (+ pad), a feathered rounded edge. Inserted first in `parent` (page
   *  coordinates when parent is the root), so it sits behind the type; the
   *  caller drives its opacity (it starts hidden). o: {pad: [x, y], radius,
   *  feather} in px. Returns the element; .box is its painted extent. */
  MG.backing = (parent, rect, alpha, o = {}) => {
    const pad = o.pad || [24, 14], f = o.feather ?? 26;
    const x = rect[0] - pad[0], y = rect[1] - pad[1];
    const w = rect[2] - rect[0] + 2 * pad[0], h = rect[3] - rect[1] + 2 * pad[1];
    const el = document.createElement('div');
    el.className = 'mg-backing';
    const a = clamp(alpha, 0, 0.92).toFixed(3);
    el.style.cssText = `position:absolute;left:${x.toFixed(1)}px;top:${y.toFixed(1)}px;` +
      `width:${w.toFixed(1)}px;height:${h.toFixed(1)}px;pointer-events:none;opacity:0;` +
      `border-radius:${(o.radius ?? Math.min(h / 2, 40)).toFixed(1)}px;background:rgba(0,0,0,${a});` +
      `box-shadow:0 0 ${f.toFixed(1)}px ${(f * 0.3).toFixed(1)}px rgba(0,0,0,${a})`;
    (parent || document.body).insertBefore(el, (parent || document.body).firstChild);
    const ext = f * 0.3 + f;
    el.box = [x - ext, y - ext, x + w + ext, y + h + ext];
    return el;
  };
  /** Union a rect into MG.box (call after the template set it). */
  MG.growBox = r => {
    if (!r) return;
    const b = MG.box;
    MG.box = b ? [Math.min(b[0], r[0]), Math.min(b[1], r[1]), Math.max(b[2], r[2]), Math.max(b[3], r[3])] : r.slice();
    MG.box = [Math.max(0, MG.box[0]), Math.max(0, MG.box[1]), Math.min(MG.W, MG.box[2]), Math.min(MG.H, MG.box[3])];
  };
  /** Union of elements' page rects [x0, y0, x1, y1] (null when none). */
  MG.rectOf = els => {
    let u = null;
    (Array.isArray(els) ? els : [els]).forEach(e => {
      if (!e) return;
      const r = e.getBoundingClientRect();
      if (r.width <= 0 && r.height <= 0) return;
      u = u ? [Math.min(u[0], r.left), Math.min(u[1], r.top), Math.max(u[2], r.right), Math.max(u[3], r.bottom)]
        : [r.left, r.top, r.right, r.bottom];
    });
    return u;
  };

  // ── legibility decisions (round 7): no boxes in an editorial look ─────
  // Judged (round 5): every translucent plate behind type read as a dirty
  // rectangle — Thiel's kicker crossing the bright projector, Elon's lines
  // on a white T-shirt — and a blurred dark halo read as a smudge. A
  // template decides from the plate under its GLYPHS (each word's ink box,
  // not its line box with padding), in this order:
  //   1. nothing: the plate already reads (or none was measured);
  //   2. placement is the caller's (captions slide inside their band);
  //   3. ink: a plate bright all the way under the weak glyphs takes dark
  //      ink, the accent kept in its own hue — deepened toward black until
  //      it reads, never paled toward white;
  //   4. a glyph scrim: a feathered darkening that follows the letterforms
  //      (stacked tight text shadows) as strong as the plate needs — it is
  //      sized by the glyphs themselves, so it never grows per word and
  //      never shows where nothing is written;
  //   5. only then a box (MG.backing), pre-sized to the final block.
  // Scrims and glows only ever darken: nothing lifts the plate.
  MG.NEED_OK = 0.1;
  // A busy plate (a shirt print, a sign, stickers: in-cell luma deviation
  // past this share) firms up light type's contact shadow even where its
  // luminance would pass.
  MG.DETAIL_BUSY = 0.1;
  // The glyph scrim: [blur (em), alpha per unit of strength, the share of
  // that alpha the plate right beside a stroke receives].
  const GS = [[0.045, 1.0, 0.55], [0.12, 0.85, 0.42], [0.24, 0.55, 0.25]];
  const gsAlphas = k => GS.map(([_b, m]) => clamp(m * k, 0, 0.95));
  const gsGives = k => 1 - gsAlphas(k).reduce((p, a, i) => p * (1 - GS[i][2] * a), 1);
  /** The most darkening a glyph scrim gives beside a stroke. */
  MG.GLYPH_MAX = +gsGives(10).toFixed(3);
  /** CSS text-shadow of a glyph scrim that darkens the plate beside the
   *  strokes by `need` (0-1; capped at MG.GLYPH_MAX); '' for none. */
  MG.glyphScrimShadow = need => {
    if (!(need > 0)) return '';
    let lo = 0, hi = 10;
    for (let i = 0; i < 24; i++) { const m = (lo + hi) / 2; if (gsGives(m) >= Math.min(need, MG.GLYPH_MAX)) hi = m; else lo = m; }
    const a = gsAlphas(hi);
    return GS.map(([b], i) => `0 ${i === 2 ? '0.02em' : '0'} ${b}em rgba(0,0,0,${a[i].toFixed(3)})`).join(', ');
  };
  // What MG.legible changed on an element (its colour and text shadow before,
  // and the shadow it set), so a template that lays out again (web fonts
  // arriving) decides afresh instead of stacking on its first decision.
  const touch = (el, color, shadow) => {
    if (!el._mgL) el._mgL = { c: el.style.color, s: el.style.textShadow };
    if (color != null) el.style.color = color;
    if (shadow != null) el.style.textShadow = shadow;
    el._mgL.set = el.style.textShadow;
  };
  const untouch = root => [root].concat(Array.from(root.querySelectorAll('*'))).forEach(e => {
    const m = e._mgL;
    if (!m) return;
    e.style.color = m.c;
    if (e.style.textShadow === m.set) e.style.textShadow = m.s;   // unless the template re-set it
    delete e._mgL;
  });
  /** Undo what MG.legible changed under els (a template that sets its own
   *  colours before calling it again calls this first). */
  MG.unlegible = els => (Array.isArray(els) ? els : [els]).forEach(el => { if (el) untouch(el); });
  /** Apply a glyph scrim to el (and any descendant that sets its own shadow). */
  MG.glyphScrim = (el, need) => {
    const css = MG.glyphScrimShadow(need);
    if (!el || !css) return;
    touch(el, null, css);
    el.querySelectorAll('*').forEach(c => { if (c.style && c.style.textShadow) touch(c, null, css); });
  };
  /** '#RRGGBB' mixed toward black by k (0-1): the same hue, deeper. */
  MG.deepen = (col, k) => {
    const m = String(col || '').trim().match(/^#([0-9a-f]{6})$/i);
    if (!m) return col;
    const n = parseInt(m[1], 16), f = c => Math.round(c * (1 - clamp(k, 0, 1)));
    return '#' + [n >> 16 & 255, n >> 8 & 255, n & 255].map(c => f(c).toString(16).padStart(2, '0')).join('').toUpperCase();
  };
  /** The words of el (its .mg-w spans), else el itself. */
  const wordsOf = el => {
    const w = el && el.querySelectorAll ? Array.from(el.querySelectorAll('.mg-w')) : [];
    return w.length ? w : (el ? [el] : []);
  };
  /** The ink box of els' glyphs (MG.glyphBoxes), else their layout rects. */
  MG.inkRect = els => {
    const g = MG.glyphBoxes(els);
    if (!g.length) return MG.rectOf(els);
    return g.reduce((u, b) => [Math.min(u[0], b[0]), Math.min(u[1], b[1]), Math.max(u[2], b[2]), Math.max(u[3], b[3])], g[0].slice());
  };
  /** What light type over els asks the plate for, word by word: the
   *  largest MG.need of any one word's glyphs (a busy plate under a word
   *  asks for at least a contact shadow). o: as MG.need. */
  MG.glyphNeed = (els, o = {}) => {
    let n = 0;
    const light = MG.luminance(o.ink || '#FFFFFF') >= 0.3;
    (Array.isArray(els) ? els : [els]).forEach(el => wordsOf(el).forEach(w => {
      const r = MG.inkRect(w);
      if (!r || r[2] - r[0] < 1 || r[3] - r[1] < 1) return;
      let v = MG.need(r, o);
      if (light) {
        const pl = MG.plateAt(r, o.t0, o.t1);
        if (pl && pl.detail != null && pl.detail > MG.DETAIL_BUSY)
          v = Math.max(v, Math.min(0.3, 0.12 + 1.5 * (pl.detail - MG.DETAIL_BUSY)));
      }
      n = Math.max(n, v);
    }));
    return n;
  };
  /** MG.darkInkOK under EVERY word of els (their .mg-w spans), each by its
   *  own glyphs: a line across a white shirt and a dark microphone is no
   *  place for dark ink, though the shirt is most of it. o: as darkInkOK. */
  MG.darkInkWords = (els, o = {}) => (Array.isArray(els) ? els : [els]).every(el =>
    wordsOf(el).every(w => {
      const r = MG.inkRect(w);
      return !r || r[2] - r[0] < 1 || r[3] - r[1] < 1 || MG.darkInkOK(r, o);
    }));
  /** The deepest an accent may go as dark ink before its hue is lost — and
   *  for a yellow, gold or amber accent (hue 30-80 deg), which turns olive
   *  and brown long before it reads dark, MG.DEEPEN_WARM: such an accent
   *  keeps its colour and the type takes a glyph scrim instead. */
  MG.DEEPEN_MAX = 0.6;
  MG.DEEPEN_WARM = 0.15;
  const hueOf = col => {
    const m = String(col || '').trim().match(/^#([0-9a-f]{6})$/i);
    if (!m) return null;
    const n = parseInt(m[1], 16), r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    if (d < 1e-6) return null;
    const h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return (h * 60 + 360) % 360;
  };
  /** The accent as it reads on a BRIGHT plate under rect: itself when it
   *  reaches `ratio`:1 against the plate's darker part, else the least
   *  deepening toward black (same hue) that does; null when that takes
   *  more than its hue allows (MG.DEEPEN_MAX / MG.DEEPEN_WARM).
   *  o: {ratio = 3, t0, t1}. */
  MG.accentOnLight = (rect, accent, o = {}) => {
    const pl = MG.plateAt(rect, o.t0, o.t1);
    if (!pl) return null;
    const Lb = toLin(pl.lo * (1 - clamp(+o.have || 0, 0, 0.95))), ratio = o.ratio || 3, hue = hueOf(accent);
    const max = hue != null && hue >= 30 && hue <= 80 ? MG.DEEPEN_WARM : MG.DEEPEN_MAX;
    for (let k = 0; k <= max + 1e-9; k += 0.05) {
      const c = k > 0 ? MG.deepen(accent, k) : accent, Lc = MG.luminance(c);
      if (Lc < Lb && MG.contrast(Lc, Lb) >= ratio) return c;
    }
    return null;
  };
  /** Decide and apply legibility for LIGHT type over the measured plate.
   *  parts: [{el, ratio = 3, ink = '#FFFFFF', acc: [accent word els],
   *  accent (their colour), accRatio = ratio, base = o.base (the darkening
   *  the part's own text shadow gives beside its strokes, 0-1)}] — one per
   *  line or block.
   *  o: {t0, t1, dark = true (dark ink allowed), have (darkening the
   *  template already lays under its type: its own soft scrim), under (the
   *  part of that darkening which stays under dark ink: a wash the template
   *  does not stand down; 0 when it hides its scrim for dark ink), root (the
   *  box's parent), box: {pad: [x, y], feather, radius, join} px}. Weak parts (need past
   *  MG.NEED_OK) get, in order: dark ink (all of them, when the plate is
   *  bright under every weak glyph and every accent keeps its hue), else a
   *  glyph scrim each, else (past MG.GLYPH_MAX) one pre-sized box per run.
   *  Returns {mode: 'none'|'dark'|'scrim'|'box', need, boxes: [els]}: the
   *  caller fades the boxes with its type and grows MG.box by their .box. */
  MG.legible = (parts, o = {}) => {
    const res = { mode: 'none', need: 0, boxes: [] };
    parts.forEach(p => { if (p && p.el) untouch(p.el); });
    if (!MG.plate) return res;
    const q = parts.filter(p => p && p.el).map(p => {
      const ink = p.ink || '#FFFFFF', ratio = p.ratio || 3, acc = (p.acc || []).filter(Boolean);
      const t0 = p.t0 ?? o.t0, t1 = p.t1 ?? o.t1;
      const plain = wordsOf(p.el).filter(w => !acc.includes(w) && !acc.some(a => a.contains(w)));
      let need = plain.length ? MG.glyphNeed(plain, { ink, ratio, t0, t1, have: o.have }) : 0;
      if (acc.length && p.accent) need = Math.max(need, MG.glyphNeed(acc, { ink: p.accent, ratio: p.accRatio || ratio, t0, t1, have: o.have }));
      return Object.assign({}, p, { ink, ratio, acc, need, t0, t1, light: MG.luminance(ink) >= 0.3 });
    });
    // weak: past what the part's own shadow already gives (``base``: a
    // glyph scrim REPLACES that shadow, so it never sets in weaker)
    const weak = q.filter(p => p.need > Math.max(MG.NEED_OK, +p.base || +o.base || 0) && p.light);
    if (!weak.length) return res;
    res.need = Math.max(...weak.map(p => p.need));
    // 3. dark ink, when the plate is bright under every weak glyph: WORD
    // by word (a line across a white shirt and a dark microphone is no
    // place for dark ink, though the shirt is most of it)
    if (o.dark !== false) {
      if (weak.every(p => MG.darkInkWords(p.el, { ratio: p.ratio, t0: p.t0, t1: p.t1, have: o.under }))) {
        const deep = [];
        const ok = weak.every(p => !p.acc.length || !p.accent || (() => {
          const c = MG.accentOnLight(MG.inkRect(p.acc), p.accent, { ratio: p.accRatio || p.ratio, t0: p.t0, t1: p.t1, have: o.under });
          if (c) deep.push([p, c]);
          return !!c;
        })());
        if (ok) {
          weak.forEach(p => {
            touch(p.el, MG.DARK_INK, 'none');
            p.el.querySelectorAll('*').forEach(c => { if (c.style && c.style.textShadow) touch(c, null, 'none'); });
          });
          deep.forEach(([p, c]) => p.acc.forEach(a => touch(a, c, null)));
          res.mode = 'dark';
          return res;
        }
      }
    }
    // 4. a glyph scrim on each weak part the letterforms can carry
    const heavy = [];
    weak.forEach(p => {
      if (p.need > MG.GLYPH_MAX + 1e-9) { heavy.push(p); return; }
      MG.glyphScrim(p.el, p.need);
      // the capture box holds the scrim's reach past the glyphs
      const r = MG.inkRect(p.el), f = 0.55 * (parseFloat(getComputedStyle(p.el).fontSize) || 0);
      if (r && MG.box) MG.growBox([r[0] - f, r[1] - f, r[2] + f, r[3] + f]);
    });
    res.mode = 'scrim';
    if (!heavy.length) return res;
    // 5. a box, pre-sized to the final block of each run of heavy parts
    const bo = o.box || {};
    const rects = heavy.map(p => [p, MG.inkRect(p.el)]).filter(([, r]) => r).sort((a, b) => a[1][1] - b[1][1]);
    const runs = [];
    rects.forEach(([p, r]) => {
      const last = runs[runs.length - 1];
      if (last && r[1] <= last.r[3] + (bo.join || 0)) {
        last.r = [Math.min(last.r[0], r[0]), Math.min(last.r[1], r[1]), Math.max(last.r[2], r[2]), Math.max(last.r[3], r[3])];
        last.need = Math.max(last.need, p.need);
      } else runs.push({ r: r.slice(), need: p.need });
    });
    runs.forEach(run => {
      res.boxes.push(MG.backing(o.root || document.body, run.r, run.need,
        { pad: bo.pad, feather: bo.feather, radius: bo.radius }));
    });
    res.mode = 'box';
    return res;
  };

  // Secondary type (kickers, sub-labels, labels, attributions) is never set
  // smaller than a phone can read: its cap height is at least MIN_CAP of the
  // frame height (2.2% on 9:16 = 42 design px; the same fraction of a
  // shorter frame's height).
  MG.MIN_CAP = 0.022;
  const capCtx = document.createElement('canvas').getContext('2d');
  /** Cap height / font size of el's computed font (0.72 when unmeasurable). */
  MG.capRatio = el => {
    const cs = getComputedStyle(el);
    capCtx.font = `${cs.fontStyle} ${cs.fontWeight} 100px ${cs.fontFamily}`;
    const r = capCtx.measureText('H').actualBoundingBoxAscent / 100;
    return r > 0.3 && r < 1.2 ? r : 0.72;
  };
  /** The smallest font-size (px) secondary text in el may use. */
  MG.minType = el => MG.MIN_CAP * MG.H / MG.capRatio(el);
  /** Raise el's font-size to MG.minType(el) when it is smaller; returns the px size. */
  MG.floorType = el => {
    const cur = parseFloat(getComputedStyle(el).fontSize) || 0, m = MG.minType(el);
    if (cur < m - 0.01) { el.style.fontSize = m.toFixed(2) + 'px'; return m; }
    return cur;
  };
  /** Fit secondary text into `width`: one line from `max` down to the
   *  floor (MG.minType, or `min` when larger); at the floor it wraps into
   *  balanced lines instead of shrinking further — only a single word too
   *  long for the width goes smaller. Returns the px size. */
  MG.fitSecondary = (el, o = {}) => {
    const width = o.width || el.parentElement.clientWidth;
    Object.assign(el.style, { whiteSpace: 'nowrap', maxWidth: '', textWrap: '' });
    el.style.fontSize = '40px';
    const floor = Math.max(o.min || 0, MG.minType(el)), hi = Math.max(o.max || floor, floor);
    let fs = MG.fit(el, { width, min: floor, max: hi, nowrap: true });
    if (el.scrollWidth > width + 1) {
      Object.assign(el.style, { whiteSpace: 'normal', maxWidth: width + 'px', textWrap: 'balance' });
      fs = floor; el.style.fontSize = fs + 'px';
      while (el.scrollWidth > width + 1 && fs > 12) { fs *= 0.95; el.style.fontSize = fs + 'px'; }
    }
    return fs;
  };

  // ── glyph-aware leading ────────────────────────────────────────────────
  // Stacked rows of display type set with negative leading collide where a
  // descender (y p g, a script swash) of one row meets the caps of the next.
  // Line boxes cannot see that: the ink of a glyph is not its line box. These
  // helpers measure the real ink of every glyph — its pen position from the
  // laid-out DOM, its ink extents from the font (canvas text metrics in the
  // element's own computed font) — so a template can stack rows as tight as
  // the glyphs allow and no tighter. Measure before any transform is applied.
  // The metrics are cached for ONE measurement only: a template's first
  // layout runs before its web fonts have loaded (canvas then measures the
  // fallback face under the same font string), and a document-wide cache
  // would hand those fallback ascents/descents to the real layout after
  // MG.ready whenever a row kept its size (height-capped hero, clamped kicker).
  const inkCtx = document.createElement('canvas').getContext('2d');
  const inkOf = (cache, font, ch) => {
    const key = font + '\u0000' + ch;
    let m = cache.get(key);
    if (!m) {
      inkCtx.font = font;
      const r = inkCtx.measureText(ch);
      m = { l: r.actualBoundingBoxLeft || 0, r: r.actualBoundingBoxRight || 0,
            a: r.actualBoundingBoxAscent || 0, d: r.actualBoundingBoxDescent || 0,
            fa: r.fontBoundingBoxAscent };
      cache.set(key, m);
    }
    return m;
  };
  /** Ink box [x0, y0, x1, y1] (page px) of every visible glyph in els. */
  MG.glyphBoxes = els => {
    const out = [], range = document.createRange(), cache = new Map();
    (Array.isArray(els) ? els : [els]).forEach(root => {
      if (!root) return;
      const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node = walk.nextNode(); node; node = walk.nextNode()) {
        const cs = getComputedStyle(node.parentElement);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        const text = node.data;
        for (let i = 0; i < text.length;) {
          const cp = text.codePointAt(i), n = cp > 0xffff ? 2 : 1, ch = text.slice(i, i + n);
          if (!/\s/.test(ch)) {
            range.setStart(node, i); range.setEnd(node, i + n);
            const rc = range.getClientRects()[0];
            if (rc) {
              const m = inkOf(cache, font, ch);
              // the content box's top is the font ascent above the baseline
              const base = rc.top + (m.fa > 0 ? m.fa : rc.height * 0.8);
              out.push([rc.left - m.l, base - m.a, rc.left + m.r, base + m.d]);
            }
          }
          i += n;
        }
      }
    });
    return out;
  };
  /** How far (px) `lower` must move down so none of its glyphs comes within
   *  `clear` px (vertically) of a glyph of `upper` that it overlaps
   *  horizontally (+`pad` px each side). Negative: that much room to spare;
   *  -Infinity when no glyphs share a column. o: {clear = 0, pad = 0}. */
  MG.stackGap = (upper, lower, o = {}) => {
    const U = MG.glyphBoxes(upper), L = MG.glyphBoxes(lower);
    const clear = +o.clear || 0, pad = +o.pad || 0;
    let need = -Infinity;
    for (const g of U) for (const h of L) {
      if (h[0] < g[2] + pad && h[2] > g[0] - pad) need = Math.max(need, g[3] + clear - h[1]);
    }
    return need;
  };

  // ── yielding: a persistent graphic hands its band to other graphics ────
  // The renderer (worker/motion_layer.yield_windows) sets MG.yields =
  // {w: [[a, b], ...], out, in} on a persistent template (the headline band):
  // composition seconds where another graphic occupies its band. The whole
  // page fades out over `out` s ending at a (0: gone on a's frame), stays
  // gone until b and fades back over `in` s from b. Unset (every other item)
  // changes nothing.
  MG.yields = null;
  // A 0 s swap (the headline gone on the frame the other lands) is a real
  // length, not "unset".
  const yieldLen = (v, dflt) => (v === undefined || v === null || isNaN(+v))
    ? dflt : Math.max(1e-3, +v);
  /** 0-1 visibility of the page at t under MG.yields (1 when unset). */
  MG.yieldLevel = t => {
    const Y = MG.yields;
    if (!Y || !Array.isArray(Y.w) || !Y.w.length) return 1;
    const o = yieldLen(Y.out, 0.12), n = yieldLen(Y.in, 0.3);
    let v = 1;
    for (const [a, b] of Y.w) {
      if (t >= a && t <= b) return 0;
      if (t < a && t > a - o) v = Math.min(v, ease.inOutQuad((a - t) / o));
      if (t > b && t < b + n) v = Math.min(v, ease.outCubic((t - b) / n));
    }
    return v;
  };
  const yieldMoving = (t, fd) => {
    const Y = MG.yields;
    if (!Y || !Array.isArray(Y.w) || !Y.w.length) return false;
    const o = yieldLen(Y.out, 0.12), n = yieldLen(Y.in, 0.3);
    return Y.w.some(([a, b]) => (t >= a - o - 1e-6 && t <= a + fd + 1e-6)
      || (t >= b - 1e-6 && t <= b + n + fd + 1e-6));
  };

  // ── frame driver (called by the renderer) ──────────────────────────────
  window.__mgSeek = t => {
    MG.t = t;
    let active = MG._alwaysActive;
    // A frame stands for the interval since the previous one: a change that
    // lands between two frames (a word revealed 5 ms after a frame) must still
    // be captured on the first frame after it, so windows reach one frame on.
    const fd = 1 / (MG.fps || 30);
    // JS choreography first: it may add/remove elements or classes whose
    // CSS animations must then be seeked to the same instant.
    if (MG._frames.length) {
      if (!MG._windows) active = true;
      else if (MG._windows.some(([a, b]) => t >= a - 1e-6 && t <= b + fd + 1e-6)) active = true;
      for (const fn of MG._frames) {
        try { fn(t); } catch (e) { window.__mgErrors.push(String(e && e.message || e)); return 2; }
      }
    }
    for (const a of document.getAnimations()) {
      try {
        a.pause();
        a.currentTime = t * 1000;
        const c = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : null;
        if (c) {
          const startMs = (c.delay || 0), endMs = c.endTime;
          if (t * 1000 >= startMs - 1 && (endMs === Infinity || t * 1000 <= endMs + fd * 1000 + 1)) active = true;
        }
      } catch (e) { active = true; }
    }
    if (MG.yields) {
      const v = MG.yieldLevel(t);
      document.body.style.opacity = v >= 1 ? '' : v.toFixed(4);
      if (yieldMoving(t, fd)) active = true;
    }
    return active ? 1 : 0;
  };
  window.__mgErrors = [];
  window.addEventListener('error', e => window.__mgErrors.push(String(e.message || e)));
  window.MG = MG;
})();
