"""Atomic, media-free edits shared by the Studio API and the agent.

All authored times in a batch describe the resulting timeline. This explicit
contract prevents an implicit remap from moving captions/music twice.
"""
import copy
import json

try:
    from schemas import validate_edl
except ImportError:
    from worker_schemas import validate_edl

LAYERS = frozenset(("keep", "speed", "inserts", "frame", "captions",
    "caption_mutes", "texts", "vectors", "music", "sfx", "voiceover", "volume", "master",
    "effects", "overlays", "canvas"))
ITEM_LAYERS = frozenset(("inserts", "texts", "vectors", "music", "sfx", "voiceover", "overlays"))


def project_media_keys(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT storage_key,duration_s FROM assets WHERE project_id=%s", (project_id,))
        return {row["storage_key"]: row for row in cur.fetchall()}


def media_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("asset_key", "storage_key", "vocals_key", "accomp_key") and item:
                if not isinstance(item, str):
                    raise ValueError("A media reference must be a storage key string.")
                yield item
            else:
                yield from media_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from media_keys(item)


def apply_batch(before, operations, duration, allowed_keys):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
        raise ValueError("Supply between 1 and 64 edit operations.")
    if len(json.dumps(operations, allow_nan=False)) > 256_000:
        raise ValueError("This edit batch is too large; split it into smaller batches.")
    edl = copy.deepcopy(before)
    for op in operations:
        if not isinstance(op, dict) or set(op) - {"action", "layer", "value", "id"}:
            raise ValueError("An operation accepts action, layer, value and id only.")
        action, layer = op.get("action"), op.get("layer")
        if not isinstance(layer, str) or layer not in LAYERS:
            raise ValueError(f"Unsupported batch layer: {layer}.")
        if action == "set":
            if "value" not in op:
                raise ValueError("A set operation requires value.")
            edl[layer] = copy.deepcopy(op["value"])
            if layer == "keep":
                edl.pop("split_keep_boundaries", None)
        elif action in ("upsert", "remove") and layer in ITEM_LAYERS:
            item_id = op.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError("An item operation requires a stable id.")
            items = edl.get(layer) or []
            if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
                raise ValueError(f"{layer} must be a list of objects.")
            edl[layer] = items
            at = next((i for i, item in enumerate(items) if item.get("id") == item_id), None)
            if action == "remove":
                if at is None:
                    raise ValueError(f"{layer} item {item_id} does not exist.")
                items.pop(at)
            else:
                value = op.get("value")
                if not isinstance(value, dict) or value.get("id", item_id) != item_id:
                    raise ValueError("An upsert requires an object with the same id.")
                value = {**(items[at] if at is not None else {}), **value, "id": item_id}
                if at is None:
                    items.append(value)
                else:
                    items[at] = value
        elif action == "reorder" and layer == "inserts":
            ids = op.get("value")
            items = edl.get("inserts") or []
            if not isinstance(items, list) or any(not isinstance(i, dict) or not isinstance(i.get("id"), str) for i in items):
                raise ValueError("Inserts must be a list of named objects.")
            if (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
                    or len(set(ids)) != len(ids) or set(ids) != {i["id"] for i in items}):
                raise ValueError("Reorder must name every insert id exactly once.")
            if edl.get("keep"):
                raise ValueError("Reorder is for a canvas sequence; set insert positions for mixed footage.")
            lookup = {i["id"]: i for i in items}
            edl["inserts"] = [lookup[i] for i in ids]
            cursor = 0.
            for item in edl["inserts"]:
                item["at_output_s"] = cursor
                cursor += item["duration_s"]
        else:
            raise ValueError(f"Unsupported operation {action} on {layer}.")
    for layer in ITEM_LAYERS:
        items = edl.get(layer) or []
        if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
            raise ValueError(f"{layer} must be a list of objects.")
        ids = [i.get("id") for i in items if i.get("id")]
        if not all(isinstance(i, str) for i in ids):
            raise ValueError(f"{layer} ids must be strings.")
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate ids in {layer}.")
    if set(media_keys(edl)) - set(allowed_keys):
        raise ValueError("Use media attached to this project. A referenced asset is not available here.")
    normalized = validate_edl(edl, duration).model_dump()
    if isinstance(allowed_keys, dict):
        for item in normalized.get("inserts") or []:
            asset_duration = (allowed_keys.get(item["asset_key"]) or {}).get("duration_s")
            if item["kind"] == "video" and asset_duration:
                end = (item.get("source_start_s") or 0) + item["duration_s"] * (item.get("rate") or 1)
                if end > float(asset_duration) + .05:
                    raise ValueError(f"Clip {item['id']} extends beyond its source video.")
    # Pydantic's historical EDL schema tolerates extras. Authoring a new
    # batch must not silently discard a misspelled field and claim success.
    def check_fields(raw, clean, path):
        if isinstance(raw, dict) and isinstance(clean, dict):
            unknown = set(raw) - set(clean)
            if unknown:
                raise ValueError(f"Unknown field in {path}: {', '.join(sorted(unknown))}.")
            for key, value in raw.items():
                check_fields(value, clean[key], f"{path}.{key}")
        elif isinstance(raw, list) and isinstance(clean, list):
            for index, (value, checked) in enumerate(zip(raw, clean)):
                check_fields(value, checked, f"{path}[{index}]")
    for layer in {op["layer"] for op in operations}:
        check_fields(edl[layer], normalized[layer], layer)
    return normalized
