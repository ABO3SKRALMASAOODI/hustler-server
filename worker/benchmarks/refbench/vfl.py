"""The Valmera Footage Library (ROADMAP §4.2, INTERFACES §8.4).

``benchmarks/footage/vfl.json`` (in git) names every window a test uses; the
trimmed files live in the fixture store (never in git):

    <fixtures>/<VFL id>/<window>.mp4

Files are STREAM COPIES of the source (no generation loss, a few MB per
minute), cut at the keyframe at or before ``t[0] - handle_s`` so junction
devices have source handles on both sides. Each window records the clock
mapping, verified frame-exact at build time:

    source_time = file_time + file_t0      (file_time = the fixture's PTS)

    python -m benchmarks.refbench vfl build   [--only V02] [--sources DIR]
    python -m benchmarks.refbench vfl verify  (sha256 of every fixture)
    python -m benchmarks.refbench vfl list
"""

import hashlib
import json
import math
import os
import shutil
import subprocess

from . import VFL_JSON, fixtures_dir

SOURCES_ROOT = os.path.expanduser(
    "~/Documents/Valmera/.tmp/valmera-podcast-shorts")
HANDLE_S = 2.0


def load(path=VFL_JSON):
    with open(path) as fh:
        return json.load(fh)


def _dumps(data):
    """Indented JSON with every list of scalars on one line."""
    import re
    text = json.dumps(data, indent=1, ensure_ascii=False)

    def flat(m):
        items = [x.strip() for x in m.group(1).split(",")]
        return "[" + ", ".join(items) + "]"
    return re.sub(r"\[\s*((?:[^\[\]{}]|\n)*?)\s*\]", flat, text)


def save(data, path=VFL_JSON):
    with open(path, "w") as fh:
        fh.write(_dumps(data) + "\n")


def sources(data=None):
    data = data or load()
    return {k: v for k, v in data.items() if not k.startswith("_")}


def window(vfl_id, name, data=None):
    """The window record, with ``path`` (absolute fixture path) added."""
    src = sources(data)[vfl_id]
    w = dict(src["windows"][name])
    w["path"] = fixture_path(vfl_id, name, data)
    w["vfl"] = vfl_id
    w["name"] = name
    return w


def fixture_path(vfl_id, name, data=None):
    src = sources(data)[vfl_id]
    rel = src["windows"][name].get("file") or f"{vfl_id}/{name}.mp4"
    return os.path.join(fixtures_dir(), rel)


def to_file_time(w, t_source):
    return float(t_source) - float(w["file_t0"])


def _frame_at(w, x):
    """Index of the first fixture frame whose PTS >= fixture time x (the
    frame a trim boundary at x starts on; constant frame rate: fixture
    frame k has PTS file_start + k / fps)."""
    fps = float(w["fps"])
    f0 = float(w.get("file_start", 0.0))
    return max(0, math.ceil((float(x) - f0) * fps - 1e-6))


def clock_q(w, edl_times=(), *, quantum=0.01):
    """(Q, nudged): the 0.01-multiple clock offset for moving SOURCE-clock
    EDL boundaries to this fixture, and the boundaries (source seconds)
    that still need a nudge.

    The renderer trims at the first frame whose PTS >= the boundary and an
    EDL stores times to 0.01 s, so the shift must be a 0.01 multiple (a
    plain ``t - file_t0`` is re-rounded by validation and can move a cut
    across a frame -- measured on V04, file_t0 110.8056: keep 130.80 became
    19.99 and the segment started one frame early) and every boundary must
    still start on the frame it starts on in the source. Shifting EVERY
    time by the same Q keeps every duration, so program time is identical.
    Of the two candidates around file_t0, Q is the one under which fewer
    boundaries change frame; ``edl_to_fixture`` nudges those by 0.01."""
    t0 = float(w["file_t0"])
    q0 = math.floor(t0 / quantum + 1e-9) * quantum
    best = None
    for q in (q0, q0 + quantum):
        bad = [t for t in edl_times
               if _frame_at(w, round(t - q, 2)) != _frame_at(w, t - t0)]
        if best is None or len(bad) < len(best[1]):
            best = (round(q, 6), bad)
    return best


