"""Department ``compositing``: chroma key (green / blue screen) and the
clean-up of a screen's leftover on footage keyed before.

``chroma_key`` measures the footage itself (worker/chroma_key.py) and does
what that footage needs for "remove the green screen":

* a screen stands behind the subject (any colour strength, dim or unevenly
  lit): it is keyed with the measured colour and spread, the screen's cast
  is taken off a band of the foreground next to it (despill), and the new
  background goes under everything that is not the screen: the presenter,
  the candles, the desk, the props. Before it writes, the render's own chain
  is dry-run on sampled frames over black and over white; their difference
  is the key's real transparency, so the result says how much of the
  screen still shows and how much of the subject was keyed away. A key that
  leaves screen is widened and measured again; one that eats the subject is
  narrowed and measured again.
* the footage was keyed before (p6311: a green line round the presenter,
  the flags and the fir sprigs, a green disc round the candle flame): the
  leftover is measured and cleaned (faint tint despilled, strong leftover
  filled from the picture around it). The footage's own backgrounds and
  every prop stay. A background the agent names explicitly is then laid
  behind the person cut-out as well, and the result says what that keeps.
* a backdrop of the screen's colour that reads less than a full screen (in
  part of the frames, dim, a tight shot) is keyed the same measured way;
  footage with no screen colour at all gets the background behind the
  person cut-out.

The key runs on the SOURCE clock, so every later trim, framing, grade and
caption sees the result.
"""

DEPARTMENT = "compositing"

SAMPLES = 8
KEY_PROBE = 270
FRINGE_PROBE = 480
RESIDUAL_OK = 0.005          # share of the measured screen still showing
SUBJECT_LOSS_OK = 0.01       # mean transparency of the subject
SIM_STEP = 0.03
NARROW_STEP, NARROW_MIN, NARROW_TRIES = 0.01, 0.02, 6
DEFAULT_BG = {"kind": "gradient", "colour": "#2b3140", "colour2": "#0d0f14"}


def _bg_spec(background, colour, colour2, asset_key):
    """(spec, named): the background to key onto, and whether the agent
    named one (False: the default gradient)."""
    kind = str(background or "").strip().lower() or None
    if asset_key and kind in (None, "asset", "image", "video", "source"):
        return {"kind": "asset", "asset_key": str(asset_key)}, True
    if kind == "solid" or (kind is None and colour and not colour2):
        return {"kind": "solid", "colour": colour or "#000000"}, True
    if kind == "gradient" or (kind is None and colour and colour2):
        return {"kind": "gradient", "colour": colour or DEFAULT_BG["colour"],
                "colour2": colour2 or colour or DEFAULT_BG["colour2"]}, True
    return dict(DEFAULT_BG), False


def _times(duration):
    d = max(0.5, float(duration or 0.0))
    return [round(d * (i + 0.5) / SAMPLES, 2) for i in range(SAMPLES)]


def _footage(ctx):
    try:
        return ctx.proxy_path()
    except Exception:  # noqa: BLE001 - the original is the same picture
        import agent_tools
        return agent_tools._original_local(ctx)


def _resolve_asset(ctx, bg, notes):
    """The asset key as this project holds it (a mistyped key repaired);
    None when the project has no such file."""
    if bg["kind"] != "asset":
        return bg
    db = getattr(ctx, "db", None)
    if db is None:                      # no project store here: as given
        return bg
    import agent_tools
    key, row = agent_tools._project_asset(ctx, bg["asset_key"], notes)
    if row is None:
        return None
    return dict(bg, asset_key=key)


def _person_background(ctx, bg):
    """The new background behind the person cut-out for the whole film
    (subject_tools.set_background). Returns (ok, text)."""
    from tools_ext import subject_tools
    edl = ctx.latest_edl()["json"]
    tl = subject_tools._timeline(edl)
    end = round(subject_tools._program_s(edl, tl), 2)
    if bg["kind"] == "asset":
        out = subject_tools.set_background(ctx, "source", 0.0, end,
                                           source=bg["asset_key"])
    elif bg["kind"] == "solid":
        out = subject_tools.set_background(ctx, "solid", 0.0, end,
                                           colour=bg.get("colour"))
    else:
        out = subject_tools.set_background(ctx, "gradient", 0.0, end,
                                           colour=bg.get("colour"),
                                           colour2=bg.get("colour2"))
    return str(out).startswith("EDL v"), str(out)


