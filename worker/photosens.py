"""Photosensitivity on rendered frames, and the flash-safe mode (track 4F).

ROADMAP Wave 4 / 4F: "Harding-style analysis on renders; safe mode ... while
keeping downbeat hits". Two halves:

  analyse(frames | path, fps)   what the RENDER does: every general and red
                                flash (a pair of opposing Harding
                                transitions over >= 25 % of the screen,
                                qa_metrics.flash_transitions), counted in
                                every one-second window, with the windows
                                that pass the limit
  safe_plan(edl, ...)           what the EDIT should do instead: the flash
                                items thinned to <= the limit per second,
                                downbeat hits of the user's own track kept
                                first (flash_safety.plan with strength), as
                                the exact apply_edit_batch ``remove`` ops

The limit is ``config.FLASH_SAFE_MAX_PER_S`` (3). A user who asked for
strobing (flash_safety.opted_in) keeps everything; the export is then
flagged ``contains_flashing`` by the existing gate. Nothing here edits a
render: the fix is always an edit the agent (or a stale MCP client) writes.

Pure numpy over decoded frames; every lane (no browser, no model).
"""

import numpy as np

import flash_safety
import qa_metrics

PHOTOSENS_VERSION = 1
# Analysis raster: Harding's area rule is a share of the screen, so a small
# frame measures it as well as a large one (qa_metrics' 32x18 cell grid).
SIZE = (160, 90)
PORTRAIT_SIZE = (90, 160)


def _frames_of(path, fps=None, start_s=None, end_s=None):
    info = qa_metrics.probe(path)
    fps = float(fps or info.get("fps") or 30.0)
    w, h = info.get("width") or 16, info.get("height") or 9
    size = PORTRAIT_SIZE if h > w else SIZE
    f0 = None if start_s is None else int(round(float(start_s) * fps))
    f1 = None if end_s is None else int(round(float(end_s) * fps))
    idx, frames = qa_metrics.read_frames(path, size=size)
    if f0 is not None or f1 is not None:
        a = 0 if f0 is None else max(0, f0)
        b = len(frames) if f1 is None else min(len(frames), f1)
        frames = frames[a:b]
    return frames, fps


def analyse(frames=None, fps=None, *, path=None, start_s=None, end_s=None,
            max_per_s=None, opted_in=False):
    """{peak_per_s, red_peak_per_s, flashes: [{t, red}], over: [{t0, t1,
    count}], max_per_s, over_limit, contains_flashing, frames}.

    ``frames`` (RGB arrays) + ``fps``, or ``path`` (decoded at a small
    raster; ``start_s`` / ``end_s`` bound the program — the end card is
    not part of it). A flash is a PAIR of opposing transitions; one window
    of one second (``round(fps)`` frames) holding more than ``max_per_s``
    flashes is over the limit."""
    if frames is None:
        frames, fps = _frames_of(path, fps, start_s, end_s)
    fps = float(fps or 30.0)
    cap = int(flash_safety.default_max_per_s() if max_per_s is None
              else max_per_s)
    tr = qa_metrics.flash_transitions(frames) if len(frames) >= 2 else []
    # pair consecutive opposing transitions into flashes (a white hit is up
    # then down: one flash, landing at the first transition)
    flashes = []
    i = 0
    while i < len(tr):
        f, d, red = tr[i]
        if i + 1 < len(tr) and tr[i + 1][1] == -d:
            flashes.append({"f": int(f), "t": round(f / fps, 3),
                            "red": bool(red or tr[i + 1][2])})
            i += 2
        else:
            i += 1
    win = max(1, int(round(fps)))
    fr = [x["f"] for x in flashes]
    red = [x["f"] for x in flashes if x["red"]]

    def peak(fs):
        best, j = 0, 0
        for k in range(len(fs)):
            while fs[k] - fs[j] >= win:
                j += 1
            best = max(best, k - j + 1)
        return best
    over = []
    j = 0
    for k in range(len(fr)):
        while fr[k] - fr[j] >= win:
            j += 1
        if k - j + 1 > cap:
            t0, t1 = round(fr[j] / fps, 2), round((fr[k] + 1) / fps, 2)
            if over and t0 <= over[-1]["t1"]:
                over[-1]["t1"] = t1
                over[-1]["count"] = max(over[-1]["count"], k - j + 1)
            else:
                over.append({"t0": t0, "t1": t1, "count": k - j + 1})
    p, rp = peak(fr), peak(red)
    over_limit = p > cap or rp > 0 and rp > cap
    return {"version": PHOTOSENS_VERSION, "frames": len(frames),
            "fps": round(fps, 3), "peak_per_s": p, "red_peak_per_s": rp,
            "flashes": [{"t": x["t"], "red": x["red"]} for x in flashes],
            "over": over, "max_per_s": cap, "over_limit": bool(over_limit),
            "opted_in": bool(opted_in),
            "contains_flashing": bool(over_limit and opted_in)}