def file_time(w, t_source, q=None):
    """A source-clock EDL boundary on the fixture clock, starting on the
    same frame as in the source (see ``clock_q``)."""
    if q is None:
        q = clock_q(w, [t_source])[0]
    x = round(float(t_source) - q, 2)
    want = _frame_at(w, float(t_source) - float(w["file_t0"]))
    for cand in (x, round(x + 0.01, 2), round(x - 0.01, 2)):
        if _frame_at(w, cand) == want:
            return max(0.0, cand)
    return max(0.0, x)


# renderer.render_edl seeks the main input with -copyts (container clock)
# when the first keep minus its 1 s preroll is >= 5 s; otherwise it reads
# the input from 0 on a clock shifted to the earliest stream's start.
COPYTS_FROM_S = 5.0 + 1.0


def restamped(w, q):
    """A stream copy of the window's fixture whose timestamps are moved by
    (file_t0 - q) >= 0, so that source time = PTS + q EXACTLY (q a 0.01
    multiple). Cached beside the refbench output (never the fixture store).
    Valid only for renders that read the container clock (``-copyts``):
    a plain read re-zeroes the clock."""
    import tempfile
    d = float(w["file_t0"]) - float(q)
    if d < -1e-9:
        raise ValueError("restamped needs q <= file_t0")
    cache = os.path.join(tempfile.gettempdir(), "valmera-refbench-restamp")
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, f"{w['sha256'][:16]}_{d:.6f}.mp4")
    if not os.path.exists(out):
        tmp = out + ".part.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", w["path"],
             "-map", "0", "-c", "copy", "-output_ts_offset", f"{d:.6f}",
             "-movflags", "+faststart", tmp], check=True)
        os.replace(tmp, out)
    return out


def render_source(w, edl):
    """(edl_on_fixture, source_path, q, nudged, mode) for rendering a
    SOURCE-clock EDL on this window's fixture so that it cuts the same
    frames for the same durations as the source would:

      copyts  the first keep is >= ``COPYTS_FROM_S`` into the fixture, so
              the renderer seeks and reads the container clock: every time
              moves by q = floor(file_t0, 0.01) and the input is
              ``restamped`` by (file_t0 - q) < 10 ms (well inside the
              renderer's 1 s seek preroll): exact, nothing nudged;
      plain   otherwise the renderer reads from 0 on the fixture's own
              clock: ``edl_to_fixture`` (a boundary may be nudged by
              0.01 s; listed)."""
    keeps = [a for a, _b in edl.get("keep") or []]
    q0 = math.floor(float(w["file_t0"]) * 100 + 1e-9) / 100.0
    if keeps and min(keeps) - q0 >= COPYTS_FROM_S:
        e, _q, _bad = edl_to_fixture(w, edl, q=q0)
        return e, restamped(w, q0), q0, [], "copyts"
    e, q, bad = edl_to_fixture(w, edl)
    return e, w["path"], q, bad, "plain"


