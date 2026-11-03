"""Approve measured compositions only after their actual inputs are ready."""
import hashlib
import json


def needed(ctx, row):
    if not getattr(ctx, "request_contract_required", False):
        return False
    edl = row["json"]
    if not edl.get("fx") and not edl.get("motion"):
        return False
    from tools_ext.world_tools import _world_ctx
    import world_layer
    return bool(world_layer.requests(_world_ctx(ctx, edl)))


def before_preview(ctx, row, wait_s=60):
    ctx.render_dependency_key = None
    if not needed(ctx, row):
        ctx.render_dependency_state = None
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
    material = {"source": source.sha256, "requests": requests, "models": versions}
    ctx.render_dependency_key = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
    # A previously rendered substitute is not the same picture as the now
    # measured composition, even when the EDL version did not change.
    preview = getattr(ctx, "last_preview", None) or {}
    if preview.get("edl_version") == row["version"] and preview.get("render_dependency_key") != ctx.render_dependency_key:
        ctx.rendered_versions.discard(row["version"])
    return None
