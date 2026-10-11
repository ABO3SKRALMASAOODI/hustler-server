"""Department ``rhythm``: track 1D's verbs (INTERFACES_W1.md §4).

Punctuation that lands on the speech (or a track the user supplied):
1–N-frame accents, frame-exact strobes, and per-cut junction devices.

Every verb SOLVES its arguments into apply_edit_batch operations — fx items
of type ``accent`` / ``strobe`` / ``junction`` (and, for
``kind='matte_flash'``, a ``subject`` fill: 1B's type) — and commits them
through ``tools_ext._write.commit`` (edit_batch.apply_batch), so a verb and
a stale MCP client's apply_edit_batch validate the same items identically.
Every item carries its device card id and, when a moment earned it, its
``bind`` (the intent a re-solve recomputes it from: ROADMAP A4).

Owner rules held here: no verb adds music, a sound or a zoom; moments come
from the speaker's prosody grid or the beat grid of a track the USER put in
the edit, never from music chosen for the purpose; automatic placements pass
``flash_safety.plan`` (≤ 3 full-frame flashes a second) unless the user
asked for strobing, in which case the export is flagged
``contains_flashing``.

Its skill is ``skills/punctuation.md``; it documents every verb AND the
exact apply_edit_batch operations that write and remove the same items (the
stale-client path).
"""

import math

DEPARTMENT = "rhythm"

# Pinned verb names (INTERFACES_W1.md §4). Renaming one is a round-lead
# change: 1F's planner, the skills and the refbench agent cases name them.
VERBS = ("add_accent", "add_strobe", "set_junction", "remove_punctuation")

ACCENT_KINDS = ("negative", "white_hit", "colour_hit", "threshold",
                "posterize", "mono", "mirror", "hue_step", "rgb_slice",
                "box_invert")
MOMENTS = ("stress", "word", "phrase_end", "cut", "beat", "downbeat",
           "onset")
DENSITIES = ("sparse", "pulse", "accelerate", "8ths", "16ths")
# per kind: (frames at peak, decay frames)
ACCENT_SHAPE = {"white_hit": (1, 4), "colour_hit": (2, 2),
                "negative": (1, 0), "box_invert": (2, 0),
                "rgb_slice": (2, 0), "hue_step": (2, 0)}
# junction style -> device card
JUNCTION_CARDS = {
    "dissolve": "junction_dissolve", "fadegrays": "junction_xfade",
    "pixelize": "junction_xfade", "radial": "junction_xfade",
    "slices": "junction_xfade", "wipe_left": "junction_wipe",
    "wipe_right": "junction_wipe", "wipe_up": "junction_wipe",
    "wipe_down": "junction_wipe", "iris": "junction_iris",
    "columns": "junction_columns", "staircase": "junction_staircase",
    "box_steps": "junction_box_steps", "bands": "junction_bands",
    "letter_window": "junction_letter_window",
    "matte_cut": "junction_matte_cut",
    "double_exposure": "junction_double_exposure",
    "pixel_stretch": "junction_pixel_stretch",
    "hit_flash": "junction_hit_flash", "whip_up": "junction_whip",
    "whip_down": "junction_whip", "zoom_blur": "junction_zoom_blur",
    "radial_blur": "junction_zoom_blur", "datamosh": "junction_datamosh"}
# style -> default window (seconds); the cards' params agree
JUNCTION_DURATION = {"dissolve": 0.5, "iris": 0.4, "columns": 0.5,
                     "staircase": 0.5, "box_steps": 0.8, "bands": 0.5,
                     "letter_window": 0.8, "matte_cut": 0.5,
                     "double_exposure": 0.6, "pixel_stretch": 0.4,
                     "hit_flash": 0.4, "whip_up": 0.4, "whip_down": 0.4,
                     "zoom_blur": 0.5, "radial_blur": 0.5, "datamosh": 1.0}
PUNCTUATION_TYPES = ("accent", "strobe", "junction")
# Minimum gap between automatic accents (sparse) and between any two.
SPARSE_GAP_S = 2.0
MIN_GAP_F = 3


def _r(msg):
    return f"REJECTED: {msg}"


# ── shared context ───────────────────────────────────────────────────────

def _edl(ctx):
    return dict(ctx.latest_edl()["json"])


