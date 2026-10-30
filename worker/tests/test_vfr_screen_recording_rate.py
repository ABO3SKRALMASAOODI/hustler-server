"""A frames-on-change screen recording plays at its nominal rate, never at its
2-15 fps average (R2 journeys, 30 Oct 2026: a production approval preview of
a 52.6 s macOS-style screen recording came back at 2.848 fps, 150 frames).
Ported from the trial branch (071bfeed), where the fix had stayed."""
import json

import media


def _probe_with(monkeypatch, r, avg):
    out = {"format": {"duration": "52.6"},
           "streams": [{"codec_type": "video", "width": 1280, "height": 800,
                        "r_frame_rate": r, "avg_frame_rate": avg,
                        "duration": "52.6"},
                       {"codec_type": "audio"}]}
    monkeypatch.setattr(media, "run", lambda *a, **k: json.dumps(out))
    return media.probe("rec.mp4")


def test_frames_on_change_recording_normalizes_to_its_nominal_rate(monkeypatch):
    p = _probe_with(monkeypatch, "25/1", "3725/1308")       # 2.85 fps average (the journey file)
    assert p["vfr"] is True and p["fps"] == 25.0
    p = _probe_with(monkeypatch, "120/1", "900/100")        # odd nominal rate
    assert p["vfr"] is True and p["fps"] == 30.0


def test_ordinary_vfr_and_cfr_footage_keep_their_average(monkeypatch):
    p = _probe_with(monkeypatch, "60/1", "5800/100")        # phone VFR at ~58 fps
    assert p["vfr"] is True and p["fps"] == 58.0
    p = _probe_with(monkeypatch, "30000/1001", "30000/1001")
    assert p["vfr"] is False and round(p["fps"], 2) == 29.97
