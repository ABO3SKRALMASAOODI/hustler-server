"""Department ``verify``: track 4F's motion critic (INTERFACES_W4.md §4, §6).

  critique_short  READ: judge the newest render of the current edit (or a
                  named render asset) against the references' craft —
                  rhythm, type ladder, restraint, B&W as a device, brand
                  integrity, flash safety, faces, motion artefacts and
                  creative range — and return ranked, concrete fixes, each
                  with its exact apply_edit_batch operation when one exists
                  (motion_critic.critique)

Read-only and browserless: the critic decodes the render with ffmpeg and
measures it with numpy on the agent, MCP and Shorts lanes. It never writes;
the agent writes the fixes it agrees with in one apply_edit_batch.
"""

import json
import os
import uuid

DEPARTMENT = "verify"
VERBS = ("critique_short",)
_MAX_SIBLINGS = 8


def _render_row(ctx, variant, edl_version):
    import db as dbx
    if variant in ("final", "preview"):
        return ctx.db.run(dbx.find_render_asset, ctx.project_id, variant,
                          edl_version)
    row = ctx.db.run(dbx.find_render_asset, ctx.project_id, "final",
                     edl_version)
    return row or ctx.db.run(dbx.find_render_asset, ctx.project_id,
                             "preview", edl_version)


def _asset_row(ctx, key):
    import db as dbx
    return ctx.db.run(dbx.asset_by_key, ctx.project_id, key)


def _siblings(conn, project_id, limit=_MAX_SIBLINGS):
    """Latest EDLs of the other shorts cut from the same parent (the run's
    earlier shorts): the critic's house-style reference. [] when this is
    not a short."""
    with conn.cursor() as cur:
        cur.execute("SELECT parent_project_id FROM projects WHERE id = %s",
                    (project_id,))
        row = cur.fetchone()
        parent = (row or {}).get("parent_project_id") if row else None
        if not parent:
            return []
        cur.execute("""SELECT DISTINCT ON (e.project_id) e.json
                       FROM edls e JOIN projects p ON p.id = e.project_id
                       WHERE p.parent_project_id = %s AND p.id <> %s
                       ORDER BY e.project_id, e.version DESC
                       LIMIT %s""", (parent, project_id, int(limit)))
        return [r["json"] for r in cur.fetchall() if r.get("json")]


def _cut_level(ctx, edl):
    """The source's sound at the keep's edges (cut_audio.source_levels), so
    the critic judges a join by the rule the keep tools and the render's
    MID-WORD AUDIT use (audit.edge_verdicts); None when it cannot be read
    (the critic then falls back to the render's own sound)."""
    hook = getattr(ctx, "cut_sound_source", None)
    times = [float(x) for span in (edl or {}).get("keep") or []
             for x in span[:2]]
    if not callable(hook) or len(times) < 4:
        return None
    try:
        import cut_audio
        src = hook()
        if not src:
            return None
        return cut_audio.source_levels(src, times,
                                       getattr(ctx, "duration", None),
                                       timeout=20.0)
    except Exception:  # noqa: BLE001 — the critic still judges without it
        return None


def critique_short(ctx, variant="auto", asset_key=None, deep=True,
                   board=True, format=None):
    import motion_critic
    import storage
    fmt = str(format or "").strip().upper() or None
    if fmt and (len(fmt) != 1 or fmt not in "ABCDEFGHIJ"):
        return ("REJECTED: format is the pack the short follows, one of "
                "A-J (as apply_format_pack names them), or leave it out for "
                "a "
                "talking head (C) / a trend desk on the user's track (B).")
    row = ctx.latest_edl()
    edl, version = row["json"], row["version"]
    if asset_key:
        asset = _asset_row(ctx, str(asset_key))
        if not asset or asset.get("kind") != "render":
            return ("REJECTED: asset_key must name a render of this project "
                    "(a preview or a final).")
    else:
        asset = _render_row(ctx, str(variant or "auto").lower(), version)
        if not asset:
            return (f"REJECTED: EDL v{version} has no completed render to "
                    "judge. Call render_preview (or export), then "
                    "critique_short. A past version is not judged as the "
                    "current edit.")
    meta = asset.get("meta") or {}
    workdir = os.path.join(ctx.workdir, f"critic_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    local = os.path.join(workdir, "render.mp4")
    try:
        storage.download_to(asset["storage_key"], local)
    except Exception as e:  # noqa: BLE001
        return f"FAILED: could not read the render ({str(e)[:160]})."
    try:
        previous = ctx.db.run(_siblings, ctx.project_id)
    except Exception:  # noqa: BLE001 — no siblings: no house-style lens
        previous = []
    maps = None
    try:
        from tools_ext import verify_tools
        maps = verify_tools.music_maps(ctx, edl)
        intensity = verify_tools.project_intensity(ctx)
    except Exception:  # noqa: BLE001
        intensity = "designed"
    final = (meta.get("variant") == "final")
    cut_level = _cut_level(ctx, edl)
    res = motion_critic.critique(
        local, edl, getattr(ctx, "index", None) or {},
        request_text=getattr(ctx, "user_message", None), report=meta,
        previous=previous, intensity=intensity, final=final, fmt=fmt,
        want_wm=bool(final and (meta.get("picture_qc") or {}).get(
            "watermark") is not None), maps=maps, deep=bool(deep),
        board_dir=workdir if board else None, cut_level=cut_level)
    ctx._last_critique = res
    text = motion_critic.critique_text(res)
    if res.get("board") and isinstance(getattr(ctx, "pending_images", None),
                                       list):
        # the board rides back with the result, like look_at's sheets
        ctx.pending_images.append((
            f"CRITIC BOARD — score {res.get('score')}/10; ticks: red "
            "blocking, amber fix, blue advisory", res["board"]))
    ops = [f.get("op") for f in res.get("findings") or [] if f.get("op")]
    for f in res.get("findings") or []:
        ops += f.get("ops") or []
    if ops:
        text += ("\nOPS (apply the ones you agree with in one "
                 "apply_edit_batch): " + json.dumps(ops[:12],
                                                    separators=(",", ":")))
    return (f"Render judged: {meta.get('variant', '?')} of EDL "
            f"v{meta.get('edl_version', version)}.\n" + text)


TOOL_SPECS = {
    "critique_short": (
        critique_short,
        "READ: judge the newest render of the current edit (final, else "
        "preview; or asset_key) like a demanding editor against the "
        "reference reels: rhythm and dead stretches, the type ladder, "
        "restraint (ledger, graphics share, owner rules), B&W as a device "
        "never a look, brand integrity (end card, corner mark), flash "
        "safety on the rendered frames, type on faces, late words, matte "
        "flicker, stuttering slow motion, and range (a house style re-used "
        "across shorts, strong moments with nothing on them). Returns a "
        "score, ranked findings with concrete fixes and the exact "
        "apply_edit_batch ops; never writes.",
        {"variant": {"type": "string", "enum": ["auto", "final", "preview"]},
         "asset_key": {"type": "string"},
         "deep": {"type": "boolean", "description": "faces, late words and "
                  "flicker passes (default true)"},
         "board": {"type": "boolean"},
         "format": {"type": "string", "description": "the pack the short "
                    "follows (A-J, as apply_format_pack): its rhythm band"}}),
}
REQUIRED_ARGS = {}
WRITE_TOOLS = set()
COMPACT = {
    "critique_short": "READ: judge the newest render vs the reference "
                      "craft (rhythm, type ladder, restraint, B&W as a "
                      "device, brand, flashes, faces, late words, range); "
                      "ranked fixes with exact batch ops.",
}
