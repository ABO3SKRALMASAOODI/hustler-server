"""RhythmMap: one shape for every clock a device can land on.

Track 0B (ROADMAP Wave 0; the shape is pinned in worker/devices/INTERFACES.md
§4.6). A RhythmMap is either a MUSIC map (a user's track: beats, downbeats,
drops, gaps — clock ``media``, seconds into the audio asset) or a SPEECH map
(the prosody grid: word, stress, syllable, phrase-end and pause-end onsets —
clock ``source``, seconds of the original upload). Devices store a binding
into one (``FxBinding``: ``{on, ref, rhythm, offset_f}``); ``to_program``
moves a map onto the edit's program clock and ``resolve_bind`` turns a
binding back into a program second, which is what lets a song swap, a recut
or a new aspect re-solve every device instead of leaving it on stale pixels
(ROADMAP A4).

Producers in Wave 0:
  * ``from_beat_grid`` — the existing ``perception.analyze_audio`` sidecar of
    a music asset (beats + its energy envelope for gaps and drops);
  * ``prosody_grid.build`` — the speech prosody grid, the default clock of a
    talking head with no track.

Rules (pinned): maps stay at or under 60 KB (``compact``); low confidence
means onsets only — a producer never invents a grid. Pure Python: no worker
imports, so the backend and the agent lanes can read maps too.
"""

import json
import math

VERSION = 1
MAX_BYTES = 60_000

SOURCES = ("music", "speech")
CLOCKS = ("media", "source")
# A map moved onto an edit (``to_program``) is a derived, unstored product.
PROGRAM_CLOCK = "program"

MUSIC_ONSETS = ("kick", "snare", "hat", "perc", "transient")
SPEECH_ONSETS = ("word", "stress", "syllable", "phrase_end", "pause_end")
MUSIC_SECTIONS = ("intro", "verse", "build", "drop", "break", "outro")
SPEECH_SECTIONS = ("phrase",)

# Below this a music map keeps its beats but claims no downbeats (meter).
DOWNBEAT_MIN_CONF = 0.6
# The energy envelope's gap and drop detection (from_beat_grid).
GAP_DB = -30.0              # a bin this far below the track peak is silence
GAP_MIN_S = 0.5
DROP_RISE_DB = 12.0         # the bins after a gap come back this much louder


