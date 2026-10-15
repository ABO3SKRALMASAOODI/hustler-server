"""Reference transfer (Wave 2, track 2F): "make it like this".

``analyze(path)`` reads a reel the user pasted with refsig's detectors (cuts,
negatives, white / colour flashes, B&W sections with or without a kept
colour, strobes, type on black) and returns its DEVICE-CLASS sequence: what
happens, in order, at which share of the reel. ``transfer(seq, plan)``
maps that sequence onto our cards at the matching story moments of THIS
edit. It transfers the grammar of the reel, never its marks: no creator's
text, logo, handle, watermark or footage is copied, and a strobing reel
counts as the user's flash opt-in only because the user asked to match it.
"""

CLASS_CARD = {
    "negative": ("accent_negative", "add_accent"),
    "white_flash": ("accent_white_hit", "add_accent"),
    "colour_flash": ("accent_colour_hit", "add_accent"),
    "bw_section": ("grade_section", "set_grade_window"),
    "bw_keep_colour": ("grade_isolate_hue", "isolate_colour"),
    "strobe": ("strobe_frames", "add_strobe"),
    "type_on_black": ("montage_cut", "plan_to_music"),
    "fast_cuts": ("montage_cut", "plan_to_music"),
}


def _runs(frames, gap=2):
    out = []
    for f in sorted(frames or []):
        if out and f - out[-1][1] <= gap:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def sequence(sig):
    """[{cls, t0, t1, u0, u1}] in time order from a refsig signature
    (u = share of the reel)."""
    fps = float(sig.get("fps") or 30.0)
    dur = float(sig.get("duration_s") or 1.0)
    ev = []

    def add(cls, a, b):
        ev.append({"cls": cls, "t0": round(a, 3), "t1": round(b, 3),
                   "u0": round(a / dur, 4), "u1": round(b / dur, 4)})
    for a, b in (sig.get("negatives") or {}).get("runs") or []:
        add("negative", a / fps, (b + 1) / fps)
    for a, b in _runs((sig.get("white_flashes") or {}).get("f")):
        add("white_flash", a / fps, (b + 1) / fps)
    for a, b in _runs((sig.get("colour_flashes") or {}).get("f")):
        add("colour_flash", a / fps, (b + 1) / fps)
    bw = sig.get("bw") or {}
    for (a, b), keep in zip(bw.get("sections_t") or [],
                            bw.get("selective_colour") or []):
        add("bw_keep_colour" if keep else "bw_section", a, b)
    for s in sig.get("strobe_sections") or []:
        a, b = (s.get("t") or [0.0, 0.0])[:2]
        add("strobe", float(a), float(b))
    for a, b in _runs((sig.get("type_on_black") or {}).get("f"), gap=4):
        if b - a >= 4:
            add("type_on_black", a / fps, (b + 1) / fps)
    cuts = (sig.get("cuts") or {}).get("t") or []
    # fast-cut stretches: 4+ cuts inside 2 s
    k = 0
    while k + 3 < len(cuts):
        if cuts[k + 3] - cuts[k] <= 2.0:
            j = k + 3
            while j + 1 < len(cuts) and cuts[j + 1] - cuts[j] <= 0.6:
                j += 1
            add("fast_cuts", cuts[k], cuts[j])
            k = j + 1
        else:
            k += 1
    ev.sort(key=lambda e: (e["t0"], e["cls"]))
    return ev


def classes_in_order(seq):
    out = []
    for e in seq:
        if not out or out[-1] != e["cls"]:
            out.append(e["cls"])
    return out


def analyze(path):
    """{sequence, classes, flashing, asl_s, duration_s} of a reel."""
    import refsig
    sig = refsig.analyze(path)
    seq = sequence(sig)
    return {"sequence": seq, "classes": classes_in_order(seq),
            "flashing": bool(sig.get("contains_flashing")),
            "asl_s": sig.get("asl_s"), "duration_s": sig.get("duration_s"),
            "shots": sig.get("shots")}


def transfer(seq, plan, fps=30.0):
    """[{cls, card, verb, at, end, moment}] the reel's devices placed on
    THIS edit: each event at the same share of the program, snapped to the
    nearest story moment within 0.6 s (a hit lands on a stress, a colour
    turn on the payoff). ``plan`` is device_planner.moments()."""
    prog = float(plan.get("program_s") or 0.0)
    moments = sorted(plan.get("moments") or [], key=lambda m: m["t"])
    out = []
    for e in seq:
        card, verb = CLASS_CARD.get(e["cls"], (None, None))
        if not card:
            continue
        at = e["u0"] * prog
        end = max(at + 1.0 / fps, e["u1"] * prog)
        near = min(moments, key=lambda m: abs(m["t"] - at)) if moments \
            else None
        if near is not None and abs(near["t"] - at) <= 0.6:
            shift = near["t"] - at
            at, end = near["t"], end + shift
        out.append({"cls": e["cls"], "card": card, "verb": verb,
                    "at": round(at, 3), "end": round(min(prog, end), 3),
                    "moment": (near or {}).get("kind") if near else None})
    return out
