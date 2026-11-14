"""Approve measured compositions only after their actual inputs are ready."""
import hashlib
import json

# sidecars.wait caps this at 90 s, what a final gives its matte.
MATTE_WAIT_FACTOR = 1.5


def needed(ctx, row):
    if not getattr(ctx, "request_contract_required", False):
        return False
    edl = row["json"]
    if not edl.get("fx") and not edl.get("motion"):
        return False
    from tools_ext.world_tools import _world_ctx
    import world_layer
    return bool(world_layer.requests(_world_ctx(ctx, edl)))


def _wait_for_matte(ctx, row, wait_s):
    """A matte effect drawn before its matte exists is left out, so the
    preview shows the edit without it and the turn takes the effect for a
    failure (p5533: the matte was 8 s old when the preview ran, and the
    editor removed the one tool that could separate the dancers from the
    wall). The wait is bounded and never refuses: a matte that does not arrive
    renders as the device's substitute, reported like any degraded device."""
    try:
        edl = row["json"]
        import matte_track
        windows = [(float(i["start"]), float(i["end"]))
                   for i in edl.get("fx") or []
                   if isinstance(i, dict) and i.get("start") is not None
                   and i.get("type") in matte_track.MATTE_FX_TYPES]
        if not windows or not edl.get("keep"):
            return
        from timeline import Timeline
        from tools_ext.subject_tools import _ensure_matte
        tl = Timeline(edl["keep"], edl.get("inserts") or [],
                      edl.get("speed") or [])
        _ensure_matte(ctx, edl, tl, windows, wait_s=wait_s)
        # The same EDL can first render a substitute, then its measured subject.
        # Bind preview caching to the dependency state, not just the EDL version.
        state = matte_track.matte_status(edl, getattr(ctx, "index", None) or {})
        return {"status": state.get("status"), "windows": state.get("windows"),
                "reason": state.get("reason")}
    except Exception as e:  # noqa: BLE001 — a wait never fails a preview
        print(f"[render_dependencies] matte wait skipped: {e}", flush=True)


def before_preview(ctx, row, wait_s=60):
    matte_state = _wait_for_matte(ctx, row, wait_s * MATTE_WAIT_FACTOR)
    ctx.render_dependency_key = None
    if any(i.get('type') == 'background' for i in row['json'].get('fx') or []):
        # A background swap cannot be approved as its missing-matte substitute.
        # The refined matte may need several bounded waits; keep the same job.
        if not matte_state:
            return 'ERROR: the background replacement has no verified subject matte. The replacement is not ready.'
        if matte_state.get('status') != 'ready':
            _bind_preview(ctx, row, {'matte': matte_state})
            states = {w.get('status') for w in matte_state.get('windows') or []}
            if states & {'pending', 'running'}:
                return ('IN_PROGRESS: the subject matte for the background replacement is still '
                        'being measured. No preview was encoded. Continue with render_preview '
                        'to wait for these same inputs; do not remove the effect or claim it is finished.')
            return ('ERROR: the subject matte for the background replacement is unavailable. '
                    'The replacement is not ready: ' + str(matte_state.get('reason') or 'no measured matte'))
    if not needed(ctx, row):
        ctx.render_dependency_state = None
        if matte_state:
            _bind_preview(ctx, row, {"matte": matte_state})
        return None
    from tools_ext.world_tools import _world_ctx, _launch_source
    import world_layer
    import sidecars
    world = _world_ctx(ctx, row["json"])
    source = _launch_source(ctx)
    if source is None:
        ctx.render_dependency_state = {"edl_version": row["version"], "ready": False,
                                       "statuses": {"source": "unavailable"}}
        return "ERROR: the requested visual measurement has no usable indexed source yet."
    world.source = world.source or source
    requests = world_layer.requests(world)
    statuses = world_layer.warm(world, launch=source, wait_s=max(0, min(60, wait_s)))
    for kind, _ranges, _params in requests:
        statuses.setdefault(kind, "absent")
    ready = all(status == "ready" for status in statuses.values())
    ctx.render_dependency_state = {"edl_version": row["version"], "ready": ready, "statuses": statuses}
    if not ready:
        if any(status not in {"ready", "pending", "running"} for status in statuses.values()):
            return "ERROR: required visual measurements are unavailable: " + json.dumps(statuses) + ". The requested effect is not ready."
        return ("IN_PROGRESS: required visual measurements are still being prepared: " + json.dumps(statuses)
                + ". No preview was encoded. Continue with render_preview; it waits for these same inputs. "
                  "Do not recreate the object or claim the effect is finished.")
    versions = {kind: {key: (sidecars.spec(kind) or {}).get(key)
                       for key in ("model_id", "model_version")}
                for kind, _ranges, _params in requests}
    material = {"source": source.sha256, "requests": requests, "models": versions, "matte": matte_state}
    _bind_preview(ctx, row, material)
    return None


def _bind_preview(ctx, row, material):
    ctx.render_dependency_key = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
    # A previously rendered substitute is not the same picture as the now
    # measured composition, even when the EDL version did not change.
    preview = getattr(ctx, "last_preview", None) or {}
    if preview.get("edl_version") == row["version"] and preview.get("render_dependency_key") != ctx.render_dependency_key:
        getattr(ctx, "rendered_versions", set()).discard(row["version"])
    return None
