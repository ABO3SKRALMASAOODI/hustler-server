"""Measure the first read for the writer's whole-script revision."""
import json


HOOK_LAND_S = 3.7          # the hook's collision lands by this many seconds of speech (the judge's median hook end)
CLOSE_S = 5.0              # the close is one landing line of at most this many seconds (the judge's upper quartile)
def _norm(w):
    return "".join(ch for ch in str(w).lower().replace("’", "'") if ch.isalnum() or ch == "'").strip("'")


def _find(spoken, phrase):
    """The index of the last word of the first place `phrase` is spoken in `spoken` ([normalised words]), else None."""
    want = [x for x in (_norm(t) for t in str(phrase or "").split()) if x]
    if not want:
        return None
    for k in range(len(spoken) - len(want) + 1):
        if spoken[k:k + len(want)] == want:
            return k + len(want) - 1
    return None


def measure(words, sc, hook_s=HOOK_LAND_S, close_s=CLOSE_S):
    """{hook_land_s, hook_word, close_s, wps, over: {"hook"|"close": {s, budget}}} from words.json ({"words": [{w, start,
    end, seg}]}, a path or the dict) and the script it reads. The hook lands at the end of the word the writer marked
    (the segment's `lands`, else its last emphasis word found in the line, else the segment's last word), counted from the
    first spoken word; the close is its segment's first word's start to its last word's end."""
    d = json.load(open(words)) if isinstance(words, str) else words
    ws = [w for w in d.get("words") or [] if float(w.get("end", 0)) >= float(w.get("start", 0))]
    segs = sc.get("segments") or []
    if not ws or not segs:
        return dict(hook_land_s=None, close_s=None, wps=None, over={})
    t_first = float(ws[0]["start"])
    hook = [w for w in ws if int(w.get("seg", 0)) == 0]
    last_i = (sc.get("beats") or {}).get("meaning", len(segs) - 1)
    close = [w for w in ws if int(w.get("seg", 0)) == last_i]
    spoken = [_norm(w["w"]) for w in hook]
    s0 = segs[0]
    k = None
    for mark in [s0.get("lands")] + list(reversed([e for e in (s0.get("emphasis") or []) if isinstance(e, str)])):
        k = _find(spoken, mark) if mark else None
        if k is not None:
            break
    if k is None and hook:
        k = len(hook) - 1
    land = round(float(hook[k]["end"]) - t_first, 2) if hook else None
    cl = round(float(close[-1]["end"]) - float(close[0]["start"]), 2) if close and last_i > 0 else None
    talk = sum(float(w["end"]) - float(w["start"]) for w in ws)
    span = float(ws[-1]["end"]) - t_first
    wps = round(len(ws) / span, 3) if span > 0 else None
    over = {}
    if land is not None and land > hook_s:
        over["hook"] = dict(s=land, budget=hook_s, word=hook[k]["w"])
    if cl is not None and cl > close_s:
        over["close"] = dict(s=cl, budget=close_s)
    return dict(hook_land_s=land, hook_word=hook[k]["w"] if hook else None, close_s=cl, wps=wps,
                talk_share=round(talk / span, 3) if span > 0 else None, over=over)


def overage(m):
    """Seconds over budget, summed over the measured beats (0 when every beat fits)."""
    return round(sum(max(0.0, float(v["s"]) - float(v["budget"])) for v in (m.get("over") or {}).values()), 2)
