"""Pure parsing helpers at the Studio chat/job HTTP boundary."""


def parse_client_event(data):
    data = data or {}
    kind = str(data.get("kind") or "")[:40]
    try:
        asset_id = int(data.get("asset_id"))
    except (TypeError, ValueError):
        asset_id = None
    return kind, asset_id, data.get("detail")