def edl_to_fixture(w, edl, q=None):
    """(edl, q, nudged): a SOURCE-clock EDL moved onto the fixture clock,
    frame-exact. Every source-clock field moves by one 0.01-multiple Q
    (``clock_q``, or the given ``q`` for a ``restamped`` input, where
    nothing needs a nudge); a cut boundary that would still change frame is
    nudged by 0.01 (its segment then lasts 10 ms more or less; listed in
    ``nudged``). Program-clock fields (texts, motion, inserts, sfx, music,
    captions) are untouched."""
    import copy
    e = copy.deepcopy(edl)
    cuts = []
    for a, b in e.get("keep") or []:
        cuts += [a, b]
    cuts += list(e.get("split_keep_boundaries") or [])
    for sp in e.get("speed") or []:
        cuts += [sp["start"], sp["end"]]
    for pt in e.get("patches") or []:
        cuts += [pt[k] for k in ("start", "end") if k in pt]
    if q is None:
        q, bad = clock_q(w, cuts)
        badset = set(bad)
    else:
        badset = set()            # a restamped input: every cut is exact

    def cut(v):
        return file_time(w, v, q) if v in badset else \
            max(0.0, round(float(v) - q, 2))

    def sh(v):
        return round(float(v) - q, 4)
    e["keep"] = [[cut(a), cut(b)] for a, b in e.get("keep") or []]
    if e.get("split_keep_boundaries"):
        e["split_keep_boundaries"] = [cut(t) for t in
                                      e["split_keep_boundaries"]]
    fr = e.get("frame") or {}
    for seg in fr.get("follow") or []:
        seg["t0"], seg["t1"] = max(0.0, sh(seg["t0"])), sh(seg["t1"])
        seg["k"] = [[sh(k[0])] + list(k[1:]) for k in seg.get("k") or []]
    for seg in fr.get("focus_track") or []:
        seg["t0"], seg["t1"] = max(0.0, sh(seg["t0"])), sh(seg["t1"])
    for v in e.get("volume") or []:
        v["start"], v["end"] = cut(v["start"]), cut(v["end"])
    for sp in e.get("speed") or []:
        sp["start"], sp["end"] = cut(sp["start"]), cut(sp["end"])
    for layer in ("texts", "motion"):
        for it in e.get(layer) or []:
            bh = it.get("behind")
            if bh:
                bh["src_start"], bh["src_end"] = cut(bh["src_start"]), \
                    cut(bh["src_end"])
    for pt in e.get("patches") or []:
        for k in ("start", "end"):
            if k in pt:
                pt[k] = cut(pt[k])
    for ins in e.get("inserts") or []:
        rt = (ins.get("hold") or {}).get("room_tone")
        if rt:
            ins["hold"]["room_tone"] = [sh(rt[0]), sh(rt[1])]

    def span(sp):
        sp["t0"], sp["t1"] = max(0.0, sh(sp["t0"])), sh(sp["t1"])

    def follow_spans(spans):
        for sp in spans or []:
            span(sp)
            sp["k"] = [[sh(k[0])] + list(k[1:]) for k in sp.get("k") or []]
    for card in (e.get("effects") or {}).get("picture_cards") or []:
        follow_spans(card.get("follow"))
        for sp in card.get("source_track") or []:
            span(sp)
        for sp in card.get("cut_steps") or []:
            span(sp)
        for pn in card.get("panels") or []:
            follow_spans(pn.get("follow"))
    cap = e.get("captions") or {}
    if (e.get("keep") or []) and isinstance(cap.get("items"), list):
        for it in cap["items"]:
            it["start"], it["end"] = sh(it["start"]), sh(it["end"])
    sc = e.get("source_clean") or {}
    for rg in sc.get("regions") or []:
        for k in ("start", "end"):
            if rg.get(k) is not None:
                rg[k] = sh(rg[k])
    for cp in [sc.get("cursor")] + list(e.get("cursor_passes") or []):
        if isinstance(cp, dict) and cp.get("click_times"):
            cp["click_times"] = [sh(t) for t in cp["click_times"]]
    return e, q, sorted(badset)


def unshifted(w, edl_before, edl_after):
    """Numeric fields that still read like SOURCE times after the move: a
    value inside the window's source span that ``edl_to_fixture`` left
    unchanged. Returns their JSON paths (empty when the move is complete);
    program-clock values that happen to fall in the span are rare for a
    window minutes into its source, so a hit means a field to add above."""
    lo, hi = w["file_span"]
    out = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in a:
                if k in b:
                    walk(a[k], b[k], f"{path}.{k}")
        elif isinstance(a, list) and isinstance(b, list):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]")
        elif isinstance(a, (int, float)) and not isinstance(a, bool):
            if lo + 1.0 <= float(a) <= hi and a == b:
                out.append(path)
    walk(edl_before, edl_after, "edl")
    return out


def to_source_time(w, t_file):
    return float(t_file) + float(w["file_t0"])


def find(vfl_id, t0, t1, data=None):
    """The window of ``vfl_id`` that contains [t0, t1] (source seconds)."""
    for name, w in sources(data)[vfl_id]["windows"].items():
        a, b = w.get("file_span") or w["t"]
        if a - 1e-6 <= t0 and t1 <= b + 1e-6:
            return name
    return None


