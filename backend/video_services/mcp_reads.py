"""Read-only MCP operations that need no executor or media process."""
import importlib.util
import json
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "worker" / "agent_prompt.py"
_spec = importlib.util.spec_from_file_location("worker_skill_text", _path)
_skills = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_skills)


def read_metadata(cur, project_id, name, args, schemas):
    """None deliberately falls back to the full tool's existing semantics."""
    if name == "read_skill" and isinstance(args.get("name"), str):
        if set(args) - {"name", "section"}:
            return None
        section = args.get("section")
        if section is not None and not isinstance(section, str):
            return None
        return _skills.read_skill_text(args["name"], section=section)
    if (name != "get_edl" or set(args) - {"sections", "compact", "offset", "limit"}
            or args.get("sections") not in (None, [], "") or args.get("compact")):
        return None
    try:
        int(args.get("offset") or 0), int(args.get("limit") or 100)
    except (ValueError, TypeError):
        return None
    cur.execute("""SELECT version, json FROM edls WHERE project_id = %s
                   ORDER BY version DESC LIMIT 1""", (project_id,))
    row = cur.fetchone()
    if not row or len(json.dumps(row["json"], indent=1)) > 19000:
        return None
    cur.execute("""SELECT duration_s FROM assets WHERE project_id = %s
                   AND kind = 'original' ORDER BY id DESC LIMIT 1""", (project_id,))
    original = cur.fetchone() or {}
    return json.dumps({"version": row["version"], "edl": row["json"],
                       "description": schemas.describe_edl(
                           row["json"], original.get("duration_s") or 0)}, indent=1)
