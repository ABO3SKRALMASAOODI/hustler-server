"""Public MCP metadata, separate from the internal editor's workflow prompts.

These are authored API descriptions, not runtime text filters. New capabilities
retain their original descriptions until an editor supplies a public entry;
tools are never hidden or renamed to satisfy a directory review.
"""

from copy import deepcopy
import json
from pathlib import Path


DESCRIPTIONS = json.loads(
    Path(__file__).with_name("mcp_directory_descriptions.json").read_text()
)

# These optional arguments already accept these JSON representations in the
# executor. The worker catalog omitted their types; no execution contract changes.
_MISSING_TYPES = {
    ("retypeset_language", "translation"): ["array", "object", "string", "null"],
    ("set_background", "source"): ["string", "number", "object", "null"],
    ("add_ui_scene", "data"): ["string", "array", "object", "null"],
}

_PARAM_DESCRIPTIONS = {
    ("open_short", "card"): "One-based card number within the explicit parent project's shorts board.",
    ("upload_start", "duration_s"): "Optional locally measured media duration in seconds, retained in the upload-completion contract.",
    ("watch_video", "asset_key"): "Exact project asset storage key; applicable only when kind is 'asset'.",
    ("apply_look", "music"): "Exact identifier of the library track explicitly selected by the user. Omission leaves music unchanged.",
    ("add_motion_graphic", "params"): "Parameters supported by the selected motion-graphics template.",
    ("add_motion_graphic", "template"): "Available motion-graphics template name, or 'html' for a supplied composition.",
    ("add_library_music", "user_pick"): "The account-bound music-selection token together with the user's own selection words.",
    ("critique_short", "format"): "The format pack followed by the short (A–J), including its rhythm band.",
    ("suggest_devices", "intensity"): "Defaults to the project's current intensity setting.",
}


def directory_tool_metadata(tool):
    """Return a detached public spec without changing dispatch or permissions."""
    spec = deepcopy(tool)
    name = spec.get("name", "")
    if name in DESCRIPTIONS:
        spec["description"] = DESCRIPTIONS[name]
    annotations = spec.setdefault("annotations", {})
    annotations.setdefault("title", spec.get("title") or name.replace("_", " ").capitalize())
    props = spec.get("inputSchema", {}).get("properties", {})
    if "project_id" in props:
        props["project_id"]["description"] = (
            "Explicit project identifier for this request. "
            "The connection's navigation pointer does not select the target."
        )
        if name == "expand_toolset":
            props["project_id"]["description"] = (
                "Optional project identifier. When dispatching a nested call, "
                "it must equal call.arguments.project_id."
            )
    for (tool_name, parameter), types in _MISSING_TYPES.items():
        if name == tool_name and parameter in props:
            props[parameter].setdefault("type", list(types))
    for (tool_name, parameter), description in _PARAM_DESCRIPTIONS.items():
        if name == tool_name and parameter in props:
            props[parameter]["description"] = description
    if name == "apply_edit_batch":
        value = props.get("operations", {}).get("items", {}).get("properties", {}).get("value")
        if value is not None:
            value["description"] = (
                "For set, the complete layer value; for upsert, an item object "
                "merged by ID; for reorder, all insert IDs in order. Values "
                "follow the current edit decision list's field shapes."
            )
    return spec
