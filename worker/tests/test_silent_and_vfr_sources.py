"""Bake-off 25 Oct: sources that carry no sound, and frames-on-change
screen recordings.

* A website capture or a source with no audio track renders a silent mix by
  design. The SILENT audio finding made the editor ask for music nobody
  requested (owner policy: never pick a track) and report a finished demo or
  highlight as partial/blocked.
* A VFR screen recording averaging 2.8 fps was normalized to 2.8 fps CFR:
  proxy, preview and export became a slideshow and section renders came back
  a frame long ("changed-section piece 0 rendered 1.76s, expected 1.45s").
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import audio_qc  # noqa: E402
import media  # noqa: E402
import renderer  # noqa: E402

SILENT = '{"input_i":"-inf","input_tp":"-inf","input_lra":"0.00"}'


def test_silent_by_design_keeps_the_measurement_without_the_finding(monkeypatch):
    monkeypatch.setattr(audio_qc, "_run_ffmpeg", lambda _p: SILENT)
    by_design = audio_qc.measure("x.mp4", duration_s=12, silent_by_design=True)
    assert by_design["i"] == -100.0
    assert not any("SILENT" in f for f in by_design["findings"])
    default = audio_qc.measure("x.mp4", duration_s=12)
    assert "essentially SILENT" in default["findings"][0]


def _meta(table):
    return lambda key: table.get(key, {})


def test_website_capture_canvas_is_silent_by_design():
    edl = {"canvas": {"width": 1920, "height": 1080},
           "inserts": [{"kind": "video", "asset_key": "fetched/1/a.mp4"}]}
    assert renderer.silent_by_design(
        edl, {}, _meta({"fetched/1/a.mp4": {"recorded": True}}))


def test_source_without_audio_is_silent_by_design():
    edl = {"keep": [[0, 10]]}
    index = {"video": {"has_audio": False}}
    assert renderer.silent_by_design(edl, index, _meta({}))


def test_anything_that_carries_sound_keeps_the_finding():
    index_silent = {"video": {"has_audio": False}}
    # authored sound
    for key in ("music", "sfx", "voiceover"):
        assert not renderer.silent_by_design({key: [{"x": 1}]}, index_silent, _meta({}))
    # a main source with audio, or one whose audio is unknown
    assert not renderer.silent_by_design({}, {"video": {"has_audio": True}}, _meta({}))
    assert not renderer.silent_by_design({}, {}, _meta({}))
    # an uploaded clip (not a capture) inserted over a silent source
    edl = {"inserts": [{"kind": "video", "asset_key": "clips/1/b.mp4"}]}
    assert not renderer.silent_by_design(edl, index_silent, _meta({}))
    # ... unless it is muted
    edl = {"inserts": [{"kind": "video", "asset_key": "clips/1/b.mp4", "mute": True}]}
    assert renderer.silent_by_design(edl, index_silent, _meta({}))

    def boom(_key):
        raise RuntimeError("db down")
    edl = {"canvas": {}, "inserts": [{"kind": "video", "asset_key": "fetched/1/a.mp4"}]}
    assert not renderer.silent_by_design(edl, {}, boom)


def _probe_with(monkeypatch, r, avg):
    out = {"format": {"duration": "52.6"},
           "streams": [{"codec_type": "video", "width": 1280, "height": 800,
                        "r_frame_rate": r, "avg_frame_rate": avg,
                        "duration": "52.6"},
                       {"codec_type": "audio"}]}
    monkeypatch.setattr(media, "run", lambda *a, **k: json.dumps(out))
    return media.probe("rec.mp4")


def test_frames_on_change_recording_normalizes_to_its_nominal_rate(monkeypatch):
    p = _probe_with(monkeypatch, "25/1", "3725/1308")       # 2.85 fps average
    assert p["vfr"] is True and p["fps"] == 25.0
    p = _probe_with(monkeypatch, "120/1", "900/100")        # odd nominal rate
    assert p["vfr"] is True and p["fps"] == 30.0


def test_ordinary_vfr_and_cfr_footage_keep_their_average(monkeypatch):
    p = _probe_with(monkeypatch, "60/1", "5800/100")        # phone VFR at ~58 fps
    assert p["vfr"] is True and p["fps"] == 58.0
    p = _probe_with(monkeypatch, "30000/1001", "30000/1001")
    assert p["vfr"] is False and round(p["fps"], 2) == 29.97


def test_new_subscriber_claude_lane_gets_the_first_requests_note(monkeypatch):
    from types import SimpleNamespace
    import agent_loop
    monkeypatch.setattr(agent_loop, "filmstrip_parts", lambda *_a, **_k: [])
    monkeypatch.setattr(agent_loop, "state_block", lambda *_a, **_k: "STATE")
    monkeypatch.setattr(agent_loop.llm, "agent_sees", lambda _model: False)

    class Db:
        @staticmethod
        def run(fn, *_args):
            if fn is agent_loop.dbx.recent_chat:
                return []
            raise AssertionError(fn)

    message = {"id": 1, "content": "record a demo of https://example.com", "meta": {}}

    def system_text(ctx):
        msgs = agent_loop._build_messages(ctx, Db(), message, include_visual_overview=False)
        return "\n".join(m["content"] for m in msgs
                         if m["role"] == "system" and isinstance(m["content"], str))

    paid = SimpleNamespace(direct_sight=False, agent_model="claude-haiku-5-5",
                           session_id=5, claude_paid={"lane": {"model": "claude-haiku-5-5"}})
    assert agent_loop.CLAUDE_PAID_FIRST_REQUESTS in system_text(paid)
    grandfathered = SimpleNamespace(direct_sight=False, agent_model="gpt-6-luna", session_id=5)
    assert "FIRST REQUESTS." not in system_text(grandfathered)
