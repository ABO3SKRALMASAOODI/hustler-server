"""Department ``compositing``: chroma key (green / blue screen).

``chroma_key`` measures the backdrop on the footage itself (worker/chroma_key.py),
keys it with that measured colour and spread, takes the screen's colour
cast off the subject's edges (despill) and composites a new background
under everything that is not the screen: the presenter, the candles, the
desk, the props. The key runs on the SOURCE clock, so every later trim,
framing, grade and caption sees the composite. Before it writes, the
render's own chain is dry-run on sampled frames and the backdrop's share of
the picture is measured before and after; the result reports both numbers.

Footage with no backdrop to key (it was keyed before and only a spill line
remains, or it never had a screen) still gets the new background: the tool
puts it behind the person cut-out (set_background) for the whole film and
says what it measured. A key that leaves backdrop behind is widened and
measured again, never handed back undone.
"""

DEPARTMENT = "compositing"

SAMPLES = 8
RESIDUAL_OK = 0.004          # share of the picture still reading as screen
WIDEN_STEP = 0.03
DEFAULT_BG = {"kind": "gradient", "colour": "#2b3140", "colour2": "#0d0f14"}


def _bg_spec(background, colour, colour2, asset_key):
    kind = str(background or "").strip().lower() or None
    if asset_key and kind in (None, "asset", "image", "video", "source"):
        return {"kind": "asset", "asset_key": str(asset_key)}
    if kind == "solid" or (kind is None and colour and not colour2):
        return {"kind": "solid", "colour": colour or "#000000"}
    if kind == "gradient" or (kind is None and colour and colour2):
        return {"kind": "gradient", "colour": colour or DEFAULT_BG["colour"],
                "colour2": colour2 or colour or DEFAULT_BG["colour2"]}
    return dict(DEFAULT_BG)


def _times(duration):
    d = max(0.5, float(duration or 0.0))
    return [round(d * (i + 0.5) / SAMPLES, 2) for i in range(SAMPLES)]


def _footage(ctx):
    try:
        return ctx.proxy_path()
    except Exception:  # noqa: BLE001 - the original is the same picture
        import agent_tools
        return agent_tools._original_local(ctx)


def _person_background(ctx, bg, reading):
    """No screen to key: the new background goes behind the person cut-out
    for the whole film (the existing subject-matte path)."""
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
    measured = (f"Measured on {reading.get('frames', SAMPLES)} frames: no "
                "green or blue screen to key (the most screen-like colour, "
                f"{reading['screen']}, covers {reading['share'] * 100:.1f}% "
                "of the picture at colour "
                f"strength {reading.get('chroma', 0):.2f}; a lit screen is "
                "0.18-0.21): this footage was keyed before or shot on a real "
                "set. ")
    return (out + "\n" + measured + "The new background is laid behind the "
            "person cut-out for the whole film instead (props in front of "
            "the old backdrop go with it; pass a region to set_background to "
            "keep a desk or table). Say this plainly to the user.")


def chroma_key(ctx, background=None, colour=None, colour2=None,
               asset_key=None, similarity=None, despill=None, remove=False):
    """WRITE: key the footage's green/blue screen onto a new background."""
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
    bg = _bg_spec(background, colour, colour2, asset_key)
    path = _footage(ctx)
    times = _times(getattr(ctx, "duration", None))
    frames = ck.sample_frames(path, times)
    reading = ck.measure_frames(frames)
    reading["frames"] = len(frames)
    if not reading.get("keyable"):
        return _person_background(ctx, bg, reading)
    sim = reading["similarity"]
    if similarity is not None:
        try:
            sim = min(max(float(similarity), ck.SIMILARITY_MIN),
                      ck.SIMILARITY_MAX)
        except (TypeError, ValueError):
            pass
    try:
        mix = min(max(float(despill), 0.0), 1.0) if despill is not None \
            else 0.6
    except (TypeError, ValueError):
        mix = 0.6
    item = {"id": "key1", "colour": reading["colour"],
            "similarity": round(sim, 3), "blend": ck.DEFAULT_BLEND,
            "despill": round(mix, 3), "spill": reading["screen"],
            "background": bg}
    widened = []
    probe = None
    try:
        probe = ck.dry_run(path, item, times)
        while probe["after"] > RESIDUAL_OK and \
                item["similarity"] + WIDEN_STEP <= ck.SIMILARITY_MAX:
            item["similarity"] = round(item["similarity"] + WIDEN_STEP, 3)
            widened.append(item["similarity"])
            probe = ck.dry_run(path, item, times)
    except Exception as e:  # noqa: BLE001 - the measurement is a report
        probe = {"error": str(e)[:200]}
    fx["key"] = item
    edl["effects"] = fx
    what = {"solid": f"solid {bg.get('colour')}",
            "gradient": f"gradient {bg.get('colour')}->{bg.get('colour2')}",
            "asset": f"picture {bg.get('asset_key')}"}[bg["kind"]]
    out = ctx.write_edl(edl, f"chroma key: {reading['screen']} screen "
                             f"{reading['colour']} over {what}")
    if not out.startswith("EDL v"):
        return out
    if probe and "after" in probe:
        note = (f"\nMeasured on {probe['frames']} sampled frames: "
                f"{reading['screen']} screen {probe['before'] * 100:.1f}% of "
                f"the picture before, {probe['after'] * 100:.2f}% after the "
                f"key (similarity {item['similarity']:g}, despill "
                f"{item['despill']:g}).")
        if widened:
            note += (" The first key left screen behind; it was widened to "
                     f"{item['similarity']:g} and measured again.")
    else:
        note = ("\nThe key is written; its dry run could not be measured "
                f"here ({(probe or {}).get('error', 'no frames')}). Check it "
                "on the next preview with look_at.")
    return (out + note + " Everything that is not the screen stays: the "
            "subject, props, candles, the desk. Look at the edges on the next "
            "preview; despill 0.8-1.0 takes a stronger green cast off hair.")


TOOL_SPECS = {
    "chroma_key": (
        chroma_key,
        "Green screen / blue screen / chroma key: remove the coloured "
        "backdrop of the main footage and composite everything else (the "
        "person, candles, props, desk) over a new background, edges cleaned "
        "of the screen's colour cast (despill). The backdrop colour and key "
        "width are MEASURED on the footage; the key is dry-run and the "
        "result says how much of the picture read as screen before and "
        "after. background: 'solid' (colour), 'gradient' (colour -> "
        "colour2, top to bottom) or 'asset' (asset_key: an image or video "
        "from list_assets: an upload or a fetch_url download). Footage "
        "with no screen left (keyed before) gets the background behind the "
        "person cut-out instead, and the result says so. remove=true takes "
        "the key off.",
        {"background": {"type": "string",
                        "enum": ["solid", "gradient", "asset"]},
         "colour": {"type": "string", "description": "#rrggbb"},
         "colour2": {"type": "string", "description": "#rrggbb"},
         "asset_key": {"type": "string"},
         "similarity": {"type": "number",
                        "description": "0.06-0.4; default measured"},
         "despill": {"type": "number", "description": "0-1, default 0.6"},
         "remove": {"type": "boolean"}}),
}

REQUIRED_ARGS = {"chroma_key": []}

WRITE_TOOLS = {"chroma_key"}

COMPACT = {
    "chroma_key": (
        "Green/blue screen key: measured backdrop removed, subject and props "
        "composited over background solid|gradient|asset (asset_key), "
        "despill cleans edges; reports screen share before/after. No screen "
        "left -> background behind the person cut-out."),
}
