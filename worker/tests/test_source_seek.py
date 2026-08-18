"""Long-source renders seek near their first retained frame.

The filtergraph keeps absolute source clocks; ``-copyts`` is what lets input
seeking avoid hours of dead decode without rebasing the EDL or its evidence.
"""

import media
import renderer
from schemas import default_edl


def test_long_source_render_seeks_but_keeps_absolute_trim_times(
        monkeypatch, tmp_path):
    edl = default_edl(5000)
    edl["keep"] = [[4505.89, 4510.0]]
    captured = {}

    monkeypatch.setattr(media, "probe", lambda _path: {
        "duration": 5000.0, "video_duration": 5000.0,
        "width": 1920, "height": 1080, "fps": 30.0,
        "has_audio": True, "sar": 1.0,
    })
    monkeypatch.setattr(media, "run",
                        lambda cmd, **_kw: captured.setdefault("cmd", cmd))
    monkeypatch.setattr(media, "duration_of", lambda _path: 4.11)

    renderer.render_edl(
        edl, {"video": {"duration": 5000}, "words": []},
        str(tmp_path / "source.mp4"), str(tmp_path / "out.mp4"),
        str(tmp_path), preview=True, suppress_outro=True)

    cmd = captured["cmd"]
    assert "-copyts" in cmd
    seek_at = cmd.index("-ss")
    assert float(cmd[seek_at + 1]) == 4504.89
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=start=4505.890:end=4510.000" in graph


def test_near_source_start_keeps_legacy_input_shape(monkeypatch, tmp_path):
    edl = default_edl(10)
    captured = {}
    monkeypatch.setattr(media, "probe", lambda _path: {
        "duration": 10.0, "video_duration": 10.0,
        "width": 640, "height": 360, "fps": 30.0,
        "has_audio": True, "sar": 1.0,
    })
    monkeypatch.setattr(media, "run",
                        lambda cmd, **_kw: captured.setdefault("cmd", cmd))
    monkeypatch.setattr(media, "duration_of", lambda _path: 10.0)

    renderer.render_edl(
        edl, {"video": {"duration": 10}, "words": []},
        str(tmp_path / "source.mp4"), str(tmp_path / "out.mp4"),
        str(tmp_path), preview=True, suppress_outro=True)

    assert "-copyts" not in captured["cmd"]