def sha256_file(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _keyframes(src, lo, hi):
    """Video keyframe PTS in [lo, hi] from packet flags (no decode)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-read_intervals", f"{max(0.0, lo):.3f}%{hi:.3f}",
         "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", src],
        capture_output=True, check=True).stdout.decode()
    ks = []
    for ln in out.splitlines():
        parts = ln.split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                ks.append(float(parts[0]))
            except ValueError:
                pass
    return sorted(set(ks))


def _keyframe_at_or_before(src, t):
    """PTS of the last video keyframe <= t (the first one when none is)."""
    ks = _keyframes(src, t - 30.0, t + 0.5)
    before = [k for k in ks if k <= t + 1e-6]
    if before:
        return before[-1]
    ks = ks or _keyframes(src, 0.0, t + 10.0)
    return ks[0] if ks else 0.0


def _duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True).stdout.decode()
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _stream_start(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=start_time", "-of", "csv=p=0", path],
        capture_output=True, check=True).stdout.decode().strip()
    try:
        return float(out.split(",")[0])
    except ValueError:
        return 0.0


def _frame_md5(path, t):
    """md5 of the first decoded frame with PTS >= t (absolute PTS: -copyts)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t - 3):.6f}",
           "-copyts", "-i", path, "-map", "0:v:0", "-vf",
           f"select=gte(t\\,{t:.6f})", "-frames:v", "1", "-f", "md5", "-"]
    return subprocess.run(cmd, capture_output=True).stdout.decode().strip()


def build_window(src, out, t0, t1, handle=HANDLE_S):
    """Stream-copy [kf(t0 - handle), t1 + handle] of ``src`` to ``out``.
    Returns the window's measured record."""
    kf = _keyframe_at_or_before(src, max(0.0, t0 - handle))
    head = t0 - handle <= 0.5          # the window starts with the file
    if head:
        kf = 0.0
    dur = (t1 + handle) - kf
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part.mp4"
    seek = [] if head else ["-ss", f"{kf:.6f}"]
    whole = head and t1 + handle >= _duration(src)
    if whole:
        # the window is the whole file: the fixture IS the file (a remux
        # of a clip with odd edit lists can shift its streams)
        shutil.copyfile(src, tmp)
    else:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-y"] + seek +
            ["-i", src, "-t", f"{dur:.6f}", "-map", "0:v:0", "-map",
             "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
             "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, out)
    start = _stream_start(out)
    # Stream-copy seeking can land on an EARLIER sync sample than the
    # keyframe asked for (an mp4 whose sync table disagrees with the packet
    # flags), so the clock is not assumed: each candidate keyframe's mapping
    # is checked frame-exact (md5 of the decoded frame 1 s into the window)
    # and the first that matches is recorded.
    probe_t = min(t0 + 1.0, t1) + 0.004
    a = _frame_md5(src, probe_t)
    file_t0, b = kf - start, None
    cands = [kf] + [k for k in reversed(_keyframes(src, kf - 30.0, kf))
                    if k < kf - 1e-6]
    if head:
        cands = [_stream_start(src)]
    if whole:
        cands, start = [0.0], 0.0
    for cand in cands:
        b = _frame_md5(out, probe_t - (cand - start))
        if a and a == b:
            file_t0 = cand - start
            break
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height,"
         "r_frame_rate", "-of", "json", out],
        capture_output=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    n, d = v["r_frame_rate"].split("/")
    s0 = file_t0 + start
    return {"file_t0": round(file_t0, 6), "file_start": round(start, 6),
            "file_span": [round(s0, 3),
                          round(s0 + float(info["format"]["duration"]), 3)],
            "bytes": int(info["format"]["size"]),
            "codec": v["codec_name"], "width": v["width"],
            "height": v["height"], "fps": round(float(n) / float(d), 5),
            "sha256": sha256_file(out),
            "aligned": bool(a and a == b),
            "has_audio": any(s["codec_type"] == "audio"
                             for s in info["streams"])}