def empty(source, *, clock=None, ref=None, producer=None):
    """A valid map with nothing on it (confidence 0)."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    return {"version": VERSION, "source": source,
            "clock": clock or ("media" if source == "music" else "source"),
            "ref": {"storage_key": None, "sha256": None, "music_id": None,
                    **(ref or {})},
            "producer": producer or "unknown@0",
            "bpm": None, "beats": [], "downbeats": [], "onsets": [],
            "sections": [], "drops": [], "gaps": [], "confidence": 0.0}


def _times(name, values):
    out = []
    for v in values or []:
        t = float(v)
        if not math.isfinite(t) or t < 0:
            raise ValueError(f"{name}: times are finite seconds >= 0")
        out.append(t)
    if out != sorted(out):
        raise ValueError(f"{name} must be sorted")
    return out


def validate(rmap):
    """Raise ValueError unless ``rmap`` has the pinned shape; returns it."""
    if not isinstance(rmap, dict):
        raise ValueError("a RhythmMap is a dict")
    if rmap.get("version") != VERSION:
        raise ValueError(f"version must be {VERSION}")
    src = rmap.get("source")
    if src not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if rmap.get("clock") not in CLOCKS + (PROGRAM_CLOCK,):
        raise ValueError(f"clock must be one of {CLOCKS + (PROGRAM_CLOCK,)}")
    if not isinstance(rmap.get("ref"), dict):
        raise ValueError("ref must be a dict")
    if not isinstance(rmap.get("producer"), str) or "@" not in rmap["producer"]:
        raise ValueError("producer must be '<name>@<version>'")
    bpm = rmap.get("bpm")
    if bpm is not None and not (0 < float(bpm) < 400):
        raise ValueError("bpm must be None or in (0, 400)")
    _times("beats", rmap.get("beats"))
    _times("downbeats", rmap.get("downbeats"))
    _times("drops", rmap.get("drops"))
    classes = MUSIC_ONSETS if src == "music" else SPEECH_ONSETS
    prev = -1.0
    for o in rmap.get("onsets") or []:
        if o.get("cls") not in classes:
            raise ValueError(f"onset class {o.get('cls')!r} is not a "
                             f"{src} class {classes}")
        t, st = float(o.get("t")), float(o.get("strength"))
        if t < prev - 1e-9:
            raise ValueError("onsets must be sorted by t")
        if not 0.0 <= st <= 1.0:
            raise ValueError("onset strength is 0..1")
        prev = t
    labels = MUSIC_SECTIONS if src == "music" else SPEECH_SECTIONS
    for sec in rmap.get("sections") or []:
        if sec.get("label") not in labels:
            raise ValueError(f"section label {sec.get('label')!r} is not a "
                             f"{src} label {labels}")
        if not float(sec["t0"]) < float(sec["t1"]):
            raise ValueError("a section needs t0 < t1")
        if not 0.0 <= float(sec.get("energy", 0.0)) <= 1.0:
            raise ValueError("section energy is 0..1")
    for g in rmap.get("gaps") or []:
        if not float(g["t0"]) < float(g["t1"]):
            raise ValueError("a gap needs t0 < t1")
    if not 0.0 <= float(rmap.get("confidence", -1)) <= 1.0:
        raise ValueError("confidence is 0..1")
    if size_bytes(rmap) > MAX_BYTES:
        raise ValueError(f"a RhythmMap must stay under {MAX_BYTES} bytes")
    return rmap


def size_bytes(rmap):
    return len(json.dumps(rmap, separators=(",", ":")).encode("utf-8"))


def _round(rmap):
    r = dict(rmap)
    for k in ("beats", "downbeats", "drops"):
        r[k] = [round(float(t), 3) for t in r.get(k) or []]
    r["onsets"] = [{"t": round(float(o["t"]), 3), "cls": o["cls"],
                    "strength": round(float(o["strength"]), 2)}
                   for o in r.get("onsets") or []]
    r["sections"] = [{**s, "t0": round(float(s["t0"]), 3),
                      "t1": round(float(s["t1"]), 3),
                      "energy": round(float(s.get("energy", 0.0)), 2)}
                     for s in r.get("sections") or []]
    r["gaps"] = [{"t0": round(float(g["t0"]), 3),
                  "t1": round(float(g["t1"]), 3)} for g in r.get("gaps") or []]
    if r.get("bpm") is not None:
        r["bpm"] = round(float(r["bpm"]), 2)
    r["confidence"] = round(float(r.get("confidence") or 0.0), 3)
    return r


# Onset classes dropped first when a map is over budget (least load-bearing
# first); the device-critical ones (stress, kick, snare) go last.
_DROP_ORDER = ("syllable", "hat", "word", "perc", "transient", "pause_end",
               "phrase_end", "snare", "kick", "stress")


def compact(rmap, max_bytes=MAX_BYTES):
    """``rmap`` rounded (ms, centi-strength) and, if still over budget,
    thinned: whole onset classes leave in _DROP_ORDER, then the weakest
    onsets, until it fits. Beats, sections, drops and gaps are kept."""
    r = _round(rmap)
    if size_bytes(r) <= max_bytes:
        return r
    for cls in _DROP_ORDER:
        r["onsets"] = [o for o in r["onsets"] if o["cls"] != cls]
        if size_bytes(r) <= max_bytes:
            return r
    while r["onsets"] and size_bytes(r) > max_bytes:
        weakest = min(range(len(r["onsets"])),
                      key=lambda i: r["onsets"][i]["strength"])
        r["onsets"].pop(weakest)
    return r


# ------------------------------------------------------------------ music
def from_beat_grid(perception, *, ref=None, version=None):
    """A music map from ``perception.analyze_audio`` output (asset meta
    ``perception``). Beats come from its phase-locked grid; gaps are runs of
    energy bins GAP_DB below the peak lasting GAP_MIN_S; a drop is a gap end
    whose next bins rise DROP_RISE_DB. Downbeats need a confident meter and
    the sidecar carries none, so a Wave 0 map leaves them empty (2C adds a
    downbeat model). Confidence is the tempo confidence."""
    p = perception or {}
    v = version if version is not None else p.get("v", 0)
    out = empty("music", ref=ref, producer=f"perception.beat_grid@{v}")
    conf = float(p.get("bpm_conf") or 0.0)
    beats = [float(b) for b in p.get("beats") or []]
    if beats and conf >= 0.3:
        out["beats"] = sorted(beats)
        out["bpm"] = float(p["bpm"]) if p.get("bpm") else None
    energy = [float(e) for e in p.get("energy") or []]
    bin_s = float(p.get("energy_bin_s") or 0.5)
    gaps, drops = [], []
    i = 0
    while i < len(energy):
        if energy[i] <= GAP_DB:
            j = i
            while j < len(energy) and energy[j] <= GAP_DB:
                j += 1
            t0, t1 = i * bin_s, j * bin_s
            if t1 - t0 >= GAP_MIN_S - 1e-9 and i > 0:
                gaps.append({"t0": t0, "t1": t1})
                after = energy[j:j + 2]
                if after and max(after) - energy[j - 1] >= DROP_RISE_DB \
                        and max(after) >= -12.0:
                    drops.append(t1)
            i = j
        else:
            i += 1
    out["gaps"], out["drops"] = gaps, drops
    out["confidence"] = conf if out["beats"] else min(conf, 0.3)
    return compact(out)


# ------------------------------------------------------------------ program
def to_program(rmap, timeline, music_item=None, track_dur=None):
    """The map on the edit's PROGRAM clock (a derived copy, clock
    ``program``); times that fall outside the program are dropped.

    speech (clock ``source``): through ``timeline`` (a timeline.Timeline:
    ``src_to_out`` / ``span_to_out``) — cut-away words vanish, spans split
    at cuts. music (clock ``media``): through ``music_item`` — program t =
    start + (media t - offset_s), inside [start, end); a looping item
    (``loop``) repeats the map every ``track_dur`` seconds when that is
    known."""
    out = {**rmap, "clock": PROGRAM_CLOCK}
    if rmap.get("source") == "speech":
        prog_len = float(getattr(timeline, "out_duration", 0.0) or 0.0)

        def points(t):
            x = timeline.src_to_out(float(t))
            return [] if x is None else [x]

        def spans(t0, t1):
            return [(a, b) for a, b in timeline.span_to_out(float(t0),
                                                             float(t1))
                    if b > a]
    else:
        m = music_item or {}
        start = float(m.get("start") or 0.0)
        end = (float(m["end"]) if m.get("end") is not None
               else float(getattr(timeline, "out_duration", 0.0) or 1e9))
        off = float(m.get("offset_s") or 0.0)
        period = (float(track_dur) if m.get("loop") and track_dur
                  and float(track_dur) > 0 else None)
        prog_len = end

        def points(t):
            hits, k = [], 0
            while k <= 10000:
                pt = start + (float(t) - off) + (k * period if period else 0.0)
                if pt >= end - 1e-9:
                    break
                if pt >= start - 1e-9:
                    hits.append(pt)
                if not period:
                    break
                k += 1
            return hits

        def spans(t0, t1):
            return [(a, min(a + float(t1) - float(t0), end))
                    for a in points(t0) if min(a + float(t1) - float(t0),
                                               end) > a]
    out["beats"] = sorted(x for t in rmap.get("beats") or []
                          for x in points(t))
    out["downbeats"] = sorted(x for t in rmap.get("downbeats") or []
                              for x in points(t))
    out["drops"] = sorted(x for t in rmap.get("drops") or []
                          for x in points(t))
    out["onsets"] = sorted(({**o, "t": x} for o in rmap.get("onsets") or []
                            for x in points(o["t"])), key=lambda o: o["t"])
    out["sections"] = sorted(({**sec, "t0": a, "t1": b}
                              for sec in rmap.get("sections") or []
                              for a, b in spans(sec["t0"], sec["t1"])),
                             key=lambda sec: sec["t0"])
    out["gaps"] = sorted(({"t0": a, "t1": b} for g in rmap.get("gaps") or []
                          for a, b in spans(g["t0"], g["t1"])),
                         key=lambda g: g["t0"])
    if prog_len:
        for k in ("beats", "downbeats", "drops"):
            out[k] = [t for t in out[k] if t <= prog_len + 1e-6]
        out["onsets"] = [o for o in out["onsets"]
                         if o["t"] <= prog_len + 1e-6]
    return out


# ------------------------------------------------------------------ bindings
_LIST_FOR = {"beat": "beats", "downbeat": "downbeats", "drop": "drops",
             "section": "sections", "gap": "gaps", "onset": "onsets"}


def resolve_bind(bind, *, fps, timeline=None, words=None, maps=None):
    """The PROGRAM second a binding names (INTERFACES.md §4.1), or None when
    its moment is no longer in the edit.

    bind     {on, ref, rhythm, offset_f} (FxBinding.model_dump())
    words    the index transcript (source-clock words with t0/t1), for
             word / syllable / stress / phrase_end
    maps     {rhythm id: PROGRAM-clock RhythmMap} ("speech" or a music id)
             for beat / downbeat / onset / section / drop / gap

    ``offset_f`` whole frames are added on the render's frame grid."""
    b = bind if isinstance(bind, dict) else dict(bind)
    on, ref = b.get("on"), b.get("ref")
    t = None
    if on == "time":
        t = float(ref)
    elif on in ("word", "syllable", "stress", "phrase_end"):
        if words is None or timeline is None or ref is None:
            return None
        i = int(ref)
        if not 0 <= i < len(words):
            return None
        w = words[i]
        src = float(w["t1"] if on == "phrase_end" else w["t0"])
        t = timeline.src_to_out(src - 1e-4 if on == "phrase_end" else src)
        if t is not None and on == "phrase_end":
            t += 1e-4
    elif on == "cut":
        if timeline is None or ref is None:
            return None
        t = timeline.src_to_out(float(ref))
    elif on in _LIST_FOR:
        rmap = (maps or {}).get(b.get("rhythm") or "speech")
        if rmap is None or ref is None:
            return None
        seq = rmap.get(_LIST_FOR[on]) or []
        i = int(ref)
        if not 0 <= i < len(seq):
            return None
        item = seq[i]
        t = float(item["t"] if on == "onset" else
                  item["t0"] if on in ("section", "gap") else item)
    if t is None:
        return None
    f3 = round(float(fps), 3)
    frame = math.floor(t * f3 + 0.5 + 1e-9) + int(b.get("offset_f") or 0)
    return max(0.0, frame / f3)


def nearest(seq, t, max_dist=None):
    """Index of the time in sorted ``seq`` nearest ``t`` (None if empty or
    farther than ``max_dist``)."""
    if not seq:
        return None
    best = min(range(len(seq)), key=lambda i: abs(float(seq[i]) - float(t)))
    if max_dist is not None and abs(float(seq[best]) - float(t)) > max_dist:
        return None
    return best