def _bg_words(bg):
    return {"solid": f"solid {bg.get('colour')}",
            "gradient": f"gradient {bg.get('colour')}->{bg.get('colour2')}",
            "asset": f"picture {bg.get('asset_key')}"}[bg["kind"]]


def _blend(ck, sim, tint):
    """chromakey's soft zone runs from ``sim`` to ``sim + blend``. A neutral
    picture (white candle, grey shirt) sits at the screen's tint from the
    key colour, so on a dim screen the soft zone must end before it."""
    return round(max(0.01, min(ck.DEFAULT_BLEND, (tint or 1.0) - sim - 0.01)),
                 3)


def _measured_key(ck, path, item, times, pinned, tint):
    """Dry-run the key and repair it: narrowed while it keys the subject
    away, widened while screen still shows (never into the subject).
    Returns (probe, changes)."""
    changes = []

    def run(sim):
        return ck.dry_run(path, dict(item, similarity=round(sim, 3),
                                     blend=_blend(ck, sim, tint)),
                          times, KEY_PROBE)

    def take(sim, probe, what):
        item["similarity"] = round(sim, 3)
        item["blend"] = _blend(ck, sim, tint)
        changes.append((what, item["similarity"]))
        return probe
    probe = run(item["similarity"])
    if probe["frames"] and probe["subject_lost"] > SUBJECT_LOSS_OK and \
            item["similarity"] > NARROW_MIN:
        # the widest width that keeps the subject, by bisection
        lo, hi, best = NARROW_MIN, item["similarity"], None
        for _ in range(NARROW_TRIES):
            mid = round((lo + hi) / 2.0, 3)
            trial = run(mid)
            if trial["subject_lost"] <= SUBJECT_LOSS_OK:
                lo, best = mid, (mid, trial)
            else:
                hi = mid
            if hi - lo < NARROW_STEP:
                break
        if best is None:
            best = (NARROW_MIN, run(NARROW_MIN))
        probe = take(best[0], best[1], "narrowed")
    if changes or pinned:
        return probe, changes
    while probe["frames"] and probe["left"] > RESIDUAL_OK and \
            item["similarity"] + SIM_STEP <= ck.SIMILARITY_MAX:
        sim = item["similarity"] + SIM_STEP
        trial = run(sim)
        if trial["subject_lost"] > SUBJECT_LOSS_OK:
            break
        probe = take(sim, trial, "widened")
    return probe, changes


def _key(ctx, ck, edl, fx, path, times, reading, bg, similarity, mix):
    pinned = False
    sim = reading.get("similarity") or 0.12
    if similarity is not None:
        try:
            sim = min(max(float(similarity), ck.SIMILARITY_MIN),
                      ck.SIMILARITY_MAX)
            pinned = True
        except (TypeError, ValueError):
            pass
    tint = reading.get("chroma")
    item = {"id": "key1", "mode": "key", "colour": reading["colour"],
            "similarity": round(sim, 3), "blend": _blend(ck, sim, tint),
            "despill": round(mix, 3), "spill": reading["screen"],
            "background": bg}
    try:
        probe, changes = _measured_key(ck, path, item, times, pinned,
                                       tint)
    except Exception as e:  # noqa: BLE001 - the measurement is a report
        probe, changes = {"error": str(e)[:200]}, []
    fx["key"] = item
    edl["effects"] = fx
    out = ctx.write_edl(edl, f"chroma key: {reading['screen']} screen "
                             f"{reading['colour']} over {_bg_words(bg)}")
    if not out.startswith("EDL v"):
        return out
    if probe.get("frames"):
        note = (f"\nMeasured on {probe['frames']} sampled frames: the "
                f"{reading['screen']} screen ({reading['colour']}) covers "
                f"{probe['screen'] * 100:.1f}% of the picture; after the key "
                f"{probe['left'] * 100:.2f}% of it still shows and "
                f"{probe['subject_lost'] * 100:.2f}% of the subject is keyed "
                f"away (similarity {item['similarity']:g}, despill "
                f"{item['despill']:g} on the band next to the screen).")
        for what, s in changes[-1:]:
            note += (f" The key was {what} to similarity {s:g} and measured "
                     "again" + (" (the first width keyed part of the "
                                "subject)." if what == "narrowed" else
                                " (the first width left screen)."))
        if probe["left"] > RESIDUAL_OK:
            note += (" Some of the screen is darker or differently lit than "
                     "the rest: pass a wider similarity to take it, and look "
                     "at the subject's edges on the next preview.")
        if probe["subject_lost"] > SUBJECT_LOSS_OK:
            note += (" Part of the subject shares the screen's colour (green "
                     "clothes or props): say so to the user.")
    else:
        note = ("\nThe key is written; its dry run could not be measured "
                f"here ({probe.get('error', 'no frames')}). Look at it on "
                "the next preview with look_at.")
    return (out + note + " Everything that is not the screen stays: the "
            "subject, props, candles, the desk. Say the numbers to the user.")


