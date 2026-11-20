"""The one SFX quality filter every sound path shares (owner, 15 Nov 2026: our sound was "terrible", the end sting "a
mountain fall").

score/sfx_quality.json carries lane STING-REF's library audit, measured with signals only (no listening model): every
flag per recording and the recordings no film may sound (a long rumbling or debris tail, clipped tops, an audible
encoder wall, a sound a phone cannot play, a class the references never use). This module is the only reader of that
verdict for choosing and playing:

- the worker's sound design (score/sound.py) for its own picks and for the workflow's sounds it passes through;
- the four workflow skills' own choices (podcast shorts, stories, marketing, long-form) and their mixers;
- the scene engine's mixer (scene/audio.py), the last place a plan's sound becomes samples;
- the editor's tools: list_sound_library never lists a flagged recording, add_sfx resolves one to its replacement,
  and a template cue's role pick never lands on one (sound_library.playable / pick / search).

A flagged recording is never silently kept and never silently dropped: it is replaced by its nearest unflagged
recording on the audit's own ruler (score/sfx_quality.json `sting.library`: attack, body, tail to -30 dB, centroid,
share under 150 Hz, noisiness, flatness; times, frequency and flatness on log scales, each dimension scaled by the
library's own robust spread), from the same role when that role keeps a clean recording (the gesture the caller asked
for stays the same kind), never an object sound of another family (a camera, paper, a till) and never a typing bed for a
one-shot. Pure Python: the skills load it from the worker beside them (load()).
"""
import importlib.util
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
QUALITY = os.path.join(HERE, "score", "sfx_quality.json")
LIB_DIR = os.path.join(HERE, "sound_library")
# the audit's ruler: (feature, log scale floor or None for a share)
RULER = (("attack_ms", 5.0), ("body_s", 0.01), ("tail30_any_s", 0.02), ("centroid_hz", 50.0),
         ("low_share_lt150", None), ("noisiness", None), ("flatness", 1e-4))

_cache = {}


def _stamp(p):
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return None


def _data(lib_dir=None):
    """(audit, manifest by id) for a library directory, re-read when either file changes."""
    lib_dir = lib_dir or os.environ.get("VALMERA_SOUNDS") or LIB_DIR
    man_p = os.path.join(lib_dir, "manifest.json")
    key = (lib_dir, _stamp(QUALITY), _stamp(man_p))
    got = _cache.get(lib_dir)
    if got and got[0] == key:
        return got[1]
    try:
        q = json.load(open(QUALITY))
    except (OSError, ValueError):
        q = {}
    try:
        man = {r["id"]: r for r in json.load(open(man_p)) if r.get("id") and r.get("file")}
    except (OSError, ValueError):
        man = {}
    d = dict(q=q, man=man, lib_dir=lib_dir, rep={})
    _cache[lib_dir] = (key, d)
    return d


def flagged(lib_dir=None):
    """The recordings no film sounds (the audit's `excluded`)."""
    return frozenset((_data(lib_dir)["q"].get("excluded") or {}))


def is_flagged(sid, lib_dir=None):
    return id_of(sid) in flagged(lib_dir)


def why(sid, lib_dir=None):
    """The audit's reasons a recording is out ([] when it plays)."""
    return list((_data(lib_dir)["q"].get("excluded") or {}).get(id_of(sid)) or [])


def id_of(name):
    """A library id from an id, a file name or a path ('impact_1', 'impact_1.flac', '/x/impact_1.flac')."""
    if not name:
        return name
    b = os.path.basename(str(name))
    return b[:-5] if b.endswith(".flac") else b


def _vec(row):
    out = []
    for k, floor in RULER:
        v = row.get(k)
        if v is None:
            out.append(None)
        elif floor is None:
            out.append(float(v))
        else:
            out.append(math.log10(max(float(v), floor)))
    return out


