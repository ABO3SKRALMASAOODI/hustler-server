"""Flash safety: at most N full-frame flashes in any one second (track 0C).

Owner decision (ROADMAP §10.1, taken as the recommended default): automatic
proposals keep at most ``config.FLASH_SAFE_MAX_PER_S`` (3) full-frame
flashes per second. Past that is a photosensitivity risk, so it happens only
when the user opted in (they asked for a strobe, or supplied a strobing
reference such as refs 19, 20 and 39); such an export is flagged
``contains_flashing``.

Two halves, separate on purpose (INTERFACES.md §8.3):
  * ``plan``  — the planning-time thinner every planner/device writer runs
    its flash events through (frame-exact windows, strongest hits kept);
  * ``flash_events`` / ``assess`` — the EDL-level reading the verifier and
    export gate use to compute ``contains_flashing``. The rendered-frame
    Harding-style counter is 0D's ``qa_metrics.flash_risk``.

Pure functions over plain data; no renderer edit.
"""

import re

import sys

_FALLBACK_MAX = 3


def default_max_per_s():
    """config.FLASH_SAFE_MAX_PER_S, read at call time (3 where the worker
    config is not importable — the backend loads this module by path)."""
    try:
        import config
        return int(getattr(config, "FLASH_SAFE_MAX_PER_S", _FALLBACK_MAX))
    except Exception:  # noqa: BLE001
        return _FALLBACK_MAX

# Motion templates that ARE a full-frame flash.
FLASH_TEMPLATES = ("flash_transition",)
# Stylize kinds that flash the whole frame.
FLASH_STYLIZE = ("flash",)
# Transition styles that dip the whole frame to white.
FLASH_TRANSITIONS = ("flash", "dip_white")
# fx types whose items are full-frame flashes unless the card says otherwise.
FLASH_FX_TYPES = ("accent", "strobe")

_OPT_IN = re.compile(
    r"\b(strob\w*|flash(?:es|ing|y)?|flicker\w*|epilep\w*|seizure|"
    r"photosensitiv\w*|rapid[- ]fire flash\w*)\b", re.I)
_REFUSE_OPT_IN = re.compile(
    r"\b(no|not|without|avoid|don'?t|never|less|fewer|reduce|stop)\b"
    r"[^.?!]{0,30}\b(strob\w*|flash\w*|flicker\w*)", re.I)
# "make it like 19" — the strobing references count as an opt-in.
_STROBE_REFS = ("19", "20", "39")


def opted_in(request_text):
    """Did the user ask for flashing? A strobe/flash request (not a
    request for fewer flashes) or a strobing reference opts in."""
    text = str(request_text or "")
    if not text.strip():
        return False
    if _REFUSE_OPT_IN.search(text):
        return False
    if _OPT_IN.search(text):
        return True
    return any(re.search(r"\blike (?:ref(?:erence)? ?)?#?" + n + r"\b",
                         text, re.I) for n in _STROBE_REFS)


def _event(e, fps):
    if isinstance(e, (int, float)):
        e = {"t": float(e)}
    t = float(e.get("t", e.get("start", 0.0)))
    return {**e, "t": t, "frame": int(round(t * fps)),
            "full_frame": bool(e.get("full_frame", True)),
            "strength": float(e.get("strength", 1.0))}


def violations(frames, fps, max_per_s):
    """Start frames of every window of one second (``fps`` frames) holding
    more than ``max_per_s`` flashes."""
    f = sorted(frames)
    win = max(1, int(round(fps)))
    return [f[i] for i in range(len(f) - max_per_s)
            if f[i + max_per_s] - f[i] < win]