def build(only=None, sources_root=SOURCES_ROOT, data=None, source_sha=True,
          log=print):
    data = data or load()
    for vid, src in sources(data).items():
        if only and vid not in only:
            continue
        for wname, w in src["windows"].items():
            spath = w.get("source") or src["source"]
            full = spath if os.path.isabs(spath) else \
                os.path.join(sources_root, spath)
            if not os.path.exists(full):
                log(f"{vid}/{wname}: source missing ({full})")
                continue
            w.setdefault("file", f"{vid}/{wname}.mp4")
            out = os.path.join(fixtures_dir(), w["file"])
            rec = build_window(full, out, w["t"][0], w["t"][1])
            if w.get("source") and source_sha and not w.get("source_sha256"):
                rec["source_sha256"] = sha256_file(full)
            w.update(rec)
            log(f"{vid}/{wname}: {rec['bytes'] / 1e6:.1f} MB "
                f"t0={rec['file_t0']} aligned={rec['aligned']}")
        if source_sha and not src.get("sha256"):
            spath = src["source"]
            full = spath if os.path.isabs(spath) else \
                os.path.join(sources_root, spath)
            if os.path.exists(full):
                src["sha256"] = sha256_file(full)
    return data


def verify(data=None):
    """{"<vfl>/<window>": "ok" | "missing" | "sha mismatch"}."""
    data = data or load()
    out = {}
    for vid, src in sources(data).items():
        for wname, w in src["windows"].items():
            p = fixture_path(vid, wname, data)
            key = f"{vid}/{wname}"
            if not os.path.exists(p):
                out[key] = "missing"
            elif not w.get("sha256"):
                out[key] = "unhashed"
            else:
                out[key] = "ok" if sha256_file(p) == w["sha256"] else \
                    "sha mismatch"
    return out


def provenance(data=None, sources_root=SOURCES_ROOT):
    """Write ``<fixtures>/<VFL id>/provenance.json`` for every source: where
    each window came from, its clock and hashes, and the usage rule. The
    fixture catalogue (CATALOG.md) expects one per VFL trim."""
    data = data or load()
    written = []
    for vid, src in sources(data).items():
        wins = {}
        for wname, w in src["windows"].items():
            wins[wname] = {k: w.get(k) for k in (
                "t", "file", "sha256", "file_t0", "file_start", "file_span",
                "fps", "width", "height", "has_audio", "aligned", "labels",
                "used_by", "source", "source_sha256") if w.get(k) is not None}
        rec = {"vfl": vid, "source": src.get("source"),
               "source_root": sources_root, "source_sha256": src.get("sha256"),
               "what": src.get("what"),
               "kind": "stream-copy trims of the owner's existing library "
                       "(ROADMAP 4.2); not internet-acquired",
               "licence": "third-party interview/event recordings already in "
                          "the owner's library; rights per the owner's "
                          "existing practice (ROADMAP 10)",
               "internal_only": True,
               "usage_rule": "internal capability tests only: never publish, "
                             "upload to product storage, commit to git or "
                             "ship in a render",
               "built_by": "track 0D (python -m benchmarks.refbench vfl "
                           "build)",
               "clock": "source_time = file_time + file_t0",
               "windows": wins}
        d = os.path.join(fixtures_dir(), vid)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "provenance.json")
        with open(p, "w") as fh:
            json.dump(rec, fh, indent=1)
        written.append(p)
    return written


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="refbench vfl")
    ap.add_argument("cmd", choices=["build", "verify", "list", "provenance"])
    ap.add_argument("--only", default=None)
    ap.add_argument("--sources", default=SOURCES_ROOT)
    ap.add_argument("--no-source-sha", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        only = set(a.only.split(",")) if a.only else None
        data = build(only, a.sources, source_sha=not a.no_source_sha)
        save(data)
        return 0
    if a.cmd == "provenance":
        for p in provenance():
            print(p)
        return 0
    if a.cmd == "verify":
        res = verify()
        bad = {k: v for k, v in res.items() if v != "ok"}
        for k, v in sorted(res.items()):
            print(f"{k:<40} {v}")
        return 1 if bad else 0
    for vid, src in sources().items():
        for wname, w in src["windows"].items():
            print(f"{vid}/{wname:<28} {w['t'][0]:>9.2f}-{w['t'][1]:<9.2f} "
                  f"{','.join(w.get('labels', []))}")
    return 0
