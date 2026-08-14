"""compose_panels: equal columns on black, not PIP overlays."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402


def test_normalize_accepts_keys_or_column_lists():
    cols, err = agent_tools._normalize_panel_columns(
        ["clips/1/a.mp4", "clips/1/b.mp4", "clips/1/c.mp4"])
    assert err is None and len(cols) == 3
    assert cols[0][0]["asset_key"] == "clips/1/a.mp4"
    cols, err = agent_tools._normalize_panel_columns([
        [{"asset_key": "a", "start": 0, "duration": 2}],
        [{"asset_key": "b"}, {"asset_key": "c", "duration": 1.5}],
    ])
    assert err is None and len(cols) == 2 and len(cols[1]) == 2
    _, err = agent_tools._normalize_panel_columns(["only-one"])
    assert err.startswith("REJECTED")


def test_filter_builds_three_equal_black_padded_columns():
    fg = agent_tools._compose_panels_filter(
        3, [1, 1, 1], [6.0, 3.0, 6.0], 6.0, 1920, 1080)
    assert "hstack=inputs=3" in fg
    assert "pad=640:1080" in fg
    assert "tpad=stop_mode=add:stop_duration=3.000" in fg
    assert "[vout]" in fg


def test_compose_panels_writes_a_clip_not_an_edl(monkeypatch):
    class _DB:
        def __init__(self):
            self.assets = {
                "clips/1/a.mp4": {"id": 1, "kind": "video_clip",
                                  "storage_key": "clips/1/a.mp4",
                                  "duration_s": 4.0,
                                  "meta": {"filename": "a.mp4"}},
                "clips/1/b.mp4": {"id": 2, "kind": "video_clip",
                                  "storage_key": "clips/1/b.mp4",
                                  "duration_s": 3.0,
                                  "meta": {"filename": "b.mp4"}},
            }
            self.inserted = []

        def run(self, fn, *a, **k):
            name = getattr(fn, "__name__", "")
            if name == "asset_by_key":
                return self.assets.get(a[1])
            if name == "insert_asset":
                self.inserted.append(a)
                return 9
            if name == "set_progress":
                return True
            return None

    class _Ctx:
        project_id = 1
        job = {}
        workdir = tempfile.mkdtemp(prefix="panels_")
        db = _DB()
        videos_generated = []
        _asset_locals = {}

        def latest_edl(self):
            return {"version": 1, "json": {
                "frame": {"ratio": "16:9", "mode": "pad"}}}

    ran = {}

    def fake_run(cmd, **kw):
        ran["cmd"] = cmd
        open(cmd[-1], "wb").write(b"mp4")
        return ""

    monkeypatch.setattr(agent_tools, "_asset_local_path",
                        lambda ctx, asset: "/tmp/" + asset["storage_key"])
    monkeypatch.setattr(agent_tools.media, "run", fake_run)
    monkeypatch.setattr(agent_tools.media, "probe",
                        lambda p: {"duration": 3.0})
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda *a, **k: None)
    monkeypatch.setattr(os.path, "getsize", lambda p: 12)

    ctx = _Ctx()
    res = agent_tools.compose_panels(
        ctx, ["clips/1/a.mp4", "clips/1/b.mp4"], duration_s=6)
    assert res.startswith("Composed a"), res
    assert "NOT in the program" in res
    assert "insert_media" in res
    assert ctx.videos_generated
    assert ctx.db.inserted
    assert "-an" in ran["cmd"]
    assert "hstack=inputs=2" in ran["cmd"][ran["cmd"].index("-filter_complex") + 1]
    assert "compose_panels" in agent_tools.WRITE_TOOLS
    assert "compose_panels" in agent_tools.REQUIRED_ARGS


def test_compose_panels_is_not_a_recipe_tool():
    assert "compose_panels" not in agent_tools.RECIPE_TOOLS
