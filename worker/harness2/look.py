"""look(): the model's eyes and ears on anything (HARNESS_V2.md 2.1).

Targets: the source, the program (the cut assembled from source frames, so
no render is needed), the render (real pixels) and any uploaded asset.
Every answer states what it covers. Nothing refuses: times are clamped and
said; a render that does not exist yet is started while program frames come
back now. Every read is today's own function (look_at, _look_at_output,
look_at_asset, open_visual_page, read_tools, find_silences,
get_audio_analysis, find_activity, find_visual_moments, read_skill).
"""
import math

import agent_tools
import read_tools
import timeline as timeline_mod

from harness2 import envelope
from harness2 import measure as measure_mod
from harness2.stage import Stage

PAGE_TILES = 16
MAX_PAGES = 6
JUNCTION_PAIRS_PER_PAGE = 8
JUNCTION_GAP_S = 0.3
REMOVED_TILES = 32


def _drain_images(ctx):
    images = list(getattr(ctx, "pending_images", None) or [])
    try:
        ctx.pending_images = []
    except AttributeError:
        pass
    return images


def _parse_target(target):
    target = str(target or "source").strip()
    if target.startswith("asset:"):
        return "asset", target.split(":", 1)[1].strip()
    if target in ("source", "program", "render"):
        return target, None
    return "source", None


def _numbers(values):
    out = []
    for v in values if isinstance(values, (list, tuple)) else [values]:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def _clamp_times(times, lo, hi, notes):
    out, moved = [], 0
    for t in times:
        c = min(max(t, lo), max(lo, hi - 0.04))
        if abs(c - t) > 1e-6:
            moved += 1
        out.append(round(c, 3))
    if moved:
        notes.append(f"{moved} time(s) outside {lo:g}-{hi:g} s held to the "
                     "nearest edge")
    return out


def _span_times(lo, hi, every=None, tiles=None, fps=None):
    length = max(0.0, hi - lo)
    if length <= 0:
        return [lo]
    if fps:
        step = 1.0 / max(0.1, float(fps))
    elif every:
        step = max(0.04, float(every))
    else:
        n = int(tiles or PAGE_TILES)
        step = length / max(1, n)
    times, t = [], lo + step / 2.0 if not fps else lo
    cap = PAGE_TILES * MAX_PAGES
    while t < hi and len(times) < cap:
        times.append(round(t, 3))
        t += step
    return times


def _program_length(ctx, edl=None):
    edl = edl if edl is not None else ctx.latest_edl()["json"]
    try:
        return float(timeline_mod.Timeline(
            edl.get("keep") or [], edl.get("inserts") or [],
            edl.get("speed") or []).out_duration or 0.0)
    except Exception:  # noqa: BLE001
        from schemas import program_duration
        return float(program_duration(edl) or 0.0)


def _pages(times):
    for i in range(0, len(times), PAGE_TILES):
        yield times[i:i + PAGE_TILES]


def _frames_source(ctx, times):
    texts = []
    for page in _pages(times):
        texts.append(str(agent_tools.look_at(ctx, times=page)))
    return texts


def _frames_program(ctx, times, version_ctx=None):
    texts = []
    who = version_ctx or ctx
    for page in _pages(times):
        texts.append(str(agent_tools._look_at_output(who, page, "")))
        if who is not ctx:
            ctx.pending_images.extend(_drain_images(who))
    return texts


def _frames_asset(ctx, key, times):
    texts = []
    for page in _pages(times):
        texts.append(str(agent_tools.look_at_asset(ctx, key, times=page)))
    return texts


def _version_ctx(ctx, version):
    """A read-only view of ``version`` for program looks (the head when
    None)."""
    if version is None:
        return None, ctx.latest_edl()
    import db as dbx
    row = ctx.db.run(dbx.get_edl_version, ctx.project_id, int(version))
    if not row:
        return None, None
    head = ctx.latest_edl()
    if int(row["version"]) == int(head["version"]):
        return None, head
    return Stage(ctx, row["json"], row["version"]), row


def _render_row(ctx, version):
    return ctx.db.run(read_tools.find_render_asset, ctx.project_id,
                      "preview", int(version))


