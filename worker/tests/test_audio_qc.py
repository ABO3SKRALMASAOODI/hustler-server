import math
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import audio_qc  # noqa: E402


def test_silent_loudnorm_measurement_is_finite_and_json_safe(monkeypatch):
    monkeypatch.setattr(
        audio_qc, "_run_ffmpeg",
        lambda _path: '''
        {"input_i":"-inf","input_tp":"-inf","input_lra":"0.00"}
        ''')

    result = audio_qc.measure("silent.mp4", duration_s=10)

    assert result["i"] == -100.0
    assert result["tp"] is None
    assert result["lra"] == 0.0
    assert all(not isinstance(value, float) or math.isfinite(value)
               for value in (result["i"], result["tp"], result["lra"]))
    assert "essentially SILENT" in result["findings"][0]