def downbeats_of(maps):
    """Program seconds of the downbeats of the USER's own tracks, from
    ``maps`` ({music id: PROGRAM-clock RhythmMap}, verify_tools.music_maps;
    only tracks the user supplied ever have one — no agent picks music to
    get a grid). [] without a track."""
    out = []
    for _mid, rmap in sorted((maps or {}).items()):
        try:
            out += [float(t) for t in (rmap or {}).get("downbeats") or []]
        except (TypeError, ValueError):
            continue
    return sorted(out)


def safe_plan(edl, index=None, *, request_text=None, fps=None,
              downbeats=None, maps=None, max_per_s=None, tol_s=0.06):
    """The flash-safe version of the edit's flashes.

    Returns {kept, thinned, ops, opted_in, peak_before, peak_after}: every
    full-frame flash event of the EDL (flash_safety.flash_events), thinned
    to ``max_per_s`` per second with downbeat hits (within ``tol_s`` of a
    downbeat of the user's track) kept first, then the rest in time order;
    ``ops`` remove the fx items whose every flash was thinned. Opted-in
    requests keep everything and return no ops."""
    edl = edl or {}
    fps = float(fps or (edl.get("frame") or {}).get("fps") or 30.0)
    opted = flash_safety.opted_in(request_text)
    beats = list(downbeats if downbeats is not None
                 else downbeats_of(maps))
    evs = []
    for e in flash_safety.flash_events(edl, index):
        on_beat = any(abs(e["t"] - b) <= tol_s for b in beats)
        evs.append({**e, "strength": 1.0 if on_beat else 0.5,
                    "downbeat": on_beat})
    kept, thinned = flash_safety.plan(evs, fps, opted_in=opted,
                                      max_per_s=max_per_s)
    before = flash_safety.assess(edl, index, request_text, fps=fps,
                                 max_per_s=max_per_s)["peak_per_s"]
    kept_ids = {e.get("id") for e in kept if e.get("source") == "fx"}
    gone = []
    for e in thinned:
        iid = e.get("id")
        if e.get("source") == "fx" and iid not in kept_ids and \
                iid not in gone:
            gone.append(iid)
    ops = [{"action": "remove", "layer": "fx", "id": str(i)} for i in gone]
    frames = sorted(int(round(e["t"] * fps)) for e in kept)
    win = max(1, int(round(fps)))
    after, j = 0, 0
    for k in range(len(frames)):
        while frames[k] - frames[j] >= win:
            j += 1
        after = max(after, k - j + 1)
    return {"kept": kept, "thinned": thinned, "ops": ops, "opted_in": opted,
            "downbeats": len(beats),
            "kept_downbeats": sum(1 for e in kept if e.get("downbeat")),
            "peak_before": before, "peak_after": after}


def summary(res):
    """One line for a reply."""
    if not res:
        return "flash check: not measured"
    if res.get("over_limit"):
        spans = ", ".join(f"{o['t0']:.2f}-{o['t1']:.2f}s ({o['count']}/s)"
                          for o in res.get("over")[:4])
        tail = (" — the user asked for it: export flagged contains_flashing"
                if res.get("opted_in") else
                f" — over the {res['max_per_s']}/s limit")
        return f"flash check: peak {res['peak_per_s']}/s at {spans}{tail}"
    return (f"flash check: peak {res['peak_per_s']}/s (limit "
            f"{res['max_per_s']}/s), {len(res.get('flashes') or [])} flashes")
