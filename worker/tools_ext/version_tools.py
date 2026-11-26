"""Versions: put an earlier film back exactly.

#1697 (p5968, 20 Nov 2026): "the latest one is too dark, go back to the one
before" was answered with three new grades and "I could not match the
earlier version's exact look". Every version is in history; this lands one.
"""

import json

import db as dbx

DEPARTMENT = "direction"


def _request_versions(ctx):
    """[(job id, version before, version after)] of the earlier requests on
    this project that changed the film, oldest first (the current request
    left out). Never raises: [] when the receipts can't be read."""
    try:
        _rows, jobs = ctx.db.run(dbx.edl_lineage, ctx.project_id, 2)
    except Exception:  # noqa: BLE001
        return []
    me = (getattr(ctx, "job", None) or {}).get("id")
    out = []
    for j in jobs or []:
        get = j.get if isinstance(j, dict) else (lambda k, _j=j: {
            "id": _j[0], "before_version": _j[2], "after_version": _j[3]}[k])
        try:
            jid, lo, hi = get("id"), int(get("before_version")), int(get("after_version"))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if jid == me or hi <= lo:
            continue
        out.append((jid, lo, hi))
    return out


def restore_version(ctx, version=None, requests_back=1):
    """Put an earlier version of the film back exactly, as a new version.

    #1697 (p5968, 20 Nov): "the latest one is too dark, go back to the one
    before" was answered with three new grades and "I could not match the
    earlier version's exact look". The earlier version is in history; this
    lands it. ``version`` names one; otherwise ``requests_back`` counts the
    customer's requests: 1 = the film as it was before their last request
    (what "go back", "undo that", "the previous one" mean)."""
    current = ctx.latest_edl()
    target_v = None
    if version not in (None, "", 0):
        try:
            target_v = int(str(version).strip().lstrip("vV"))
        except (TypeError, ValueError):
            target_v = None
    how = ""
    if target_v is None:
        try:
            back = max(1, int(requests_back or 1))
        except (TypeError, ValueError):
            back = 1
        start = getattr(ctx, "turn_start_edl", None) or {}
        reqs = [r for r in _request_versions(ctx)
                if not start.get("version") or r[2] <= int(start["version"])]
        if reqs:
            pick = reqs[-min(back, len(reqs))]
            target_v = pick[1]
            how = (f" (the film before request {len(reqs) - min(back, len(reqs)) + 1}"
                   f" of {len(reqs)}, which made v{pick[1]} -> v{pick[2]})")
        else:
            try:
                hist = ctx.db.run(dbx.edl_history, ctx.project_id, back + 2)
            except Exception:  # noqa: BLE001
                hist = []
            older = [int(h["version"] if isinstance(h, dict) else h[0])
                     for h in hist or []]
            older = [v for v in older if v < int(current["version"])]
            if older:
                target_v = older[min(back, len(older)) - 1]
                how = " (the version before the current one)"
    if target_v is None:
        return ("NO CHANGE: this project has no earlier version to go back "
                f"to; the film is v{current['version']}.")
    if int(target_v) == int(current["version"]):
        return (f"NO CHANGE: v{target_v} is already the current film.")
    try:
        row = ctx.db.run(dbx.get_edl_version, ctx.project_id, int(target_v))
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        try:
            hist = ctx.db.run(dbx.edl_history, ctx.project_id, 12)
        except Exception:  # noqa: BLE001
            hist = []
        have = ", ".join(f"v{h['version'] if isinstance(h, dict) else h[0]}"
                         for h in hist or [])
        return (f"NO CHANGE: v{target_v} is not in this project's history "
                f"(recent versions: {have or 'none'}); call restore_version "
                "with one of them.")
    restored = json.loads(json.dumps(row["json"] if isinstance(row, dict)
                                     else row[3]))
    res = ctx.write_edl(restored, f"restored v{target_v} exactly{how}")
    if res.startswith("NO CHANGE"):
        return (f"NO CHANGE: the current film already equals v{target_v}.")
    if not res.startswith("EDL v"):
        return res
    return (res + f"\nThe film is v{target_v} again, every layer as it was"
            f"{how}. Later versions stay in history (restore_version brings "
            "any of them back). Render it and tell the customer which "
            "version they are looking at.")



TOOL_SPECS = {
    "restore_version": (restore_version, "Put an earlier version of the film "
                        "back EXACTLY, every layer as it was, as a new version. "
                        "Use it when the customer asks to go back, undo, or "
                        "return to the previous/earlier one: never rebuild a "
                        "look you think matches. requests_back=1 (default) is "
                        "the film before their last request; version=N "
                        "restores vN.", {
                            "version": {"type": "integer"},
                            "requests_back": {"type": "integer"}}),
}
WRITE_TOOLS = {"restore_version"}
COMPACT = {"restore_version": "Put an earlier version back exactly (go back, "
           "undo, the previous one): requests_back=1 is the film before the "
           "last request; version=N restores vN."}
