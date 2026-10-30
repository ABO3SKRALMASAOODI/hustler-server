"""What the Studio's chat may show of an activity row (owner, 30 Oct 2026).

The worker and the MCP fast path store one activity row per tool call, and
its `content` is written for the AGENT and for the admin transcript:
``render_preview{"complete": true, ...} → Preview v5 rendered ... LOOK AT
THEM YOURSELF before replying ... CHECK EACH CLAIM ...``. The owner opened
"N editing steps" in his own Studio and read exactly that: raw tool names,
JSON arguments and instructions aimed at the model.

The stored row is never changed (the agent's own history and the admin
views read it as it is). Only the copy sent to the browser is rewritten,
here: a tool call becomes a plain step ("Recording the site"), a user's own
timeline edit keeps its description without the "you → EDL vN:" prefix, and
the meta keeps only what the Studio reads (tool, outcome, stamp, change
ranges, op, kind, queue notice) — never the arguments.
"""

import re

# Plain words for a tool call. Keep in step with frontend
# src/lib/turnProgress.js TOOL_WORDS (the Studio maps the tool again, so an
# older backend row never shows raw either). Unknown tools read "Editing".
TOOL_WORDS = {
    "render_preview": "Rendering your preview",
    "render_variants": "Rendering your preview",
    "keep_segments": "Cutting the video",
    "cut_range": "Cutting the video",
    "cut_output_range": "Cutting the video",
    "restore_range": "Restoring a cut",
    "cut_silences": "Trimming the pauses",
    "remove_filler_words": "Removing filler words",
    "conceal_jump_cuts": "Smoothing the cuts",
    "add_captions": "Adding captions",
    "set_caption_style": "Styling the captions",
    "set_caption_fixes": "Fixing caption words",
    "set_caption_mutes": "Fixing caption words",
    "add_motion_graphic": "Adding a title",
    "set_motion_graphic": "Adjusting a title",
    "add_text": "Adding text",
    "add_kinetic_text": "Adding text",
    "add_typeset": "Adding text",
    "set_typeset": "Adjusting text",
    "set_typography_scene": "Adding text",
    "add_text_behind": "Adding text",
    "add_title_card": "Adding a title card",
    "set_picture_card": "Adding a title card",
    "add_zoom": "Adding a camera move",
    "add_zoom_path": "Adding a camera move",
    "punch_in_on_emphasis": "Adding camera moves",
    "auto_reframe": "Reframing for vertical",
    "set_frame": "Framing the shot",
    "set_screen_frame": "Framing the screen",
    "add_device_stage": "Framing the screen",
    "source_broll": "Finding B-roll",
    "research_broll": "Finding B-roll",
    "find_footage": "Finding footage",
    "search_stock": "Finding footage",
    "add_stock_media": "Adding B-roll",
    "fetch_url": "Downloading the link",
    "download_url": "Downloading the link",
    "insert_media": "Placing the clip",
    "move_insert": "Placing the clip",
    "set_insert_window": "Placing the clip",
    "remove_insert": "Removing a clip",
    "add_overlay": "Adding an overlay",
    "move_overlay": "Adding an overlay",
    "set_overlay_motion": "Adding an overlay",
    "set_master_loudness": "Levelling the sound",
    "set_volume": "Levelling the sound",
    "set_audio_gain": "Levelling the sound",
    "record_website": "Recording the site",
    "record_website_demo": "Recording the site",
    "showcase_demo": "Adding camera moves to the demo",
    "enhance_cursor": "Polishing the cursor",
    "look_at": "Checking the picture",
    "look_at_asset": "Checking the picture",
    "watch_video": "Watching the video",
    "critique_short": "Reviewing the edit",
    "get_words": "Reading the transcript",
    "get_transcript": "Reading the transcript",
    "get_kept_transcript": "Reading the transcript",
    "search_transcript": "Reading the transcript",
    "set_color_grade": "Grading the colour",
    "set_grade_custom": "Grading the colour",
    "match_grade": "Grading the colour",
    "apply_look": "Applying a look",
    "set_fades": "Adding fades",
    "set_transitions": "Adding transitions",
    "set_junction": "Adding transitions",
    "add_sfx": "Adding sound effects",
    "move_sfx": "Adding sound effects",
    "add_music": "Adding music",
    "add_library_music": "Adding music",
    "swap_music": "Adding music",
    "set_music_fit": "Fitting the music",
    "shape_music": "Fitting the music",
    "add_voiceover": "Adding a voiceover",
    "set_speed": "Changing the speed",
    "set_speed_ramp": "Changing the speed",
    "add_freeze_frame": "Adding a freeze frame",
    "blur_region": "Blurring a region",
    "erase_region": "Cleaning up the picture",
    "set_background": "Setting the background",
    "make_shorts": "Planning your shorts",
    "export_final": "Exporting your video",
    "apply_edit_batch": "Applying the edit",
    "apply_recipe": "Applying the edit",
    "apply_format_pack": "Applying the edit",
}