def plan(events, fps, opted_in=False, max_per_s=None):
    """(kept, thinned): flash events thinned so no one-second window holds
    more than ``max_per_s`` full-frame flashes.

    ``events`` are dicts with ``t`` (program seconds), optional ``strength``
    (0..1, the hit's weight), ``full_frame`` (default True; local flashes
    pass untouched) and anything else the caller carries (an id). Windows
    are measured in frames (``round(t * fps)``), so 23.976 and 29.97 never
    drift. Strongest events are kept first, ties to the earlier one; the
    result is in time order and deterministic. ``opted_in`` keeps
    everything (the export is then flagged contains_flashing)."""
    fps = float(fps or 30.0)
    cap = int(default_max_per_s() if max_per_s is None else max_per_s)
    evs = [_event(e, fps) for e in events or ()]
    if opted_in:
        return sorted(evs, key=lambda e: e["t"]), []
    local = [e for e in evs if not e["full_frame"]]
    full = [e for e in evs if e["full_frame"]]
    order = sorted(range(len(full)),
                   key=lambda i: (-full[i]["strength"], full[i]["t"], i))
    kept_idx, frames = [], []
    for i in order:
        trial = frames + [full[i]["frame"]]
        if not violations(trial, fps, cap):
            frames = trial
            kept_idx.append(i)
    kept = [full[i] for i in sorted(kept_idx)] + local
    thinned = [full[i] for i in range(len(full)) if i not in set(kept_idx)]
    return (sorted(kept, key=lambda e: e["t"]),
            sorted(thinned, key=lambda e: e["t"]))


def _timeline():
    try:
        import timeline
        return timeline
    except ImportError:
        # The backend loads worker/timeline.py by path as worker_timeline.
        return sys.modules.get("worker_timeline")


def _junction_times(edl, index):
    try:
        tl = _timeline()
        program_blocks, transition_junctions = (tl.program_blocks,
                                                tl.transition_junctions)
        blocks = program_blocks(edl)
        juncs = transition_junctions(edl, index or {},
                                     n_blocks=len(blocks))
        return [blocks[k]["out_end"] for k in sorted(juncs)
                if 0 <= k < len(blocks) - 1]
    except Exception:  # noqa: BLE001
        return []


def _flash_card(device):
    if not device:
        return None
    try:
        import devices
        card = devices.card(device)
    except Exception:  # noqa: BLE001
        return None
    return card


def flash_events(edl, index=None):
    """Every full-frame flash an EDL plays, as ``{t, source, id}``: flash
    transitions at the junctions they fire on, flash stylize items, flash
    motion templates and fx items from flash-flagged devices."""
    edl = edl or {}
    out = []
    fx = edl.get("effects") or {}
    tr = fx.get("transition") or {}
    if tr.get("style") in FLASH_TRANSITIONS:
        for t in _junction_times(edl, index):
            out.append({"t": float(t), "source": "transition",
                        "id": f"transition:{tr.get('style')}"})
    for s in fx.get("stylize") or []:
        if isinstance(s, dict) and s.get("kind") in FLASH_STYLIZE:
            out.append({"t": float(s.get("start") or 0.0),
                        "source": "stylize", "id": s.get("id")})
    for m in edl.get("motion") or []:
        if isinstance(m, dict) and m.get("template") in FLASH_TEMPLATES:
            out.append({"t": float(m.get("start") or 0.0),
                        "source": "motion", "id": m.get("id")})
    for it in edl.get("fx") or []:
        if not isinstance(it, dict):
            continue
        card = _flash_card(it.get("device"))
        flag = ((card or {}).get("owner_flags") or {}).get("flash")
        if flag is None:
            flag = it.get("type") in FLASH_FX_TYPES
        if flag and it.get("start") is not None:
            out.append({"t": float(it["start"]), "source": "fx",
                        "id": it.get("id")})
    return sorted(out, key=lambda e: e["t"])


def assess(edl, index=None, request_text=None, fps=None, max_per_s=None):
    """{events, peak_per_s, over_limit, opted_in, contains_flashing}.

    ``contains_flashing`` is the export flag: true when the program passes
    the limit and the user opted in. ``over_limit`` without opt-in is the
    verifier's flash finding (an automatic proposal would have thinned
    it)."""
    fps = float(fps or ((edl or {}).get("frame") or {}).get("fps") or 30.0)
    cap = int(default_max_per_s() if max_per_s is None else max_per_s)
    evs = flash_events(edl, index)
    frames = sorted(int(round(e["t"] * fps)) for e in evs)
    win = max(1, int(round(fps)))
    peak = 0
    j = 0
    for i in range(len(frames)):
        while frames[i] - frames[j] >= win:
            j += 1
        peak = max(peak, i - j + 1)
    over = peak > cap
    optin = opted_in(request_text)
    return {"events": len(evs), "peak_per_s": peak, "max_per_s": cap,
            "over_limit": over, "opted_in": optin,
            "contains_flashing": bool(over and optin)}
