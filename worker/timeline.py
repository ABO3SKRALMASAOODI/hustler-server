"""Source-timeline <-> output-timeline mapping for a set of keep segments.

Everything the agent writes is in SOURCE seconds (except music, which is
output-positioned by definition). The renderer uses this mapping to place
captions, volume automation, and ducking windows on the OUTPUT timeline after
the cuts are applied.
"""


class Timeline:
    def __init__(self, keep):
        """keep: sorted, non-overlapping [[s, e], ...] in source seconds."""
        self.segs = [(float(s), float(e)) for s, e in keep]
        self.offsets = []          # output start time of each segment
        acc = 0.0
        for s, e in self.segs:
            self.offsets.append(acc)
            acc += e - s
        self.out_duration = acc

    def src_to_out(self, t):
        """Map a source time to output time. Times inside a cut region map to
        None."""
        for (s, e), off in zip(self.segs, self.offsets):
            if s - 1e-6 <= t <= e + 1e-6:
                return off + min(max(t, s), e) - s
        return None

    def span_to_out(self, t0, t1):
        """Map a source span to a list of output spans (a span crossing cut
        regions splits into pieces; fully-cut spans map to [])."""
        out = []
        for (s, e), off in zip(self.segs, self.offsets):
            lo, hi = max(t0, s), min(t1, e)
            if hi - lo > 1e-3:
                out.append((off + lo - s, off + hi - s))
        return out

    def kept_words(self, words):
        """Words (objects or dicts with t0/t1) whose midpoint survives the
        cut, with output-mapped times. Returns [{'w', 't0', 't1'}] in output
        time, in order."""
        out = []
        for w in words:
            t0 = w["t0"] if isinstance(w, dict) else w.t0
            t1 = w["t1"] if isinstance(w, dict) else w.t1
            token = w["w"] if isinstance(w, dict) else w.w
            mid = (t0 + t1) / 2.0
            o = self.src_to_out(mid)
            if o is None:
                continue
            o0 = self.src_to_out(t0)
            o1 = self.src_to_out(t1)
            half = (t1 - t0) / 2.0
            out.append({
                "w": token,
                "t0": o0 if o0 is not None else max(0.0, o - half),
                "t1": o1 if o1 is not None else min(self.out_duration, o + half),
            })
        return out


def merge_spans(spans, gap=0.3):
    """Merge output spans closer than `gap` — keeps ffmpeg enable expressions
    short when there are hundreds of speech spans."""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]