def _start_render(ctx):
    from harness2 import render_tool
    try:
        return render_tool.start_preview(ctx)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


RENDER_WAIT_S = 45.0


def _await_render(ctx, version):
    """The rendered preview of ``version``: started if needed and waited on
    for up to RENDER_WAIT_S (a preview takes seconds). (row, None) once it
    exists; (None, running) when it is still going."""
    from harness2 import render_tool
    started = _start_render(ctx)
    if started.get("asset"):
        return started["asset"], None
    job_id = started.get("job_id")
    if job_id is None or int(started.get("version") or -1) != int(version):
        return None, ({"handle": started.get("handle"),
                       "what": f"preview v{started.get('version', '?')}"}
                      if started.get("handle") else None)
    env = render_tool.settle(ctx, job_id, version, RENDER_WAIT_S)
    if env.running:
        return None, env.running
    return _render_row(ctx, version), None


def _env(body_parts, ctx, coverage, notes, findings=None, running=None,
         measured=None):
    body = "\n".join(p for p in body_parts if p)
    if notes:
        body = "\n".join(notes) + ("\n" + body if body else "")
    return envelope.read("look", body, see=_drain_images(ctx),
                         coverage=coverage, findings=findings,
                         running=running, measured=measured)


# --------------------------------------------------------------------------

def look(ctx, target="source", show="frames", times=None, range=None,
         every=None, fps=None, tiles=None, query=None, version=None):
    kind, key = _parse_target(target)
    show = str(show or "frames").strip()
    notes = []
    if show.startswith("skill:"):
        name = show.split(":", 1)[1]
        return envelope.read("look", str(agent_tools.read_skill(ctx, name)),
                             coverage=f"skill {name}")
    handler = {"frames": _show_frames, "sheet": _show_sheet,
               "junctions": _show_junctions, "removed": _show_removed,
               "chapters": _show_chapters, "transcript": _show_transcript,
               "shots": _show_shots, "audio": _show_audio,
               "activity": _show_activity, "moments": _show_moments,
               "rank": _show_rank}.get(show)
    if handler is None:
        notes.append(f"show {show!r} read as frames")
        handler = _show_frames
    return handler(ctx, kind, key, notes, times=times, rng=range,
                   every=every, fps=fps, tiles=tiles, query=query,
                   version=version)


def _bounds(ctx, kind, key, version=None):
    if kind in ("program", "render"):
        _vctx, row = _version_ctx(ctx, version)
        return 0.0, _program_length(ctx, (row or {}).get("json"))
    if kind == "asset":
        try:
            asset, _err = agent_tools._resolve_media_asset(
                ctx, key, ("video", "image", "music", "clip"))
            dur = agent_tools._asset_media_duration(ctx, asset) \
                if asset else None
        except Exception:  # noqa: BLE001
            dur = None
        return 0.0, float(dur or 1e9)
    return 0.0, float(getattr(ctx, "duration", 0.0) or 0.0)


def _range(rng, lo, hi, notes):
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        vals = _numbers(rng)
        if len(vals) == 2:
            a, b = sorted(vals)
            ca, cb = max(lo, a), min(hi, b)
            if (ca, cb) != (a, b):
                notes.append(f"range held to {ca:g}-{cb:g} s")
            return ca, cb
    return lo, hi


def _show_frames(ctx, kind, key, notes, times=None, rng=None, every=None,
                 fps=None, tiles=None, version=None, **_):
    lo, hi = _bounds(ctx, kind, key, version)
    wanted = _numbers(times) if times is not None else []
    if not wanted:
        a, b = _range(rng, lo, hi, notes)
        wanted = _span_times(a, b, every, tiles or 8, fps)
    wanted = _clamp_times(wanted, lo, hi, notes)
    return _frames_at(ctx, kind, key, wanted, notes, version)


