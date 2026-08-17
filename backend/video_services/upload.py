"""Pure upload/tray response normalization.

Media transfer remains direct-to-object-storage; these helpers deliberately
accept only database metadata and never open, probe, or decode media bytes.
"""


def clean_dimension(value, low, high):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if low <= value <= high else None


def serialize_asset(asset):
    return {
        "id": asset["id"], "kind": asset["kind"],
        "storage_key": asset["storage_key"], "bytes": asset["bytes"],
        "duration_s": asset["duration_s"], "width": asset["width"],
        "height": asset["height"], "fps": asset["fps"],
        "sha256": asset["sha256"], "meta": asset.get("meta") or {},
        "created_at": (asset["created_at"].isoformat()
                       if asset.get("created_at") else None),
    }