def _fringe(ctx, ck, edl, fx, path, times, reading, fr, mix):
    item = {"id": "key1", "mode": "fringe", "colour": fr.get("colour"),
            "spill": fr["screen"], "excess": fr.get("excess") or 23.0,
            "despill": round(mix, 3)}
    try:
        probe = ck.dry_run(path, item, times, FRINGE_PROBE)
    except Exception as e:  # noqa: BLE001 - the measurement is a report
        probe = {"error": str(e)[:200]}
    fx["key"] = item
    edl["effects"] = fx
    out = ctx.write_edl(edl, f"cleaned the {fr['screen']} screen's leftover "
                             f"({fr.get('colour')}) off the edges")
    if not out.startswith("EDL v"):
        return out, False
    if probe.get("frames"):
        note = (f"\nMeasured on {probe['frames']} sampled frames: no "
                f"{fr['screen']} screen stands behind the subject (the "
                "footage was keyed before), but its leftover does: "
                f"{probe['before'] * 100:.2f}% of the picture read "
                f"{fr['screen']}-tinted round the edges before, "
                f"{probe['after'] * 100:.2f}% after the clean, which "
                f"touched {probe['changed'] * 100:.1f}% of the picture. "
                "Lines round the subject and the props are despilled, a "
                "thicker leftover (a halo round a flame or a lamp) is "
                "filled from the picture around it; the footage's own "
                "backgrounds, the presenter and every prop stay.")
    else:
        note = ("\nThe clean is written; its dry run could not be measured "
                f"here ({probe.get('error', 'no frames')}). Look at it on "
                "the next preview with look_at.")
    return out + note, True


