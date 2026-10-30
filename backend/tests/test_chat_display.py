"""The Studio never receives the agent's tool text (owner, 30 Oct 2026).

The owner's own Studio showed `render_preview{"complete": true, ...} →
Preview v5 rendered ... LOOK AT THEM YOURSELF ... CHECK EACH CLAIM` under
"N editing steps". These rows are project 5246's, as stored.
"""
from datetime import datetime, timezone
import json
import os
import re

from video_services import chat_display as cd

AT = datetime(2026, 10, 30, 20, 30, tzinfo=timezone.utc)

RENDER = ('render_preview{"complete": true, "quality": "approval", "report": '
          'true} → Preview v5 rendered: 20.0s (source 0.0s). It is attached to '
          'the chat and the player updates to it automatically. The frames '
          'follow this message — LOOK AT THEM YOURSELF before replying: the '
          'numbered tiles ... CHECK EACH CLAIM against its numbered tile')
RECORD = ('record_website{"url": "https://valmera.io/", "duration_s": 20} → '
          'Recorded "Valmera" — 21.1s at 1080x1920: storage_key=fetched/5246/'
          'e05a5e8e7402.mp4. The capture is SILENT.')

LEAKS = re.compile(r"[{}]|→|LOOK AT|CHECK EACH|storage_key|REJECTED|"
                   r"render_preview|record_website|EDL v", re.I)


def row(content, meta, role="activity", i=1):
    return {"id": i, "role": role, "content": content, "meta": meta,
            "created_at": AT}


def test_tool_rows_become_plain_steps_without_arguments():
    out = cd.client_message(row(RENDER, {
        "tool": "render_preview", "source": "agent", "edl_version": 5,
        "args": {"report": True, "quality": "approval", "complete": True},
        "tool_outcome": {"status": "success"}}))
    assert out["content"] == "Rendering your preview"
    assert "args" not in out["meta"]
    assert out["meta"] == {"tool": "render_preview", "source": "agent",
                           "edl_version": 5,
                           "tool_outcome": {"status": "success"}}
    assert out["created_at"] == AT.isoformat()
    rec = cd.client_message(row(RECORD, {"tool": "record_website",
                                         "args": {"url": "https://valmera.io/"}}))
    assert rec["content"] == "Recording the site"


def test_planning_rejected_and_unknown_tools_never_show_raw():
    for tool, want in (("load_tools", "Planning the edit"),
                       ("read_skill", "Planning the edit"),
                       ("get_edl", "Planning the edit"),
                       ("look_at_asset", "Checking the picture"),
                       ("insert_media", "Placing the clip"),
                       ("some_new_tool", "Editing")):
        content = (f'{tool}{{"x": 1}} → REJECTED: native_resolution requires '
                   'rendered=true; use look_at_asset for original media.')
        out = cd.client_message(row(content, {
            "tool": tool, "tool_outcome": {"status": "correction_needed"}}))
        assert out["content"] == want
        assert not LEAKS.search(out["content"])
        assert out["meta"]["tool_outcome"] == {"status": "correction_needed"}


def test_change_ranges_and_stamps_survive_for_the_edit_flash():
    change = {"global": True, "manifest": {"output_ranges": [[0.0, 20.0]]}}
    out = cd.client_message(row("insert_media{} → EDL v1 -> v2: placed", {
        "tool": "insert_media", "change": change, "edl_version": 1,
        "creative_blueprint": {"x": 1}}))
    assert out["meta"]["change"] == change
    assert out["meta"]["edl_version"] == 1
    assert "creative_blueprint" not in out["meta"]


def test_user_timeline_edits_keep_their_own_words():
    out = cd.client_message(row("you → EDL v2: added 11 uploads to the timeline",
                                {"tool": "tray_submit", "op": "tray_submit"}))
    assert out["content"] == "Added 11 uploads to the timeline"
    out = cd.client_message(row(
        "you → EDL v3: removed insert ins1", {"op": "remove_insert",
                                              "tool": "remove_insert"}))
    assert out["content"] == "Removed insert ins1"
    out = cd.client_message(row(
        "you → EDL v4 -> v5: trimmed [ins2] to 0.0-2.5s. Before: canvas "
        "1080x1920. After: canvas 1080x1920", {"op": "trim_keep_segment"}))
    assert out["content"] == "Trimmed to 0.0-2.5s"
    # a receipt that is all internal falls back to a plain step
    out = cd.client_message(row("you → EDL v9: {\"a\": 1}", {"op": "x"}))
    assert out["content"] == "Editing"


def test_mcp_receipts_without_source_read_plainly():
    out = cd.client_message(row("mcp → EDL v100: applied edit batch",
                                {"tool": "apply_edit_batch"}))
    assert out["content"] == "Applying the edit"


def test_notices_progress_steps_and_conversation_rows_are_untouched():
    notice = row("Your request is next in line.", {"queue_notice": True})
    assert cd.client_message(notice)["content"] == "Your request is next in line."
    step = row("Added captions", {"kind": "first_cut_step", "step": "captions"})
    out = cd.client_message(step)
    assert out["content"] == "Added captions"
    assert out["meta"] == {"kind": "first_cut_step", "step": "captions"}
    for role in ("user", "assistant"):
        meta = {"preview": {"sha256": "x"}, "outcome": "fulfilled"}
        msg = row("Your 20-second vertical showcase is in place.", meta, role)
        assert cd.client_message(msg) == {
            "id": 1, "role": role, "content": msg["content"], "meta": meta,
            "created_at": AT.isoformat()}


def test_an_activity_row_without_a_tool_shows_only_safe_text():
    assert cd.client_message(row("Muted the original sound", None))[
        "content"] == "Muted the original sound"
    assert cd.client_message(row('x{"a": 1} → y', {}))["content"] == "Editing"
    assert cd.client_message(row(None, None))["content"] == "Editing"


def test_both_user_facing_serializers_use_it():
    src = open(os.path.join(os.path.dirname(__file__), "..", "routes",
                            "video.py"), encoding="utf-8").read()
    assert src.count("chat_display.client_message(r)") == 2
    assert '"content": r["content"],\n             "meta": r["meta"]' not in src


def test_the_studio_words_match_the_frontend_map_where_both_name_a_tool():
    # Kept in step by hand; the frontend file is in another repository, so
    # this pins the backend side of the shared names only.
    assert cd.tool_words("render_preview") == "Rendering your preview"
    assert cd.tool_words("record_website_demo") == "Recording the site"
    assert json.dumps(cd.TOOL_WORDS)  # plain strings only
