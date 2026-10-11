"""Sound requests: a sound the approved library lacks goes to the owner.

Owner rule 2 (track 0C): sound effects come only from the owner-approved
library (sound_library.py), and the web-SFX chain is off and out of the live
catalog (config.WEB_SFX_ENABLED). When a user asks for a sound the library
does not have — a crowd cheer, rain, a specific door — the editor declines in
one clause and this module files a request the owner auditions by ear; an
approved recording joins the library and every later edit can use it.

Storage: one bounded JSON map in app_kv (key ``sound_requests``; the table
exists since migration 016), merged atomically by db.kv_merge_json_map. No
new table and no migration in Wave 0 (INTERFACES.md §6.1). The same request
from many edits is one row with a count, keyed by its normalized wording.
Filing is best effort and never raises into a tool.
"""

import hashlib
import json
import re
import time

import db as dbx

KV_KEY = "sound_requests"
LIMIT = 512
STATUSES = ("open", "approved", "declined")


def _normalize(description):
    words = re.sub(r"[^a-z0-9]+", " ", str(description or "").lower()).split()
    stop = {"a", "an", "the", "sound", "of", "some", "effect", "sfx",
            "please", "add"}
    return " ".join(w for w in words if w not in stop) or " ".join(words)


def _seconds(value):
    try:
        return None if value is None else round(float(value), 2)
    except (TypeError, ValueError):
        return None


def request_id(description):
    norm = _normalize(description)
    return "sr_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:10]


def _load(db):
    try:
        raw = db.run(dbx.kv_get, KV_KEY)
        rows = json.loads(raw) if raw else {}
        return rows if isinstance(rows, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def file(ctx, description, at=None, purpose=None):
    """File (or re-count) a request for a sound the library lacks; returns
    its id. ``at`` is the program second the user wanted it on, ``purpose``
    the on-screen moment it was for."""
    desc = " ".join(str(description or "").split())[:160]
    rid = request_id(desc)
    if not desc:
        return rid
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db = getattr(ctx, "db", None)
    prior = (_load(db) if db is not None else {}).get(rid) or {}
    projects = list(prior.get("projects") or [])
    pid = getattr(ctx, "project_id", None)
    if pid is not None and pid not in projects:
        projects = (projects + [pid])[-20:]
    row = {"id": rid, "description": prior.get("description") or desc,
           "normalized": _normalize(desc),
           "status": prior.get("status") or "open",
           "count": int(prior.get("count") or 0) + 1,
           "first_at": prior.get("first_at") or now, "last_at": now,
           "projects": projects,
           "user_id": (getattr(ctx, "job", None) or {}).get("user_id"),
           "at": _seconds(at),
           "purpose": " ".join(str(purpose or "").split())[:200] or None}
    try:
        db.run(dbx.kv_merge_json_map, KV_KEY, json.dumps({rid: row}), LIMIT)
    except Exception as exc:  # noqa: BLE001 — never fail the edit
        print(f"[sound_requests] not persisted: {type(exc).__name__}",
              flush=True)
    try:
        filed = getattr(ctx, "sound_requests_filed", None)
        if filed is None:
            filed = []
            ctx.sound_requests_filed = filed
        if rid not in filed:
            filed.append(rid)
    except Exception:  # noqa: BLE001
        pass
    return rid


def decline_clause(description, rid):
    """The one clause the editor tells the user."""
    desc = " ".join(str(description or "").split())[:80]
    return (f"The approved sound library has no '{desc}' yet; I filed it for "
            f"the owner to audition ({rid}) and placed no web sound.")


def list_requests(db, status="open"):
    """Owner view: requests by count, most asked first."""
    rows = [r for r in _load(db).values() if isinstance(r, dict)
            and (status is None or r.get("status") == status)]
    return sorted(rows, key=lambda r: (-int(r.get("count") or 0),
                                       r.get("first_at") or ""))


def set_status(db, rid, status, note=None):
    """Owner decision on one request (approved once the recording joined
    the library, or declined)."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    row = _load(db).get(rid)
    if not row:
        return False
    row = dict(row, status=status,
               decided_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               note=(str(note)[:300] if note else None))
    db.run(dbx.kv_merge_json_map, KV_KEY, json.dumps({rid: row}), LIMIT)
    return True