# Reads and bookkeeping the model does for itself: one plain step.
PLANNING_TOOLS = frozenset({
    "load_tools", "expand_toolset", "read_skill", "get_edl", "project_state",
    "index_status", "list_assets", "get_video_info", "get_shots",
    "find_shots", "get_editorial_map", "get_audio_analysis", "get_syllables",
    "suggest_story", "suggest_devices", "suggest_emphasis", "suggest_recipes",
    "suggest_motion_beats", "suggest_match_cuts", "plan_footage",
    "plan_to_music", "plan_loop", "list_motion_templates", "list_music_library",
    "list_sound_library", "find_song", "analyze_music", "analyze_reference",
    "find_visual_moments", "find_silences", "check_speech", "audit_captions",
    "audit_motion", "audit_audio_mix", "justify_verification_findings",
})

PLANNING = "Planning the edit"
EDITING = "Editing"

# Keys of an activity row's meta the Studio reads. Everything else (the
# tool's raw arguments, the creative blueprint, receipts) stays server-side.
CLIENT_META_KEYS = ("tool", "source", "edl_version", "tool_outcome", "change",
                    "op", "kind", "step", "queue_notice", "branch_base")

# "you → EDL v2: added 11 uploads" / "mcp → EDL v4 -> v5: placed ..."
_EDL_PREFIX = re.compile(
    r"^\s*[\w.-]+\s*(?:→|->)\s*EDL\s+v\d+(?:\s*(?:→|->)\s*v\d+)?\s*:\s*", re.I)
# Anything that reads like the model's working text: JSON, arrows, receipts.
_INTERNAL = re.compile(
    r"[{}\[\]]|→|->|\bEDL\b|\bREJECTED\b|\bCORRECTION\b|LOOK AT|CHECK EACH|"
    r"storage_key|asset_key|\bv\d+\b", re.I)


def tool_words(tool):
    name = str(tool or "")
    if name in PLANNING_TOOLS:
        return PLANNING
    return TOOL_WORDS.get(name, EDITING)


def _user_op_words(content):
    """A user's own timeline edit: its description, without the prefix and
    without the before/after receipt."""
    text = _EDL_PREFIX.sub("", str(content or ""), count=1)
    text = re.split(r"\.\s+(?:Before|After)\s*:", text, maxsplit=1)[0]
    text = re.sub(r"\s*\[[^\]]*\]", "", text).strip().rstrip(".")
    if not text or _INTERNAL.search(text):
        return None
    return text[:1].upper() + text[1:]


def activity_label(content, meta):
    """The one line the Studio may show for this activity row."""
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("queue_notice") or meta.get("kind") == "first_cut_step":
        return str(content or "")
    if meta.get("op"):
        return _user_op_words(content) or EDITING
    if meta.get("tool"):
        return tool_words(meta.get("tool"))
    text = str(content or "").strip()
    if text and not _INTERNAL.search(text) and len(text) <= 160:
        return text
    return EDITING


def client_message(row):
    """The browser's copy of one chat_messages row."""
    out = {"id": row["id"], "role": row["role"], "content": row["content"],
           "meta": row["meta"], "created_at": row["created_at"].isoformat()}
    if row["role"] != "activity":
        return out
    meta = row["meta"] if isinstance(row["meta"], dict) else {}
    out["content"] = activity_label(row["content"], meta)
    out["meta"] = {k: meta[k] for k in CLIENT_META_KEYS if k in meta}
    return out