def _scale(rows):
    """Each dimension's robust spread over the library ((p90 - p10) / 2.56, a robust sd)."""
    sc = []
    for i in range(len(RULER)):
        v = sorted(r[i] for r in rows if r[i] is not None)
        if len(v) < 3:
            sc.append(1.0)
            continue
        lo, hi = v[int(0.1 * (len(v) - 1))], v[int(math.ceil(0.9 * (len(v) - 1)))]
        sc.append(max((hi - lo) / 2.56, 1e-3))
    return sc


def distance(a, b, lib_dir=None):
    """The audit's distance between two recordings (None when either was not measured)."""
    d = _data(lib_dir)
    L = (d["q"].get("sting") or {}).get("library") or {}
    if a not in L or b not in L:
        return None
    vecs = {k: _vec(r) for k, r in L.items()}
    sc = _scale(list(vecs.values()))
    return _dist(vecs[a], vecs[b], sc)


def _dist(va, vb, sc):
    s, n = 0.0, 0
    for x, y, k in zip(va, vb, sc):
        if x is None or y is None:
            continue
        s += ((x - y) / k) ** 2
        n += 1
    return math.sqrt(s * len(RULER) / n) if n else float("inf")


def ranking(sid, lib_dir=None):
    """Every unflagged recording that may stand in for `sid`, nearest first: [(distance, id)]. Same role first when the
    role keeps one; never a flagged one, one whose file changed since it was measured (another sha), a typing bed for a
    one-shot, or an object sound of another family."""
    d = _data(lib_dir)
    L = (d["q"].get("sting") or {}).get("library") or {}
    man, bad = d["man"], flagged(lib_dir)
    me = man.get(sid) or {}
    vecs = {k: _vec(r) for k, r in L.items()}
    if sid not in vecs:
        return []
    sc = _scale(list(vecs.values()))
    cands = []
    for k, r in L.items():
        x = man.get(k)
        if (k == sid or k in bad or not x or r.get("sha") != x.get("sha")
                or not os.path.exists(os.path.join(d["lib_dir"], x["file"]))):
            continue
        if (x.get("align") == "start") != (me.get("align") == "start"):
            continue
        if x.get("family") and x.get("family") != me.get("family"):
            continue
        cands.append((round(_dist(vecs[sid], vecs[k], sc), 4), k, x.get("role") == me.get("role")))
    same = [c for c in cands if c[2]]
    pool = same or cands
    return sorted(((c[0], c[1]) for c in pool), key=lambda c: (c[0], c[1]))


def nearest(sid, lib_dir=None):
    """The recording that plays for `sid`: itself when it is not flagged, else its nearest unflagged recording (None
    only when the library has no candidate at all)."""
    sid = id_of(sid)
    if sid not in flagged(lib_dir):
        return sid
    d = _data(lib_dir)
    if sid not in d["rep"]:
        r = ranking(sid, lib_dir)
        d["rep"][sid] = r[0][1] if r else None
    return d["rep"][sid]


clean = nearest


def replacements(lib_dir=None):
    """{flagged id: the recording that plays instead}."""
    return {s: nearest(s, lib_dir) for s in sorted(flagged(lib_dir))}


def playable(rows, lib_dir=None, key="id"):
    """The rows (manifest rows, or dicts with `key`) whose recording is not flagged."""
    bad = flagged(lib_dir)
    return [r for r in rows if id_of(r.get(key)) not in bad]


def row(sid, lib_dir=None):
    """A recording's manifest row: from `lib_dir`, else (a set that does not carry it: a plan designed on the product
    library before the run switched to a set) from the product library, so a sound already placed resolves."""
    sid = id_of(sid)
    got = _data(lib_dir)["man"].get(sid)
    if got is None and _data(lib_dir)["lib_dir"] != LIB_DIR:
        got = _data(LIB_DIR)["man"].get(sid)
    return got


def _hit(x):
    return float(x.get("hit_s") or x.get("peak_s") or 0.0) if x.get("align") != "start" else 0.0