def chroma_key(ctx, background=None, colour=None, colour2=None,
               asset_key=None, mode=None, screen=None, similarity=None,
               despill=None, remove=False):
    """WRITE: key the footage's green/blue screen onto a new background, or
    clean a screen's leftover off footage keyed before."""
    import chroma_key as ck
    if not getattr(ctx, "has_main_video", False):
        return ("This project is a canvas of pictures with no main footage "
                "to key. Key a clip by uploading it as the main video.")
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    if remove:
        if not fx.get("key"):
            return "NO CHANGE — there is no chroma key on this edit."
        fx.pop("key", None)
        edl["effects"] = fx
        return ctx.write_edl(edl, "removed the chroma key")
    notes = []
    bg, named = _bg_spec(background, colour, colour2, asset_key)
    bg = _resolve_asset(ctx, bg, notes)
    if bg is None:
        return (f"NO CHANGE — {asset_key!r} is not a file of this project. "
                "Pass an asset_key from list_assets (an upload, or a "
                "fetch_url download), or a solid / gradient background.")
    try:
        mix = min(max(float(despill), 0.0), 1.0) if despill is not None \
            else ck.DEFAULT_DESPILL
    except (TypeError, ValueError):
        mix = ck.DEFAULT_DESPILL
    screens = (screen,) if screen in ("green", "blue") else ("green", "blue")
    want = mode if mode in ("key", "fringe") else None
    path = _footage(ctx)
    times = _times(getattr(ctx, "duration", None))
    frames = ck.sample_frames(path, times, FRINGE_PROBE)
    if not frames:                    # the proxy unreadable: the original
        try:
            import agent_tools
            path = agent_tools._original_local(ctx)
            frames = ck.sample_frames(path, times, FRINGE_PROBE)
        except Exception:  # noqa: BLE001 - said below
            frames = []
    if not frames:
        return ("The footage's frames could not be read on this lane, so "
                "the screen could not be measured; nothing was written. Try "
                "chroma_key again; if it repeats, tell the user.")
    reading = ck.measure_frames(frames, screens)
    fr = reading.get("fringe")
    if want == "fringe" and not fr:
        fr = ck.measure_fringe(frames, screens) or {
            "screen": screens[0], "colour": None, "excess": 23.0}
    head = "\n".join(notes) + ("\n" if notes else "")
    go_key = want == "key" or (want is None and reading.get("keyable"))
    if go_key and reading.get("colour"):
        if not reading.get("keyable"):
            head += (f"Keyed as asked: the {reading['screen']} backdrop "
                     f"({reading['colour']}) covers {reading['share'] * 100:.1f}"
                     f"% of the sampled picture, in "
                     f"{reading.get('presence', 0) * 100:.0f}% of the frames.\n")
        return head + _key(ctx, ck, edl, fx, path, times, reading, bg,
                           similarity, mix)
    if fr:
        out, ok = _fringe(ctx, ck, edl, fx, path, times, reading, fr, mix)
        if not ok or not named:
            return head + out + (
                "" if not ok else
                " No new background was laid: the footage's own pictures "
                "stay. If the user wants one, call chroma_key again with the "
                "background, which then goes behind the person cut-out.")
        placed, said = _person_background(ctx, bg)
        if placed:
            return (head + out + "\n" + said + "\nThe new background ("
                    f"{_bg_words(bg)}) is laid behind the person cut-out for "
                    "the whole film. This footage has no screen left to key, "
                    "so props in front of its old background (candles, "
                    "flags, a desk) go with that background; pass a region "
                    "to set_background to keep them. Say this to the user.")
        return (head + out + "\nThe new background could not be laid "
                f"behind the person: {said}")
    if reading.get("colour") and \
            float(reading.get("share") or 0.0) >= ck.BACKDROP_MIN_SHARE:
        # A backdrop of the screen's colour that reads less than a full
        # screen (in part of the frames, dim, a tight shot): the user asked
        # for it gone, so it is keyed; the dry run below narrows the key
        # wherever it would take the subject.
        head += (f"Keyed: the {reading['screen']} backdrop "
                 f"({reading['colour']}) covers {reading['share'] * 100:.1f}% "
                 "of the sampled picture, in "
                 f"{reading.get('presence', 0) * 100:.0f}% of the frames.\n")
        return head + _key(ctx, ck, edl, fx, path, times, reading, bg,
                           similarity, mix)
    # No screen colour in the footage at all: the background behind the
    # person is what the user wants gone, so the person cut-out takes it.
    placed, said = _person_background(ctx, bg)
    if placed:
        return (head + said + "\nMeasured on "
                f"{len(frames)} frames: no green or blue screen and no "
                "screen-coloured leftover in this footage, so the "
                + ("" if named else "neutral ")
                + f"background ({_bg_words(bg)}) is laid behind the person "
                "cut-out for the whole film; props in front of the old "
                "background go with it (pass a region to set_background "
                "to keep them"
                + ("" if named else "; pass a background to choose "
                   "another") + "). Say this to the user.")
    return head + said


TOOL_SPECS = {
    "chroma_key": (
        chroma_key,
        "Green screen / blue screen / chroma key, measured on the footage. "
        "A screen behind the subject (dim or bright): keyed out, edges "
        "despilled, everything else (the person, candles, props, desk) "
        "composited over a new background: 'solid' (colour), 'gradient' "
        "(colour -> colour2, top to bottom) or 'asset' (asset_key: an image "
        "or video from list_assets). Footage keyed before, with a green "
        "line or halo left round the subject and props: the leftover is "
        "cleaned and the footage's own backgrounds stay (a background you "
        "pass then goes behind the person cut-out). The result reports "
        "measured numbers (screen left, subject kept, leftover before and "
        "after): say them. mode 'key' forces a key, 'fringe' forces the "
        "leftover clean; screen 'green'|'blue' when the user named it. "
        "remove=true takes it off.",
        {"background": {"type": "string",
                        "enum": ["solid", "gradient", "asset"]},
         "colour": {"type": "string", "description": "#rrggbb"},
         "colour2": {"type": "string", "description": "#rrggbb"},
         "asset_key": {"type": "string"},
         "mode": {"type": "string", "enum": ["auto", "key", "fringe"]},
         "screen": {"type": "string", "enum": ["green", "blue"]},
         "similarity": {"type": "number",
                        "description": "0.04-0.4; default measured"},
         "despill": {"type": "number", "description": "0-1, default 0.6"},
         "remove": {"type": "boolean"}}),
}

REQUIRED_ARGS = {"chroma_key": []}

WRITE_TOOLS = {"chroma_key"}

COMPACT = {
    "chroma_key": (
        "Green/blue screen: a screen behind the subject is keyed (measured, "
        "despilled edges, props kept) over background solid|gradient|asset; "
        "footage keyed before gets its green line/halo cleaned, its own "
        "backgrounds kept. Reports measured numbers. mode key|fringe, "
        "screen green|blue."),
}