def _timeline(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _fps(ctx, edl):
    """The final program rate (renderer: the source rate capped at 60)."""
    v = ((getattr(ctx, "index", None) or {}).get("video") or {})
    fps = v.get("fps") or ((edl.get("canvas") or {}).get("fps")) or 30.0
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        fps = 30.0
    return max(1.0, min(60.0, fps))


def _frame(t, fps):
    import fxprims
    return fxprims.frame_at(t, fps)


def _t(frame, fps):
    return round(frame / round(float(fps), 3), 2)


def _supported(fx_type):
    """None when the render executor can draw ``fx:<type>`` (or cannot be
    asked), else a refusal line."""
    try:
        import remote
        ok = remote.executor_supports(f"fx:{fx_type}")
    except Exception:  # noqa: BLE001
        ok = None
    if ok is False:
        return _r(f"the render service does not draw {fx_type} items yet "
                  "(it is still on an older version); nothing was written. "
                  "Try again after the render service updates.")
    return None


def _new_id(edl, prefix, taken):
    have = {i.get("id") for i in edl.get("fx") or []} | taken
    n = 1
    while f"{prefix}{n}" in have:
        n += 1
    taken.add(f"{prefix}{n}")
    return f"{prefix}{n}"


def _opted_in(ctx):
    import flash_safety
    return flash_safety.opted_in(getattr(ctx, "user_message", "") or "")


# ── moments ──────────────────────────────────────────────────────────────

def _speech_moments(ctx, tl, kind):
    """[(program t, strength, bind)] for word / stress / phrase_end."""
    import prosody_grid
    words = list((getattr(ctx, "index", None) or {}).get("words") or [])
    if not words:
        return None
    perception = None
    try:
        import agent_tools
        perception = agent_tools._get_perception(ctx)
    except Exception:  # noqa: BLE001 — duration-only stress then
        perception = None
    rmap = prosody_grid.build(words, perception)
    starts = {}
    for i, w in enumerate(words):
        try:
            starts.setdefault(round(float(w["t0"]), 4), i)
            starts.setdefault(("end", round(float(w["t1"]), 4)), i)
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for o in rmap.get("onsets") or []:
        if o.get("cls") != kind:
            continue
        src = float(o["t"])
        if kind == "phrase_end":
            i = starts.get(("end", round(src, 4)))
            t = tl.src_to_out(src - 1e-4)
            t = None if t is None else t + 1e-4
        else:
            i = starts.get(round(src, 4))
            t = tl.src_to_out(src)
        if t is None or i is None:
            continue
        out.append((float(t), float(o.get("strength") or 0.5),
                    {"on": kind, "ref": int(i), "rhythm": "speech"}))
    return out


def _music_moments(ctx, edl, tl, kind):
    """[(program t, strength, bind)] from the beat grid of a track the user
    put in the edit, or (None, reason)."""
    import rhythm_types
    music = [m for m in edl.get("music") or [] if isinstance(m, dict)
             and m.get("storage_key") and not m.get("muted")]
    if not music:
        return None, ("on='%s' needs the beat grid of a track the user "
                      "supplied, and this edit has no music. Talking heads: "
                      "use on='stress' (the speaker's own rhythm). Never add "
                      "music to get a clock." % kind)
    item = music[0]
    try:
        import agent_tools
        import perception as perception_mod
        p = ctx._asset_perception.get(item["storage_key"])
        if p is None:
            asset = ctx.db.run(agent_tools.dbx.asset_by_key, ctx.project_id,
                               item["storage_key"])
            p = perception_mod.get_or_compute_for_asset(
                ctx.db, agent_tools.dbx, asset,
                agent_tools._asset_local_path(ctx, asset))
            ctx._asset_perception[item["storage_key"]] = p
    except Exception as e:  # noqa: BLE001
        return None, f"the track's beat grid could not be read ({str(e)[:120]})"
    rmap = rhythm_types.from_beat_grid(p, ref={"music_id": item.get("id")})
    prog = rhythm_types.to_program(rmap, tl, item)
    out = []
    if kind in ("beat", "downbeat"):
        seq = prog.get("beats" if kind == "beat" else "downbeats") or []
        out = [(float(t), 1.0 if kind == "downbeat" else 0.7,
                {"on": kind, "ref": i, "rhythm": item.get("id")})
               for i, t in enumerate(seq)]
    else:
        out = [(float(o["t"]), float(o.get("strength") or 0.5),
                {"on": "onset", "ref": i, "rhythm": item.get("id")})
               for i, o in enumerate(prog.get("onsets") or [])]
    if not out:
        return None, "the user's track has no clear pulse to land on"
    return out, None


def _cut_moments(edl, tl):
    import timeline
    out = []
    blocks = timeline.program_blocks(edl)
    for a, b in zip(blocks, blocks[1:]):
        if a.get("kind") == b.get("kind") == "footage" and \
                abs(float(b["src_start"]) - float(a["src_end"])) < 1e-3:
            continue                         # a split, not a cut
        t = float(a["out_end"])
        ref = float(b["src_start"]) if b.get("kind") == "footage" else t
        on = "cut" if b.get("kind") == "footage" else "time"
        out.append((t, 1.0, {"on": on, "ref": ref}))
    return out


def _subdivide(beats, n):
    """Every 1/n of the beat grid (8ths: n=2, 16ths: n=4)."""
    out = []
    for (a, sa, ba), (b, _sb, _bb) in zip(beats, beats[1:]):
        for k in range(n):
            out.append((a + (b - a) * k / n, sa if k == 0 else 0.5,
                        dict(ba, offset_f=0) if k == 0 else
                        {"on": "time", "ref": round(a + (b - a) * k / n, 3)}))
    return out


def _shape(moments, density, count, fps, end_t):
    """Pick moments by density. ``moments`` are (t, strength, bind) sorted
    by time inside the window."""
    if not moments:
        return []
    min_gap = MIN_GAP_F / fps
    if density in ("8ths", "16ths", "pulse"):
        out, last = [], -1e9
        gap = 0.5 if density == "pulse" else min_gap
        for m in moments:
            if m[0] - last >= gap - 1e-6:
                out.append(m)
                last = m[0]
        return out[:count] if count else out
    if density == "accelerate":
        # from the payoff end backwards, each earlier gap longer than the
        # one after it: spacing decreases monotonically into the payoff
        ms = sorted(moments, key=lambda m: m[0])
        picks = [ms[-1]]
        gap = max(min_gap, 2.5 / fps)
        i = len(ms) - 2
        while i >= 0 and (not count or len(picks) < count):
            want = picks[-1][0] - gap * 1.45
            best = None
            for j in range(i, -1, -1):
                g = picks[-1][0] - ms[j][0]
                if g > gap * 1.12 and ms[j][0] <= want + gap * 0.5:
                    best = j
                    break
            if best is None:
                break
            gap = picks[-1][0] - ms[best][0]
            picks.append(ms[best])
            i = best - 1
        return sorted(picks, key=lambda m: m[0])
    # sparse: the strongest moments, at least SPARSE_GAP_S apart
    want = count or 2
    picks = []
    for m in sorted(moments, key=lambda m: (-m[1], m[0])):
        if all(abs(m[0] - p[0]) >= SPARSE_GAP_S for p in picks):
            picks.append(m)
        if len(picks) >= want:
            break
    return sorted(picks, key=lambda m: m[0])


def _parse_region(region):
    if region in (None, "", "none"):
        return None
    if isinstance(region, str):
        if region in ("card", "face"):
            return {"shape": region}
        return None
    return dict(region) if isinstance(region, dict) else None


# ── add_accent ───────────────────────────────────────────────────────────

def add_accent(ctx, kind, at=None, on=None, start=None, end=None, count=None,
               density=None, scope=None, region=None, frames=None,
               colour=None, intensity=None, decay_f=None, bands=None,
               seed=None):
    kind = str(kind or "").strip().lower()
    if kind not in ACCENT_KINDS + ("matte_flash",):
        return _r(f"kind must be one of {', '.join(ACCENT_KINDS)} or "
                  "matte_flash.")
    fx_type = "subject" if kind == "matte_flash" else "accent"
    import schemas
    if fx_type not in schemas.FX_TYPES:
        return _r("matte_flash writes a subject fill, and subject effects "
                  "are not available in this build. Use kind='white_hit' "
                  "for a full-frame hit.")
    refused = _supported(fx_type)
    if refused:
        return refused
    edl = _edl(ctx)
    tl = _timeline(edl)
    prog = float(tl.out_duration)
    fps = _fps(ctx, edl)
    a = 0.0 if start is None else float(start)
    b = prog if end is None else float(end)
    if not 0 <= a < b <= prog + 0.01:
        return _r(f"start/end must lie inside the program (0-{prog:.2f}s).")
    density = (density or "sparse").strip().lower()
    if density not in DENSITIES:
        return _r(f"density must be one of {', '.join(DENSITIES)}.")
    moments = None
    if at is not None:
        ats = at if isinstance(at, (list, tuple)) else [at]
        try:
            moments = [(float(t), 1.0, {"on": "time", "ref": round(float(t),
                                                                   3)})
                       for t in ats]
        except (TypeError, ValueError):
            return _r("at is a program second or a list of them.")
        bad = [t for t, _s, _b in moments if not 0 <= t < prog]
        if bad:
            return _r(f"at {bad[0]:g}s is outside the program (0-{prog:.2f}s).")
    else:
        on = (on or ("stress" if (getattr(ctx, "index", None) or {}).get(
            "words") else None))
        if on is None:
            return _r("give at= (program seconds) or on= (stress, word, "
                      "phrase_end, cut, beat, downbeat, onset).")
        on = str(on).strip().lower()
        if on not in MOMENTS:
            return _r(f"on must be one of {', '.join(MOMENTS)}.")
        if on in ("stress", "word", "phrase_end"):
            moments = _speech_moments(ctx, tl, on)
            if moments is None:
                return _r(f"on='{on}' needs the transcript; this program has "
                          "none. Use at= or on='cut'.")
        elif on == "cut":
            moments = _cut_moments(edl, tl)
        else:
            moments, why = _music_moments(ctx, edl, tl, on)
            if moments is None:
                return _r(why)
        if density in ("8ths", "16ths"):
            if on not in ("beat", "downbeat"):
                return _r(f"density='{density}' subdivides a beat grid: "
                          "use on='beat' with a track the user supplied.")
            moments = _subdivide(moments, 2 if density == "8ths" else 4)
        moments = sorted(m for m in moments if a - 1e-6 <= m[0] < b)
        if not moments:
            return _r(f"no {on} moment between {a:g}s and {b:g}s.")
        try:
            want = None if count is None else max(1, int(count))
        except (TypeError, ValueError):
            return _r("count must be a positive integer.")
        moments = _shape(moments, density, want, fps, b)
    peak_f, decay = ACCENT_SHAPE.get(kind, (1, 0))
    if frames is not None:
        try:
            peak_f = max(1, min(12, int(frames)))
        except (TypeError, ValueError):
            return _r("frames must be a whole number 1-12.")
    if decay_f is not None:
        decay = int(decay_f)
    if kind not in ("white_hit", "colour_hit", "matte_flash"):
        decay = 0
    reg = _parse_region(region)
    scope = (scope or "picture").strip().lower()
    if scope not in ("picture", "all"):
        return _r("scope is 'picture' (type untouched) or 'all' (the type "
                  "inverts too).")
    stage = "over_type" if scope == "all" else "pre_captions"
    # flash safety (automatic placements): <= 3 full-frame flashes a second
    import flash_safety
    from fx_schemas.accents import FLASH_KINDS
    flashes = kind in FLASH_KINDS + ("matte_flash",) or \
        (kind == "box_invert" and reg is None)
    full = reg is None or reg.get("shape") == "card"
    events = [{"t": m[0], "strength": m[1], "i": i,
               "full_frame": bool(flashes and full)}
              for i, m in enumerate(moments)]
    opted = _opted_in(ctx)
    kept, thinned = flash_safety.plan(events, fps, opted_in=opted)
    keep_idx = {e["i"] for e in kept}
    taken, items, seen_frames = set(), [], set()
    for i, (t, _s, bind) in enumerate(moments):
        if i not in keep_idx:
            continue
        f0 = _frame(t, fps)
        if f0 in seen_frames:
            continue
        seen_frames.add(f0)
        f1 = min(_frame(prog, fps), f0 + peak_f)
        if f1 <= f0:
            continue
        if kind == "matte_flash":
            item = {"id": _new_id(edl, "mf", taken), "type": "subject",
                    "stage": "world", "start": _t(f0, fps),
                    "end": _t(f1, fps), "kind": "fill",
                    "colour": colour or "white", "decay_f": decay,
                    "bind": bind}
            if _card_exists("subject_fill_flash"):
                item["device"] = "subject_fill_flash"
        else:
            item = {"id": _new_id(edl, f"ac_{kind[:3]}", taken),
                    "type": "accent", "stage": stage, "start": _t(f0, fps),
                    "end": _t(f1, fps), "kind": kind,
                    "device": f"accent_{kind}", "bind": bind}
            if decay:
                item["decay_f"] = int(decay)
            if colour is not None and kind == "colour_hit":
                item["colour"] = colour
            elif kind == "colour_hit":
                hue = _outgoing_hue(ctx, tl, t, fps)
                if hue:
                    item["colour"] = hue
            if intensity is not None:
                item["intensity"] = float(intensity)
            if bands is not None and kind in ("rgb_slice", "box_invert"):
                item["bands"] = int(bands)
            if kind in ("box_invert", "rgb_slice", "hue_step"):
                item["seed"] = int(seed if seed is not None else len(items))
            if reg is not None:
                item["region"] = reg
        items.append(item)
    if not items:
        return _r("every placement was removed by flash safety; space the "
                  "accents out or ask the user whether they want strobing.")
    import tools_ext._write as w
    ops = [w.upsert_fx(it) for it in items]
    when = ", ".join(f"{it['start']:g}" for it in items[:8]) + \
        ("..." if len(items) > 8 else "")
    note = ""
    if thinned:
        note = (f"; flash safety held {len(thinned)} back (≤3 full-frame "
                "flashes a second; the user did not ask for strobing)")
    elif opted and len(items) > 1 and flashes:
        note = "; the user asked for flashing: the export is flagged"
    summary = (f"add_accent: {len(items)} {kind} at {when}s "
               f"({'scope all' if stage == 'over_type' else 'picture'})"
               f"{note}")
    out = w.commit(ctx, ops, summary)
    if out.startswith("REJECTED"):
        return out
    ids = ", ".join(it["id"] for it in items)
    return f"{out}\nids: {ids}{note}"


def _card_exists(card_id):
    try:
        import edit_batch
        return card_id in edit_batch._device_cards()
    except Exception:  # noqa: BLE001
        return False


def _outgoing_hue(ctx, tl, t, fps):
    """The dominant hue of the frame before ``t`` on the proxy (colour_hit
    with no colour: ref 01@3.833's red is the outgoing shot's)."""
    try:
        import wipe_masks
        src = tl.out_to_src(max(0.0, t - 1.0 / fps))
        if src is None:
            return None
        im = wipe_masks.grab_frame(ctx.proxy_path(), src, max_side=480)
        return wipe_masks.dominant_hue(im) if im is not None else None
    except Exception:  # noqa: BLE001
        return None


# ── add_strobe ───────────────────────────────────────────────────────────

def _frame_source(entry):
    """A strobe image from a verb argument: an FxSource dict, a project
    media key, 'src:<seconds>', 'program', or a colour."""
    if isinstance(entry, dict):
        if "source" in entry:
            return dict(entry["source"]), entry
        return dict(entry), {}
    s = str(entry or "").strip()
    low = s.lower()
    if low.startswith("src:"):
        return {"kind": "source", "src_t": float(low[4:])}, {}
    if low in ("program", "live"):
        return {"kind": "program"}, {}
    if low in ("white", "black", "accent") or (low.startswith("#")
                                                and len(low) == 7):
        return {"kind": "colour", "colour": low}, {}
    return {"kind": "asset", "asset_key": s}, {}


def add_strobe(ctx, start, end, frames=None, variant_set=None, hold=2,
               pattern=None, fit=None, negate=None, as_insert=False):
    refused = _supported("strobe")
    if refused:
        return refused
    if as_insert:
        return _r("as_insert is not available: a strobe is written as a "
                  "timeline-neutral cover (sound and timing untouched), "
                  "which is what as_insert=false gives.")
    edl = _edl(ctx)
    tl = _timeline(edl)
    prog = float(tl.out_duration)
    fps = _fps(ctx, edl)
    try:
        a, b = float(start), float(end)
        hold = max(1, min(8, int(hold or 2)))
    except (TypeError, ValueError):
        return _r("start/end are program seconds; hold is 1-8 frames.")
    if not 0 <= a < b <= prog + 0.01:
        return _r(f"start/end must lie inside the program (0-{prog:.2f}s).")
    srcs = []
    if variant_set:
        vs = variant_set if isinstance(variant_set, dict) else {}
        import fxgraph
        keys = fxgraph.peer("variants:make_variant_set", ctx,
                            vs.get("t"), vs.get("kinds") or [],
                            default=None)
        if not keys:
            return _r("variant sets come from make_variant_set (the "
                      "finishing department), which is not available here; "
                      "pass frames= (project images, 'src:<s>' clip frames, "
                      "'program', colours) instead.")
        srcs = [({"kind": "asset", "asset_key": k}, {}) for k in keys]
    for entry in frames or []:
        try:
            srcs.append(_frame_source(entry))
        except (TypeError, ValueError):
            return _r(f"cannot read strobe frame {entry!r}.")
    if not srcs:
        return _r("give frames= or variant_set=.")
    if len(srcs) > 64:
        return _r("at most 64 images.")
    neg = (negate or "none").strip().lower()
    if neg not in ("none", "alternate", "all"):
        return _r("negate is none, alternate or all.")
    fr_items = []
    for i, (src, extra) in enumerate(srcs):
        fr = {"source": src, "hold_f": int(extra.get("hold_f") or hold)}
        n = extra.get("negate")
        if n is None:
            n = neg == "all" or (neg == "alternate" and i % 2 == 1)
        if n:
            fr["negate"] = True
        fr_items.append(fr)
    f0, f1 = _frame(a, fps), _frame(b, fps)
    if f1 - f0 < min(f["hold_f"] for f in fr_items):
        return _r("the window is shorter than one hold.")
    taken = set()
    item = {"id": _new_id(edl, "st", taken), "type": "strobe",
            "stage": "pre_captions", "start": _t(f0, fps),
            "end": _t(f1, fps), "frames": fr_items,
            "device": "strobe_frames", "bind": {"on": "time", "ref": a}}
    if pattern:
        item["pattern"] = pattern
        if pattern == "random":
            item["seed"] = 1
    if fit:
        item["fit"] = fit
    # flash safety: a strobe is a train of full-frame changes
    per_s = 0
    try:
        from fx_schemas.accents import StrobeFx
        times = StrobeFx.model_validate(item).flash_times() or []
        per_s = _peak_per_s(times, fps)
    except Exception:  # noqa: BLE001 — the commit names the rule
        per_s = 0
    import flash_safety
    cap = flash_safety.default_max_per_s()
    if per_s > cap and not _opted_in(ctx):
        return _r(f"this strobe changes the whole frame {per_s} times "
                  "inside one second. Flash safety allows more than 3 a second "
                  "only when the user asked for strobing or flashing: ask "
                  "them first (or strobe at most 3 images in a second).")
    import tools_ext._write as w
    note = "; the user asked for strobing: the export is flagged " \
        "contains_flashing" if per_s > cap else ""
    out = w.commit(ctx, [w.upsert_fx(item)],
                   f"add_strobe: {len(fr_items)} images, {hold}-frame holds, "
                   f"{item['start']:g}-{item['end']:g}s{note}")
    if out.startswith("REJECTED"):
        return out
    return f"{out}\nid: {item['id']}{note}"


def _peak_per_s(times, fps):
    """The most events inside any one-second window (flash_safety's
    counting, on frames)."""
    frames = sorted(int(round(float(t) * fps)) for t in times)
    win = max(1, int(round(fps)))
    peak, j = 0, 0
    for i in range(len(frames)):
        while frames[i] - frames[j] >= win:
            j += 1
        peak = max(peak, i - j + 1)
    return peak


# ── set_junction ─────────────────────────────────────────────────────────

def _parse_origin(origin, edl):
    if origin in (None, "", "centre", "center"):
        return None
    if isinstance(origin, dict):
        return dict(origin)
    s = str(origin).strip()
    if s == "face":
        return {"kind": "face"}
    if s.startswith("point:"):
        x, y = (float(v) for v in s[6:].split(","))
        return {"kind": "point", "point": [x, y]}
    if s in {m.get("id") for m in edl.get("motion") or []}:
        return {"kind": "glyph", "ref": s}
    raise ValueError(f"origin {s!r}: centre, face, point:x,y or a motion "
                     "item id")


def set_junction(ctx, style, at=None, cuts=None, origin=None, duration=None,
                 count=None, direction=None, colour=None):
    from fx_schemas.accents import JUNCTION_STYLES
    style = str(style or "").strip().lower()
    if style not in JUNCTION_STYLES + ("none",):
        return _r(f"style must be one of {', '.join(JUNCTION_STYLES)} or "
                  "'none'.")
    refused = None if style == "none" else _supported("junction")
    if refused:
        return refused
    edl = _edl(ctx)
    tl = _timeline(edl)
    fps = _fps(ctx, edl)
    all_cuts = _cut_moments(edl, tl)
    if not all_cuts:
        return _r("this program has no cut to decorate.")
    targets = []
    if at is not None:
        ats = at if isinstance(at, (list, tuple)) else [at]
        for t in ats:
            near = min(all_cuts, key=lambda c: abs(c[0] - float(t)))
            if abs(near[0] - float(t)) > 0.15:
                listed = ", ".join(f"{c[0]:.2f}" for c in all_cuts[:12])
                return _r(f"no cut at {float(t):g}s; the program cuts at "
                          f"{listed}s.")
            targets.append(near)
    else:
        mode = (cuts or "scene")
        if isinstance(mode, (list, tuple)):
            for t in mode:
                near = min(all_cuts, key=lambda c: abs(c[0] - float(t)))
                if abs(near[0] - float(t)) <= 0.15:
                    targets.append(near)
        elif mode == "every":
            targets = list(all_cuts)
        elif mode == "scene":
            targets = _scene_cuts(ctx, edl, all_cuts)
            if not targets:
                return _r("no scene change in this program (every cut is a "
                          "jump cut inside one shot, which stays bare). Name "
                          "a cut with at=, or cuts='every' for a montage.")
        else:
            return _r("cuts is 'scene', 'every' or a list of program "
                      "seconds.")
    targets = sorted({round(t[0], 3): t for t in targets}.values())
    import tools_ext._write as w
    ops, taken, removed = [], set(), []
    existing = [i for i in edl.get("fx") or []
                if i.get("type") == "junction"]
    for t, _s, _bind in targets:
        for it in existing:
            if abs(float(it.get("at") or -9) - t) <= 1.5 / fps:
                ops.append(w.remove_fx(it["id"]))
                removed.append(it["id"])
    if style == "none":
        if not ops:
            return ("NO CHANGE: no junction device sits on those cuts. Do "
                    "NOT tell the user you changed anything.")
        return w.commit(ctx, ops, f"set_junction: removed {', '.join(removed)}")
    try:
        org = _parse_origin(origin, edl)
    except ValueError as e:
        return _r(str(e))
    d = float(duration) if duration is not None else \
        JUNCTION_DURATION.get(style, 0.4)
    items = []
    for t, _s, bind in targets:
        item = {"id": _new_id(edl, "jn", taken), "type": "junction",
                "stage": "junction", "at": round(t, 2), "style": style,
                "duration_s": round(d, 3), "device": JUNCTION_CARDS[style],
                "bind": bind}
        if org is not None:
            item["origin"] = org
        if count is not None:
            item["count"] = int(count)
        if direction is not None:
            item["direction"] = direction
        if colour is not None:
            item["colour"] = colour
        if style == "datamosh":
            item["seed"] = 1
        items.append(item)
    # flash safety on the cuts that flash (hit_flash, box_steps)
    import flash_safety
    if style in ("hit_flash", "box_steps"):
        evs = [{"t": it["at"], "i": k} for k, it in enumerate(items)]
        kept, thinned = flash_safety.plan(evs, fps, opted_in=_opted_in(ctx))
        keep = {e["i"] for e in kept}
        items = [it for k, it in enumerate(items) if k in keep]
    ops += [w.upsert_fx(it) for it in items]
    where = ", ".join(f"{it['at']:g}" for it in items[:10])
    out = w.commit(ctx, ops, f"set_junction: {style} at {where}s"
                   + (f" (replaced {', '.join(removed)})" if removed else ""))
    if out.startswith("REJECTED"):
        return out
    return f"{out}\nids: {', '.join(it['id'] for it in items)}"


def _scene_cuts(ctx, edl, all_cuts):
    """The cuts where the footage changes shot (timeline.transition_
    junctions' 'scene' rule): jump cuts inside one take stay bare."""
    try:
        import timeline
        probe = dict(edl)
        probe["effects"] = dict(edl.get("effects") or {})
        probe["effects"]["transition"] = {"style": "dip_black",
                                          "scope": "scene"}
        blocks = timeline.program_blocks(edl)
        juncs = timeline.transition_junctions(
            probe, getattr(ctx, "index", None) or {}, n_blocks=len(blocks))
        times = {round(float(blocks[k]["out_end"]), 3) for k in juncs
                 if 0 <= k < len(blocks) - 1}
    except Exception:  # noqa: BLE001
        return list(all_cuts)
    return [c for c in all_cuts if round(c[0], 3) in times]


# ── remove_punctuation ──────────────────────────────────────────────────

def remove_punctuation(ctx, ids):
    edl = _edl(ctx)
    fx = [i for i in edl.get("fx") or [] if isinstance(i, dict)]
    mine = {i["id"]: i for i in fx if i.get("type") in PUNCTUATION_TYPES
            or (i.get("type") == "subject" and str(i.get("id", ""))
                .startswith("mf"))}
    if ids in ("all", ["all"]):
        ids = list(mine)
    if isinstance(ids, str):
        ids = [ids]
    ids = [str(i) for i in ids or []]
    missing = [i for i in ids if i not in mine]
    if missing:
        have = ", ".join(sorted(mine)) or "none"
        return _r(f"{', '.join(missing)} is not punctuation in this edit "
                  f"(accents, strobes, junctions: {have}).")
    if not ids:
        return ("NO CHANGE: nothing to remove. Do NOT tell the user you "
                "changed anything.")
    import tools_ext._write as w
    return w.commit(ctx, [w.remove_fx(i) for i in ids],
                    f"remove_punctuation: {', '.join(ids)}")


# ── re-solve on a keep change (hooks.on_keep_change) ────────────────────

def _drop_orphan_junctions(old_edl, new_edl, old_tl=None, new_tl=None):
    """A junction whose cut a keep change removed (two spans merged, an
    insert deleted) decorates nothing: drop it with a note rather than
    leave a device the render can only report ``omit`` (and a final would
    then ask to acknowledge). Accents and strobes are content-anchored and
    move with their footage (timeline.remap_program_items)."""
    items = (new_edl or {}).get("fx") or []
    juncs = [i for i in items if isinstance(i, dict)
             and i.get("type") == "junction"]
    if not juncs:
        return new_edl, []
    try:
        cuts = [c[0] for c in _cut_moments(new_edl, _timeline(new_edl))]
    except Exception:  # noqa: BLE001
        return new_edl, []
    gone = [j["id"] for j in juncs
            if not any(abs(float(j.get("at") or -9) - c) <= 0.06
                       for c in cuts)]
    if not gone:
        return new_edl, []
    new_edl["fx"] = [i for i in items if not (isinstance(i, dict)
                                              and i.get("id") in gone)]
    return new_edl, [f"junction device(s) {', '.join(gone)} removed: their "
                     "cut is no longer in the edit"]


try:
    import hooks as _hooks
    _hooks.register("keep_change", _drop_orphan_junctions)
except Exception:  # noqa: BLE001 — a lane without hooks keeps every item
    pass


# ── catalog ──────────────────────────────────────────────────────────────

_REGION = {"type": "object", "description":
           "FxRegion: {shape: card|face|rect|ellipse|polygon|glyph, box?, "
           "points?, ref?, invert?, feather_px?}"}

TOOL_SPECS = {
    "add_accent": (
        add_accent,
        "1-N-frame punctuation on the speech: negative, white_hit (peak + "
        "decay), colour_hit (the outgoing shot's hue), threshold, posterize, "
        "mono, mirror, hue_step, rgb_slice, box_invert; kind='matte_flash' "
        "fills the subject. Place with at= (program seconds) or on=stress|"
        "word|phrase_end|cut (beat|downbeat|onset only on a track the USER "
        "supplied) inside start/end, shaped by density: sparse (strongest, "
        ">=2s apart), pulse, accelerate (gaps shrink into the payoff), 8ths/"
        "16ths (beat grids). scope='all' inverts the type too. region "
        "{shape:'card'} clips to the picture card. Flash safety keeps <=3 "
        "full-frame flashes a second unless the user asked for strobing. "
        "Adds no sound or zoom. One punctuation family per section; never "
        "inside a payoff hold.",
        {"kind": {"type": "string", "enum": list(ACCENT_KINDS)
                  + ["matte_flash"]},
         "at": {"type": ["number", "array"], "items": {"type": "number"}},
         "on": {"type": "string", "enum": list(MOMENTS)},
         "start": {"type": "number"}, "end": {"type": "number"},
         "count": {"type": "integer", "minimum": 1},
         "density": {"type": "string", "enum": list(DENSITIES)},
         "scope": {"type": "string", "enum": ["picture", "all"]},
         "region": _REGION,
         "frames": {"type": "integer", "minimum": 1, "maximum": 12},
         "colour": {"type": "string"},
         "intensity": {"type": "number", "minimum": 0, "maximum": 1},
         "decay_f": {"type": "integer", "minimum": 0, "maximum": 12},
         "bands": {"type": "integer", "minimum": 2, "maximum": 32},
         "seed": {"type": "integer", "minimum": 0}}),
    "add_strobe": (
        add_strobe,
        "A frame-exact strobe over start-end: images held hold (1-8) frames "
        "each, cycled by pattern (sequence|pingpong|random). frames: project "
        "media keys, 'src:<seconds>' (a clip frame), 'program' (the live "
        "picture), colours, or {source, hold_f, negate}; variant_set {t, "
        "kinds} uses make_variant_set. negate: none|alternate|all. fit: "
        "cover|contain|register (register = every image on the first one's "
        "pixels). A timeline-neutral cover: sound and timing untouched. More "
        "than 3 changes a second needs the user's ask (flash safety).",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "frames": {"type": "array", "items": {}},
         "variant_set": {"type": "object"},
         "hold": {"type": "integer", "minimum": 1, "maximum": 8},
         "pattern": {"type": "string",
                     "enum": ["sequence", "pingpong", "random"]},
         "fit": {"type": "string", "enum": ["cover", "contain", "register"]},
         "negate": {"type": "string", "enum": ["none", "alternate", "all"]},
         "as_insert": {"type": "boolean"}}),
    "set_junction": (
        set_junction,
        "A designed cut device at real cuts, duration-preserving (sound and "
        "timing untouched): dissolve, fadegrays, pixelize, radial, slices, "
        "wipe_left/right/up/down, iris, columns, staircase, box_steps, bands, "
        "letter_window (origin = a motion item id), matte_cut, "
        "double_exposure, pixel_stretch, hit_flash, whip_up/down, zoom_blur, "
        "radial_blur, datamosh; 'none' removes. at= one cut (program "
        "seconds) or cuts='scene' (default: real shot changes; jump cuts stay "
        "bare)|'every'|[seconds]. origin: centre|face|point:x,y|<motion id>. "
        "One device per cut (it replaces the cut's previous one). Content-"
        "born transitions first; one family per section.",
        {"style": {"type": "string"},
         "at": {"type": ["number", "array"], "items": {"type": "number"}},
         "cuts": {"type": ["string", "array"], "items": {"type": "number"}},
         "origin": {"type": ["string", "object"]},
         "duration": {"type": "number", "minimum": 0.04, "maximum": 2},
         "count": {"type": "integer", "minimum": 2, "maximum": 32},
         "direction": {"type": "string",
                       "enum": ["left", "right", "up", "down", "in", "out"]},
         "colour": {"type": "string"}}),
    "remove_punctuation": (
        remove_punctuation,
        "Remove accents, strobes, junction devices and matte flashes by id "
        "(get_edl fx), or ids='all'.",
        {"ids": {"type": ["array", "string"], "items": {"type": "string"}}}),
}

