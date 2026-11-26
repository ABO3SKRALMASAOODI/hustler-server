"""Department ``picture``: camera shake and meme inserts, each under the name
a customer's words point to.

``stabilize_video``: p6311 asked to "remove the camera shake". Stabilizing
existed only as one kind of add_stylize, a name nothing about shake points
to, so the agent never loaded it and the ask ended "cannot tell, no step".
This tool measures the camera's frame-to-frame motion on the footage, writes
the whole-video stabilize pass (renderer.stabilize_filter, the very chain the
render runs) and reports the jitter before and after that chain, measured on
the same frames.

``find_meme``: #935 "put memes in it" was skipped: no tool's name said meme,
and find_footage's own guidance steers away from meme frames and reaction
clips. This searches short meme / reaction clips (YouTube, the same search and
fetch path as find_footage) and shows their thumbnails, so the agent picks one
by looking and drops it on the punchline as a short insert.
"""

import subprocess

import numpy as np

DEPARTMENT = "picture"

PROBE_S = 4.0
PROBE_SIZE = 256


def _footage(ctx):
    try:
        return ctx.proxy_path()
    except Exception:  # noqa: BLE001 - the original is the same picture
        import agent_tools
        return agent_tools._original_local(ctx)


def _frames(path, start, dur, vf=None, size=PROBE_SIZE):
    chain = (vf + "," if vf else "") + f"scale={size}:{size},format=gray"
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t",
         f"{dur:.3f}", "-i", path, "-vf", chain, "-an", "-f", "rawvideo",
         "-"], capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode(errors="replace")[-240:])
    n = len(r.stdout) // (size * size)
    return np.frombuffer(r.stdout[:n * size * size], np.uint8).reshape(
        n, size, size).astype(np.float32)


def _subpixel(c, i, n):
    """Parabolic refinement of a correlation peak along one axis."""
    a, b, d = c[(i - 1) % n], c[i], c[(i + 1) % n]
    den = a - 2 * b + d
    return 0.0 if abs(den) < 1e-12 else 0.5 * (a - d) / den


def jitter(frames):
    """Mean global frame-to-frame shift in pixels of the probe size (phase
    correlation, sub-pixel), and the share of steps that moved > 0.5 px."""
    if len(frames) < 3:
        return None
    size = frames.shape[1]
    win = np.outer(np.hanning(size), np.hanning(size)).astype(np.float32)
    shifts = []
    prev = np.fft.fft2((frames[0] - frames[0].mean()) * win)
    for f in frames[1:]:
        cur = np.fft.fft2((f - f.mean()) * win)
        r = prev * np.conj(cur)
        r /= np.abs(r) + 1e-9
        c = np.fft.ifft2(r).real
        y, x = np.unravel_index(int(np.argmax(c)), c.shape)
        dy = y + _subpixel(c[:, x], y, size)
        dx = x + _subpixel(c[y, :], x, size)
        if dy > size / 2:
            dy -= size
        if dx > size / 2:
            dx -= size
        shifts.append(float(np.hypot(dx, dy)))
        prev = cur
    s = np.array(shifts)
    return {"mean_px": round(float(s.mean()), 3),
            "moving_share": round(float((s > 0.5).mean()), 3)}


def measure(path, start, dur, intensity):
    before = jitter(_frames(path, start, dur))
    after = jitter(_frames(path, start, dur, _stabilize_chain(intensity)))
    return before, after


STILL_PX = 0.3          # 1080p px per frame: below this the camera holds
TRY_STRENGTHS = (0.3, 0.5, 0.8)


