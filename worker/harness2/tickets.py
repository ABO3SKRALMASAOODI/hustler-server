"""A verb's refusal reached through v2 goes to the fixers, not the model.

Each refusal becomes an activity row the backend's refusal intake already
reads (ops_loop/refusals.py: role 'activity', meta.source 'agent', a
non-success meta.tool_outcome.status), stamped with ``harness: v2`` and the
verb's class from EDIT-CORE map part 2 (technical, overrule, owner_policy,
regex_intent), so the fixer who converts it (step H32) knows which kind of
site it is. The model only ever sees ``not_applied`` with the rest landed.
"""
import functools
import json
import os

import db as dbx

_TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                      "refusal_classes.json")


@functools.lru_cache(maxsize=1)
def _classes():
    try:
        with open(_TABLE, encoding="utf-8") as fh:
            return json.load(fh).get("functions") or {}
    except (OSError, ValueError):
        return {}


def class_of(tool):
    row = _classes().get(str(tool or ""))
    return (row or {}).get("class") or "technical"


def file_refusals(ctx, refusals):
    session_id = getattr(ctx, "session_id", None)
    if not session_id:
        return 0
    filed = 0
    for row in refusals or []:
        tool = str(row.get("tool") or "?")
        text = str(row.get("text") or "")[:600]
        meta = {"tool": tool, "source": "agent", "harness": "v2",
                "refusal_class": class_of(tool), "v2_op": row.get("op"),
                "tool_outcome": {"status": "correction_needed"}}
        try:
            ctx.db.run(dbx.add_message, session_id, "activity",
                       f"{tool} (inside edit): {text}", meta)
            filed += 1
        except Exception as exc:  # noqa: BLE001 - telemetry never fails a turn
            print(f"[harness2] refusal row not written: {exc}", flush=True)
    return filed