REQUIRED_ARGS = {"add_accent": ["kind"], "add_strobe": ["start", "end"],
                 "set_junction": ["style"], "remove_punctuation": ["ids"]}

WRITE_TOOLS = {"add_accent", "add_strobe", "set_junction",
               "remove_punctuation"}

COMPACT = {
    "add_accent": (
        "1-N-frame accents (negative, white_hit, colour_hit, threshold, "
        "posterize, mono, mirror, hue_step, rgb_slice, box_invert; "
        "matte_flash = subject fill). at= seconds, or on=stress|word|"
        "phrase_end|cut (beat/downbeat/onset only on the user's track) in "
        "start/end with density sparse|pulse|accelerate|8ths|16ths. "
        "scope='all' inverts type; region {shape:'card'}. <=3 flashes/s "
        "unless the user asked. No sound, no zoom."),
    "add_strobe": (
        "Frame-exact strobe over start-end: frames (media keys, 'src:<s>', "
        "'program', colours) or variant_set, hold 1-8 frames, pattern, "
        "negate none|alternate|all, fit cover|contain|register. Timeline-"
        "neutral. >3 changes/s needs the user's ask."),
    "set_junction": (
        "Cut device at real cuts (at= or cuts='scene'|'every'), duration-"
        "preserving: dissolve, fadegrays, pixelize, radial, slices, wipe_*, "
        "iris, columns, staircase, box_steps, bands, letter_window, "
        "matte_cut, double_exposure, pixel_stretch, hit_flash, whip_up/down, "
        "zoom_blur, radial_blur, datamosh; 'none' removes. origin centre|"
        "face|point:x,y|<motion id>. One per cut."),
    "remove_punctuation": "Remove accents/strobes/junctions by ids (or 'all').",
}