def _frames_at(ctx, kind, key, wanted, notes, version=None, label=""):
    running = None
    if kind == "source":
        texts = _frames_source(ctx, wanted)
        coverage = f"source frames at {len(wanted)} time(s)"
    elif kind == "asset":
        texts = _frames_asset(ctx, key, wanted)
        coverage = f"asset {key}: {len(wanted)} frame(s)"
    elif kind == "render":
        vctx, row = _version_ctx(ctx, version)
        rendered = _render_row(ctx, row["version"]) if row else None
        if not rendered and row:
            rendered, running = _await_render(ctx, int(row["version"]))
        if rendered:
            texts = _frames_asset(ctx, rendered["storage_key"], wanted)
            coverage = (f"rendered v{row['version']}: {len(wanted)} "
                        "frame(s)")
        else:
            running = running or {
                "what": f"preview v{row['version'] if row else '?'}"}
            texts = _frames_program(ctx, wanted, vctx)
            # p5873: these frames were read as the render and a dip that
            # rendered fine was called missing. Say what they lack.
            coverage = ("NOT the render: the cut assembled from source "
                        "frames, without transitions, grades, captions, "
                        "texts or overlays; the render is still running "
                        f"({len(wanted)} frame(s))")
    else:
        vctx, row = _version_ctx(ctx, version)
        texts = _frames_program(ctx, wanted, vctx)
        coverage = (f"program v{row['version'] if row else '?'} assembled "
                    f"from source frames: {len(wanted)} frame(s)")
    if label:
        coverage = f"{label}; {coverage}"
    return _env([_strip(t) for t in texts], ctx, coverage, notes,
                running=running)


def _strip(text):
    """Today's look answers end with advice to the model; v2 keeps facts."""
    lines = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if s.startswith(("NEXT:", "Next:", "Tip:", "Do NOT", "Do not")):
            continue
        lines.append(line)
    return "\n".join(lines)[:4000]


def _show_sheet(ctx, kind, key, notes, times=None, rng=None, every=None,
                fps=None, tiles=None, version=None, **_):
    lo, hi = _bounds(ctx, kind, key, version)
    if kind == "source" and times is None and rng is None and not fps \
            and not every:
        storyboard = (ctx.index or {}).get("visual_storyboard") or {}
        if storyboard.get("sheets"):
            text = agent_tools.open_visual_page(ctx)
            return _env([_strip(text)], ctx,
                        f"stored storyboard pages over the whole source "
                        f"(0-{hi:g} s)", notes)
    if times is not None:
        wanted = _clamp_times(_numbers(times), lo, hi, notes)
    else:
        a, b = _range(rng, lo, hi, notes)
        wanted = _span_times(a, b, every, tiles, fps)
        if len(wanted) >= PAGE_TILES * MAX_PAGES:
            notes.append(f"sheet held to {len(wanted)} tiles "
                         f"({MAX_PAGES} pages); look again from "
                         f"{wanted[-1]:g} s for the rest")
    return _frames_at(ctx, kind, key, wanted, notes, version,
                      label=f"sheet of {len(wanted)} tile(s)")


def _cuts(edl):
    try:
        blocks = timeline_mod.program_blocks(edl)
    except Exception:  # noqa: BLE001
        return []
    return [b["out_start"] for b in blocks[1:]]


def _show_junctions(ctx, kind, key, notes, version=None, rng=None, **_):
    vctx, row = _version_ctx(ctx, version)
    if not row:
        return _env([f"there is no version {version}"], ctx, "nothing", notes)
    edl = row["json"]
    cuts = _cuts(edl)
    total = len(cuts)
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        a, b = sorted(_numbers(rng) or [0, 0])[:2] if len(
            _numbers(rng)) == 2 else (0, 0)
        cuts = [c for c in cuts if a <= c <= b]
    page = cuts[:JUNCTION_PAIRS_PER_PAGE * 2]
    if len(cuts) > len(page):
        notes.append(f"{len(cuts) - len(page)} more cut(s): pass range="
                     f"[{page[-1]:g}, ...] for the next page")
    length = _program_length(ctx, edl)
    times = []
    for c in page:
        times.extend([round(max(0.0, c - JUNCTION_GAP_S), 3),
                      round(min(length - 0.04, c + JUNCTION_GAP_S), 3)])
    if not times:
        return _env(["this version has no cuts"], ctx,
                    f"v{row['version']}: 0 cuts", notes)
    texts = _frames_program(ctx, times, vctx)
    body = [f"cuts at (film s): " + ", ".join(f"{c:g}" for c in page)]
    body += [_strip(t) for t in texts]
    return _env(body, ctx, f"v{row['version']}: {len(page)} of {total} "
                f"cut(s), frames {JUNCTION_GAP_S:g} s either side", notes)


