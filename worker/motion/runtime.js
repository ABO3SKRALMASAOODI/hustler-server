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
  MG.esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ── frame driver (called by the renderer) ──────────────────────────────
  window.__mgSeek = t => {
    MG.t = t;
    let active = MG._alwaysActive;
    // JS choreography first: it may add/remove elements or classes whose
    // CSS animations must then be seeked to the same instant.
    if (MG._frames.length) {
      if (!MG._windows) active = true;
      else if (MG._windows.some(([a, b]) => t >= a - 1e-6 && t <= b + 1e-6)) active = true;
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
          if (t * 1000 >= startMs - 1 && (endMs === Infinity || t * 1000 <= endMs + 1)) active = true;
        }
      } catch (e) { active = true; }
    }
    return active ? 1 : 0;
  };
  window.__mgErrors = [];
  window.addEventListener('error', e => window.__mgErrors.push(String(e.message || e)));
  window.MG = MG;
})();
