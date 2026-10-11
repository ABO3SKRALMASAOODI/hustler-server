/* MG.beats — the item's rhythm (params._beats, motion_engine.beats_param),
 * in composition seconds: t = beats, down = downbeats, on = [[t, cls,
 * strength]] onsets (speech: word/stress/phrase_end; music: kick/snare/...).
 * Pure functions of t, like everything a page draws. With no _beats every
 * helper answers "no beat", so a page that uses them still renders.
 */
(function () {
  'use strict';
  const MG = window.MG;
  if (!MG) return;
  const list = k => {
    const b = (MG.params || {})._beats;
    if (!b) return [];
    if (k === 't' || k === 'down') return b[k] || [];
    return (b.on || []).filter(o => !k || k === 'on' || o[1] === k).map(o => o[0]);
  };
  const last = (a, t) => {
    let lo = 0, hi = a.length - 1, r = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (a[m] <= t + 1e-6) { r = m; lo = m + 1; } else hi = m - 1; }
    return r;
  };
  MG.beats = {
    times: k => list(k || 't'),
    index: (t, k) => last(list(k || 't'), t),
    since: (t, k) => { const a = list(k || 't'), i = last(a, t); return i < 0 ? Infinity : t - a[i]; },
    until: (t, k) => { const a = list(k || 't'), i = last(a, t) + 1; return i >= a.length ? Infinity : a[i] - t; },
    pulse: (t, decay, k) => { const s = MG.beats.since(t, k); return s === Infinity ? 0 : Math.max(0, 1 - s / (decay || 0.25)); },
  };
})();