def removed_ranges(prev_edl, new_edl):
    pk = [list(map(float, k)) for k in prev_edl.get("keep") or []]
    nk = [list(map(float, k)) for k in new_edl.get("keep") or []]
    return measure_mod._interval_sub(pk, nk)


def _show_removed(ctx, kind, key, notes, version=None, **_):
    import db as dbx
    head = ctx.latest_edl()
    v = int(version) if version is not None else int(head["version"])
    row = ctx.db.run(dbx.get_edl_version, ctx.project_id, v) \
        if v != int(head["version"]) else head
    prev = ctx.db.run(dbx.get_edl_version, ctx.project_id, v - 1) \
        if v > 1 else None
    if not row or not prev:
        return _env([f"v{v} has no earlier version to compare"], ctx,
                    "nothing removed", notes)
    gone = sorted(removed_ranges(prev["json"], row["json"]),
                  key=lambda r: r[1] - r[0], reverse=True)
    if not gone:
        return _env([f"v{v} removed no footage from v{v - 1}"], ctx,
                    f"v{v - 1} -> v{v}: nothing removed", notes)
    total = sum(b - a for a, b in gone)
    times, labels = [], []
    per = max(1, REMOVED_TILES // max(1, min(len(gone), REMOVED_TILES)))
    for a, b in gone[:REMOVED_TILES]:
        n = min(per, 3) if b - a > 20 else 1
        for k in range(n):
            times.append(round(a + (b - a) * (k + 0.5) / n, 2))
        labels.append(f"{a:.2f}-{b:.2f} ({b - a:.1f} s)")
        if len(times) >= REMOVED_TILES:
            break
    lines = [f"v{v - 1} -> v{v} removed {len(gone)} stretch(es), "
             f"{measure_mod.clock_text(total)} in all; longest first: "
             + "; ".join(labels[:20])]
    texts = _frames_source(ctx, sorted(times))
    lines += [_strip(t) for t in texts]
    return _env(lines, ctx, f"source frames inside {min(len(gone), len(labels))}"
                f" of {len(gone)} removed stretch(es)", notes)


def _show_chapters(ctx, kind, key, notes, **_):
    storyboard = (ctx.index or {}).get("visual_storyboard") or {}
    chapters = storyboard.get("chapters") or []
    sheets = storyboard.get("sheets") or []
    if not chapters:
        import visual_index
        text = visual_index.compact_text(storyboard) if storyboard else ""
        if text:
            return _env([text], ctx, "storyboard inventory (no chapters)",
                        notes)
        lo, hi = 0.0, float(getattr(ctx, "duration", 0.0) or 0.0)
        notes.append("the storyboard is not built yet: an even sheet of the "
                     "source instead")
        return _frames_at(ctx, "source", None,
                          _span_times(lo, hi, tiles=PAGE_TILES * 2), notes,
                          label="even sheet")
    lines = []
    pages = []
    for ch in chapters:
        lines.append(f"{ch.get('chapter_id')}: {float(ch.get('t0', 0)):.1f}-"
                     f"{float(ch.get('t1', 0)):.1f} s  "
                     f"{str(ch.get('description') or '')[:200]}"
                     + (f" | said: {str(ch.get('transcript_excerpt'))[:160]}"
                        if ch.get("transcript_excerpt") else ""))
        for row in sheets:
            if float(row.get("t1", 0)) >= float(ch.get("t0", 0)) and \
                    float(row.get("t0", 0)) <= float(ch.get("t1", 0)):
                page = int(row.get("page") or 0)
                if page and page not in pages:
                    pages.append(page)
                break
    if pages:
        text = agent_tools.open_visual_page(ctx, pages=pages[:24])
        lines.append(_strip(text))
    hi = float(getattr(ctx, "duration", 0.0) or 0.0)
    return _env(lines, ctx, f"{len(chapters)} chapter(s) over 0-{hi:g} s, "
                f"{min(len(pages), 24)} storyboard page(s)", notes)


def _show_transcript(ctx, kind, key, notes, rng=None, query=None, **_):
    if query:
        return _env([str(read_tools.search_transcript(ctx, query))], ctx,
                    f"transcript search for {query!r}", notes)
    if kind in ("program",):
        return _env([str(read_tools.get_kept_transcript(ctx))], ctx,
                    "what the current cut keeps (film clock)", notes)
    lo, hi = _bounds(ctx, kind, key)
    if rng:
        a, b = _range(rng, lo, hi, notes)
    else:
        a, b = 0.0, None
    text = read_tools.get_transcript(ctx, start=a, end=b,
                                     asset_key=key if kind == "asset"
                                     else None)
    coverage = (f"asset {key} transcript" if kind == "asset" else
                f"source transcript {a:g}-{(b if b is not None else hi):g} s")
    return _env([str(text)], ctx, coverage, notes)


def _show_shots(ctx, kind, key, notes, rng=None, **_):
    lo, hi = _bounds(ctx, "source", None)
    a, b = _range(rng, lo, hi, notes)
    return _env([str(read_tools.get_shots(ctx, start=a, end=b))], ctx,
                f"shots {a:g}-{b:g} s", notes)


def _show_audio(ctx, kind, key, notes, rng=None, **_):
    if kind == "asset":
        return _env([str(agent_tools.get_audio_analysis(ctx, asset_key=key))],
                    ctx, f"asset {key} audio", notes)
    lo, hi = _bounds(ctx, "source", None)
    a, b = _range(rng, lo, hi, notes)
    parts = [str(agent_tools.find_silences(ctx, start=a, end=b))]
    try:
        parts.append(str(agent_tools.get_audio_analysis(ctx)))
    except Exception as exc:  # noqa: BLE001
        parts.append(f"loudness and beats not read ({str(exc)[:120]})")
    return _env(parts, ctx, f"speech gaps {a:g}-{b:g} s; loudness and beats "
                "over the source", notes)


def _show_activity(ctx, kind, key, notes, rng=None, **_):
    lo, hi = _bounds(ctx, "source", None)
    a, b = _range(rng, lo, hi, notes)
    text = str(agent_tools.find_activity(ctx, start=a, end=b))
    parts = [_strip(text)]
    if kind == "program":
        parts.append(_idle_in_cut(ctx))
    return _env(parts, ctx, f"activity measured {a:g}-{b:g} s", notes)


def _idle_in_cut(ctx):
    import activity
    track, why = agent_tools._activity_track(ctx)
    if not track:
        return f"idle time in the cut not measured ({why})"
    seg = activity.segment(track)
    keep = ctx.latest_edl()["json"].get("keep") or []
    idle = 0.0
    for a, b, *_ in seg.get("idle") or []:
        for s, e in keep:
            idle += max(0.0, min(b, e) - max(a, s))
    return f"idle time still in the cut: {measure_mod.clock_text(idle)}"


def _show_moments(ctx, kind, key, notes, rng=None, query=None, **_):
    lo, hi = _bounds(ctx, "source", None)
    a, b = _range(rng, lo, hi, notes)
    text = agent_tools.find_visual_moments(ctx, query or "", start=a, end=b)
    return _env([_strip(text)], ctx, f"storyboard moments {a:g}-{b:g} s "
                f"for {query!r}", notes)


def _show_rank(ctx, kind, key, notes, **kw):
    notes.append("rank is read from the chapters and the activity in this "
                 "build")
    chapters = _show_chapters(ctx, kind, key, [], **kw)
    activity_env = _show_activity(ctx, "source", None, [], **kw)
    return envelope.read(
        "look", "\n".join(notes) + "\n" + chapters.body + "\n"
        + activity_env.body, see=chapters.see + activity_env.see,
        coverage="chapters and activity over the source")