def stabilize_video(ctx, strength=None):
    """WRITE: the whole-video stabilize pass, measured before it is written.

    The jitter is measured first. Footage that holds still gets no pass
    (deshake there only adds render time and mirrored-edge wobble round a
    moving presenter). Otherwise the pass at the asked strength is measured,
    and when it does not lower the jitter the other strengths are measured
    too and the one that steadies most is written."""
    import agent_tools
    if not getattr(ctx, "has_main_video", False):
        return ("This project is a canvas of pictures and clips with no main "
                "footage; there is no camera to steady.")
    try:
        asked = None if strength is None else \
            min(max(float(strength), 0.05), 1.0)
    except (TypeError, ValueError):
        asked = None
    dur = float(getattr(ctx, "duration", 0.0) or 0.0)
    start = max(0.0, dur / 2.0 - PROBE_S / 2.0)
    span = min(PROBE_S, dur or PROBE_S)
    scale = 1080.0 / PROBE_SIZE
    path = _footage(ctx)
    try:
        before = jitter(_frames(path, start, span))
    except Exception as e:  # noqa: BLE001 - measured below or written blind
        before, err = None, str(e)[:160]
    else:
        err = None
    where = f"{span:g} s of the footage from {start:.1f} s"
    if before and before["mean_px"] * scale < STILL_PX:
        return (f"NO CHANGE — measured on {where}: camera jitter "
                f"{before['mean_px'] * scale:.2f} px per frame (1080p scale); "
                "the camera already holds still, so there is no shake to "
                "remove and no pass was added. Tell the user the footage "
                "has no measurable camera shake.")
    tries = [asked if asked is not None else 0.5]
    results = {}
    best = None
    if before:
        for inten in tries + [x for x in TRY_STRENGTHS if x not in tries]:
            try:
                after = jitter(_frames(path, start, span,
                                       _stabilize_chain(inten)))
            except Exception as e:  # noqa: BLE001 - next strength
                err = str(e)[:160]
                continue
            if not after:
                continue
            results[inten] = after
            if best is None or after["mean_px"] < results[best]["mean_px"]:
                best = inten
            if inten == tries[0] and after["mean_px"] < before["mean_px"]:
                break
        if best is not None and \
                results[best]["mean_px"] >= before["mean_px"]:
            tried = ", ".join(f"{k:g}: {v['mean_px'] * scale:.2f} px"
                              for k, v in results.items())
            return (f"NO CHANGE — measured on {where}: camera jitter "
                    f"{before['mean_px'] * scale:.2f} px per frame; the "
                    f"stabilize pass did not lower it at any strength "
                    f"({tried}). This movement is not handheld wobble (a pan, "
                    "a walk, a whip or rolling shutter): deshake cannot "
                    "steady it. Tell the user exactly that.")
    inten = best if best is not None else tries[0]
    out = _write_pass(ctx, agent_tools, inten)
    if not (out.startswith("EDL v") or out.startswith("NO CHANGE")):
        return out
    if not before or best is None:
        return (out + "\nThe pass is on the whole video; its jitter could "
                f"not be measured here ({err or 'too few frames'}).")
    b = before["mean_px"] * scale
    a = results[inten]["mean_px"] * scale
    note = (f"\nMeasured on {where}: camera jitter {b:.2f} px per frame "
            f"before, {a:.2f} px after (1080p scale; frames moving > 0.5 px: "
            f"{before['moving_share'] * 100:.0f}% -> "
            f"{results[inten]['moving_share'] * 100:.0f}%), strength "
            f"{inten:g}.")
    if asked is not None and inten != asked:
        note += (f" Strength {asked:g} measured "
                 f"{results[asked]['mean_px'] * scale:.2f} px, so {inten:g} "
                 "was written." if asked in results else "")
    return (out + note + " Deshake smooths handheld wobble; a whip pan, a "
            "walk or rolling shutter stays.")


def _stabilize_chain(inten):
    import renderer
    return renderer.stabilize_filter(inten)


