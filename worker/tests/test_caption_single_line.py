"""Focused regressions for the transcript-caption single-row contract."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools  # noqa: E402
import captions  # noqa: E402
from schemas import CaptionStyle, edl_signature, validate_edl  # noqa: E402


def _words(text, step=0.34):
    return [{"w": token, "t0": i * step, "t1": i * step + 0.25}
            for i, token in enumerate(text.split())]


def _state_rows(events):
    grouped = {}
    for event in events:
        key = (event["start"], event["end"])
        grouped.setdefault(key, []).append(event)
    out = []
    for state_events in grouped.values():
        rows, seen = [], set()
        for event in state_events:
            for row_key, text in agent_tools._ass_caption_rows(event["text"]):
                if row_key not in seen:
                    seen.add(row_key)
                    rows.append(text)
        if rows:
            out.append(rows)
    return out


def test_single_line_is_optional_signature_safe_and_tool_addressable():
    assert CaptionStyle(single_line=True).single_line is True
    assert agent_tools._STYLE_PROPS["single_line"] == {"type": "boolean"}
    assert agent_tools._parse_partial_style({"single_line": False}) == {
        "single_line": False}

    raw = {"keep": [[0, 5]], "captions": {
        "mode": "from_transcript", "style": {"preset": "podcast"}}}
    absent = validate_edl(raw, 5).model_dump()
    explicit_none = validate_edl({
        "keep": [[0, 5]], "captions": {
            "mode": "from_transcript",
            "style": {"preset": "podcast", "single_line": None}}},
        5).model_dump()
    assert absent["captions"]["style"]["single_line"] is None
    assert edl_signature(absent) == edl_signature(explicit_none)


@pytest.mark.parametrize("preset", ["podcast", "clean", "stacked"])
def test_single_line_overrides_flow_and_stack_without_dropping_words(preset):
    words = _words("Every difficult choice creates a stronger future")
    events = captions.events_premium(
        words, style={"preset": preset, "single_line": True},
        max_words=2, play_res=(1080, 1920),
        design_version=captions.CAPTION_DESIGN_VERSION)

    rows = _state_rows(events)
    assert rows and all(len(state_rows) == 1 for state_rows in rows)
    assert all(r"\N" not in event["text"] for event in events)
    rendered = " ".join(line for state_rows in rows for line in state_rows)
    assert all(word in rendered for word in
               "Every difficult choice creates a stronger future".split())


def test_absent_contract_preserves_podcasts_authored_multiline_layout():
    events = captions.events_premium(
        _words("one two three four"), style={"preset": "podcast"},
        max_words=4, play_res=(1080, 1920))
    assert any(r"\N" in event["text"] for event in events)


class _DB:
    @staticmethod
    def run(*_args, **_kwargs):
        return None


class _AuditCtx:
    project_id = 99
    duration = 3.0
    db = _DB()

    def __init__(self, workdir, edl, words):
        self.workdir = str(workdir)
        self._row = {"version": 7, "json": edl}
        self.index = {"words": words,
                      "video": {"width": 1080, "height": 1920}}

    def latest_edl(self):
        return self._row


def test_audit_reports_single_row_density_metrics_on_real_ass(tmp_path):
    words = _words("one clear idea lands now")
    edl = {"keep": [[0, 3]], "captions": {
        "mode": "from_transcript", "design_version": 2,
        "max_words_per_caption": 2,
        "style": {"preset": "podcast", "single_line": True,
                  "animation": "none"}}}
    result = json.loads(agent_tools.audit_captions(
        _AuditCtx(tmp_path, edl, words)))

    assert result["status"] == "pass"
    assert result["max_words_seen"] <= 2
    assert result["max_lines_seen"] == 1
    assert result["density_violation_count"] == 0
    assert result["wrap_violation_count"] == 0
    assert all(state["line_count"] == 1 for state in result["event_page"])


def test_audit_fails_declared_density_and_wrap_contracts(monkeypatch, tmp_path):
    words = _words("one two three four")
    edl = {"keep": [[0, 3]], "captions": {
        "mode": "from_transcript", "max_words_per_caption": 2,
        "style": {"preset": "podcast", "single_line": True}}}

    def fake_build(_edl, _index, _tl, path, play_res=None):
        del play_res
        with open(path, "w", encoding="utf-8") as handle:
            # The vector panel must be ignored, and the text-effect duplicate
            # must not double the language count. The explicit newline is the
            # one genuine second rendered row.
            handle.write(
                "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,"
                r"{\p1}m 0 0 l 10 0 l 10 10" "\n"
                "Dialogue: 1,0:00:00.00,0:00:02.00,Default,,0,0,0,,"
                r"{\pos(540,1500)}one two\Nthree four" "\n"
                "Dialogue: 5,0:00:00.00,0:00:02.00,Default,,0,0,0,,"
                r"{\pos(540,1500)}one two\Nthree four" "\n")
        return path

    monkeypatch.setattr(agent_tools.caplib, "build_ass", fake_build)
    result = json.loads(agent_tools.audit_captions(
        _AuditCtx(tmp_path, edl, words)))

    assert result["status"] == "fail"
    assert result["max_words_seen"] == 4
    assert result["max_lines_seen"] == 2
    assert result["density_violation_count"] == 1
    assert result["wrap_violation_count"] == 1
    assert result["event_page"][0]["text_lines"] == ["one two", "three four"]