def swap(item, lib_dir=None):
    """A plan's sound item ({s, file, event_f, start_s, trim_s, db, peak_s, dur_s}) with a flagged recording replaced:
    the replacement's hit lands where the old one's did (the same lead the item gave it), at the old hit's loudness
    (the library's measured hit_lufs), stopped at its own measured tail. Returns (item, changed). A voice layer (echo,
    swell), a sound outside the library or an unflagged one comes back as it was."""
    if not isinstance(item, dict):
        return item, False
    sid = id_of(item.get("s")) if item.get("s") not in (None, "echo", "swell") else None
    if not sid or row(sid, lib_dir) is None:
        sid = id_of(item.get("file")) if item.get("file") else None
    if not sid or sid not in flagged(lib_dir):
        return item, False
    new = nearest(sid, lib_dir)
    old_x, new_x = row(sid, lib_dir), row(new, lib_dir) if new else None
    if not new_x:
        return item, False
    if old_x is None:                                          # measured nowhere: the item's own peak is the old hit
        old_x = {"peak_s": item.get("peak_s"), "hit_s": item.get("peak_s")}
    start, trim = float(item.get("start_s") or 0.0), float(item.get("trim_s") or 0.0)
    peak = float(item["peak_s"]) if item.get("peak_s") is not None else _hit(old_x)
    t_hit = start - trim + peak                                # the programme second the old hit landed on
    old_peak = float(old_x.get("peak_s") or 0.0)
    if abs(peak - old_peak) < 1e-3:                            # the caller placed by the file's peak
        new_peak = float(new_x.get("peak_s") or 0.0)
    else:                                                      # by its hit, with a lead (score/sound.py's LEAD_S)
        new_peak = _hit(new_x) + (peak - _hit(old_x))
    s0 = t_hit - new_peak
    out = dict(item, s=new, file=new_x["file"], start_s=round(max(s0, 0.0), 4), trim_s=round(max(-s0, 0.0), 4),
               peak_s=round(new_peak, 4), replaced=dict(id=sid, why=why(sid, lib_dir)))
    if out.get("db") is not None and old_x.get("hit_lufs") is not None and new_x.get("hit_lufs") is not None:
        out["db"] = round(float(out["db"]) + float(old_x["hit_lufs"]) - float(new_x["hit_lufs"]), 2)
    if new_x.get("max_s") and float(new_x["max_s"]) - out["trim_s"] >= 0.05:
        out["dur_s"] = round(float(new_x["max_s"]) - out["trim_s"], 3)
    else:
        out.pop("dur_s", None)
    return out, True


def swap_all(items, lib_dir=None):
    """swap() over a plan's sound list: (items, [{id, by}] replaced)."""
    out, done = [], []
    for s in items or []:
        t, ch = swap(s, lib_dir)
        out.append(t)
        if ch:
            done.append(dict(id=t["replaced"]["id"], by=t["s"], at=t.get("event_f")))
    return out, done


def facts(ids, measure, lib_dir=None):
    """A skill's sound table: {asked id: facts of the recording that plays for it} with `id` the recording and
    `asked` the name the skill used. measure(path) -> extra facts (the skill's own peak level). A flagged id's facts are
    its replacement's, never its own."""
    d = _data(lib_dir)
    out = {}
    for a in ids:
        sid = nearest(a, lib_dir)
        x = d["man"].get(sid) if sid else None
        if not x:
            continue
        p = os.path.join(d["lib_dir"], x["file"])
        f = dict(file=x["file"], peak_s=float(x.get("peak_s") or 0.0), path=p, id=sid, asked=a)
        f.update(measure(p) or {})
        out[a] = f
    return out


_loaded = {}


def load(worker):
    """This module as the worker at `worker` has it (the skills read the worker beside them): its own audit and
    library, whatever else is on sys.path."""
    p = os.path.join(worker, "sfx_filter.py")
    if p not in _loaded:
        if os.path.abspath(p) == os.path.abspath(__file__):
            import sys
            _loaded[p] = sys.modules[__name__]
        else:
            spec = importlib.util.spec_from_file_location(f"sfx_filter_{abs(hash(p))}", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            _loaded[p] = m
    return _loaded[p]
