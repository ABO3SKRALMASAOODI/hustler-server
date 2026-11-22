"""MCP entry point for documentary narration."""
from mcp_film import build

DEPARTMENT = "picture"
TOOL_SPECS = {
    "build_film": (build, "Build and export a documentary from the complete uploaded narration. "
                   "Reads the transcript, finds checked archive pictures, places the pictures and "
                   "narration on the timeline and renders the final MP4. Follow wait_for_job on "
                   "the returned job id. Completed chapters are reused.",
                   {"narration_asset_key": {"type": "string", "description": "The narration upload's storage key."},
                    "goal": {"type": "string", "description": "The user's documentary brief."}})
}
REQUIRED_ARGS = {"build_film": []}
WRITE_TOOLS = {"build_film"}
