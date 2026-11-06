"""Directory metadata must not weaken or mutate the live call contract."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_directory_metadata import directory_tool_metadata


@pytest.mark.parametrize("name,argument,types", [
    ("retypeset_language", "translation", {"array", "object", "string", "null"}),
    ("set_background", "source", {"string", "number", "object", "null"}),
    ("add_ui_scene", "data", {"string", "array", "object", "null"}),
])
def test_missing_argument_types_preserve_supported_representations(name, argument, types):
    tool = {
        "name": name, "title": "Readable tool name", "description": "Internal workflow prompt.",
        "inputSchema": {
            "type": "object", "required": ["project_id"],
            "properties": {"project_id": {"type": "integer"}, argument: {"description": "Accepted data"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
    }
    original = deepcopy(tool)
    public = directory_tool_metadata(tool)
    assert tool == original  # Cached worker schemas belong to other clients too.
    assert set(public["inputSchema"]["properties"][argument]["type"]) == types
    assert public["inputSchema"]["required"] == ["project_id"]
    assert public["inputSchema"]["additionalProperties"] is False
    assert public["annotations"] == {**original["annotations"], "title": "Readable tool name"}
    assert public["name"] == name
    assert public["description"] != original["description"]
    json.dumps(public)  # The complete spec stays transport serializable.


def test_worker_supplied_type_and_constraints_win_over_legacy_repair():
    tool = {"name": "add_ui_scene", "inputSchema": {"properties": {
        "data": {"type": "object", "additionalProperties": {"type": "number"}}
    }}}
    assert directory_tool_metadata(tool)["inputSchema"] == tool["inputSchema"]


def test_new_tool_is_not_hidden_renamed_or_replaced_with_generic_description():
    tool = {"name": "future_operation", "description": "Returns a future measurement.",
            "inputSchema": {"type": "object", "required": ["sample"], "properties": {
                "sample": {"type": "number", "minimum": 0}
            }}, "annotations": {"title": "Future measurement", "readOnlyHint": True}}
    assert directory_tool_metadata(tool) == tool


def test_gateway_project_scope_remains_optional_and_matches_nested_call():
    tool = {"name": "expand_toolset", "inputSchema": {
        "required": [], "properties": {"project_id": {"type": "integer"}}
    }, "annotations": {"readOnlyHint": False}}
    public = directory_tool_metadata(tool)
    assert public["inputSchema"]["required"] == []
    assert "call.arguments.project_id" in public["inputSchema"]["properties"]["project_id"]["description"]
    assert public["annotations"]["readOnlyHint"] is False


def test_nested_batch_description_does_not_mutate_or_remove_validation():
    value = {"description": "Read get_edl for exact shapes.", "anyOf": [{"type": "object"}, {"type": "array"}]}
    tool = {"name": "apply_edit_batch", "inputSchema": {"properties": {"operations": {
        "type": "array", "minItems": 1, "items": {"type": "object", "properties": {"value": value}}
    }}}}
    public = directory_tool_metadata(tool)
    operations = public["inputSchema"]["properties"]["operations"]
    assert operations["minItems"] == 1
    assert operations["items"]["properties"]["value"]["anyOf"] == value["anyOf"]
    assert "get_edl" not in operations["items"]["properties"]["value"]["description"]
    assert value["description"] == "Read get_edl for exact shapes."