def _write_pass(ctx, agent_tools, inten):
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    sts = [dict(s) for s in fx.get("stylize") or [] if isinstance(s, dict)]
    old = next((s for s in sts if s.get("kind") == "stabilize"), None)
    if old is None:
        return agent_tools.add_stylize(ctx, "stabilize", None, None, inten)
    # one pass: a second deshake on the first one's output only softens
    old["intensity"] = round(inten, 3)
    fx["stylize"] = sts
    edl["effects"] = fx
    out = ctx.write_edl(edl, f"stabilize pass strength {inten:g} "
                             f"[{old.get('id')}]")
    return out


def find_meme(ctx, query):
    """READ: candidate meme / reaction clips for a punchline."""
    import agent_tools
    import song_find
    if not song_find.footage_available():
        return ("Meme search runs on the web footage search, which is off on "
                "this deployment. Use search_stock for a reaction image, a "
                "generated image, or a link the user pastes (fetch_url).")
    q = " ".join(str(query or "").split())
    if not q:
        return ("Name the meme or the reaction to find ('surprised pikachu', "
                "'this is fine dog', 'crowd cheering reaction').")
    try:
        hits = agent_tools._search_named_youtube(ctx, q + " meme", "footage")
    except song_find.SongFindError as e:
        return (f"Meme search failed ({e}). search_stock (reaction images) "
                "and generated images still work; tell the user if none fits.")
    hits = song_find.rank_memes(hits)
    if not hits:
        return (f"No meme clips found for \"{q}\". Try the meme's common "
                "name, or a reaction ('shocked face', 'slow clap').")
    lines = "\n- ".join(song_find.describe(h) for h in hits[:5])
    seen = agent_tools._queue_candidate_thumbs(ctx, hits)
    eye = ("\nTheir thumbnails are attached below, labeled by link: pick the "
           "clip whose picture IS the meme (the recognisable face, animal or "
           "scene), not a compilation or a person talking about it. "
           if seen else "\n")
    return (f"{min(len(hits), 5)} meme clip(s) for \"{q}\", shortest and "
            "closest first:\n- " + lines + eye +
            "Then fetch_url(url, as_kind='clip'), look_at_asset to find the "
            "meme's 1-3 seconds, and place it as an insert (or a "
            "picture-in-picture overlay) right on the line it answers, with "
            "its sound when the sound is the joke. One meme per beat that "
            "earns it. Tell the user which clips you used (title + channel).")


TOOL_SPECS = {
    "stabilize_video": (
        stabilize_video,
        "Remove camera shake: a whole-video stabilize pass (deshake), "
        "measured first. The result reports the camera's frame-to-frame "
        "jitter before and after the pass on the same frames, so say the "
        "numbers. Footage that already holds still gets no pass (the "
        "result says so); a strength that does not steady it is replaced "
        "by one that does. strength 0.05-1 (default 0.5). Smooths handheld "
        "wobble; a whip pan, a walk or rolling shutter stays. One pass per "
        "edit (a new call replaces it).",
        {"strength": {"type": "number"}}),
    "find_meme": (
        find_meme,
        "Find MEME and reaction clips for a punchline ('put memes in it', "
        "'add funny reactions'): searches short meme clips by the meme's "
        "name or the reaction ('surprised pikachu', 'this is fine dog', "
        "'slow clap'), shortest first, with thumbnails to pick by looking. "
        "Then fetch_url(url, as_kind='clip') and place 1-3 s as an insert "
        "or overlay on the line it answers.",
        {"query": {"type": "string"}}),
}

REQUIRED_ARGS = {"stabilize_video": [], "find_meme": ["query"]}

WRITE_TOOLS = {"stabilize_video"}


def _footage_off():
    import song_find
    return not song_find.footage_available()


DISABLED = {"find_meme": _footage_off}

COMPACT = {
    "stabilize_video": (
        "Camera shake: whole-video stabilize pass; reports measured jitter "
        "before/after. strength 0.05-1."),
    "find_meme": (
        "Meme/reaction clips by name ('surprised pikachu', 'slow clap'), "
        "shortest first with thumbnails; then fetch_url as clip and insert "
        "1-3 s on the punchline."),
}
