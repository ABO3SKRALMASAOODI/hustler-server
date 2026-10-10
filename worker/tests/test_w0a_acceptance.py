"""Track 0A acceptance on real footage and the pinned models (local only).

These need the VFL fixtures (ROADMAP §4.2, $VALMERA_FIXTURES_DIR), the
pinned model files (YUNET_MODEL, MATTE_RVM_MODEL) and minutes of CPU, so
they run only when VALMERA_ACCEPTANCE=1 is set as well: other tracks' full
suites on this machine stay fast, and CI (no fixtures) skips them.

    VALMERA_ACCEPTANCE=1 YUNET_MODEL=... MATTE_RVM_MODEL=... \\
      PYTHONPATH=.. pytest -q -s tests/test_w0a_acceptance.py

AT-0A-2 (cold container, zero Chromium renders) and AT-0A-3 (a killed
shard) run in the ordinary suite: tests/test_w0a_clip_store.py and
tests/test_w0a_sidecars.py.
"""

import json
import os
import sys

import pytest

from localfix import fixture, local_only

ON = os.environ.get("VALMERA_ACCEPTANCE") == "1"
YUNET = os.environ.get("YUNET_MODEL", "")
RVM = os.environ.get("MATTE_RVM_MODEL", "")
TOOLS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools")
acceptance = pytest.mark.skipif(not ON, reason="set VALMERA_ACCEPTANCE=1")


def _tool(name):
    sys.path.insert(0, TOOLS)
    try:
        return __import__(name)
    finally:
        sys.path.pop(0)


@acceptance
@local_only(fixture("labels", "V01", "w0a_faces", "faces", "labels.json"),
            fixture("w0a", "faces_frames", "V06"), YUNET or "/no/yunet")
def test_at_0a_4_yunet_recall_beats_haar_and_is_fast():
    tool = _tool("at0a4_face_recall")
    frames = tool.load_labels()
    assert len(frames) == 200
    report = tool.evaluate(frames, YUNET)
    print(json.dumps({k: report[k] for k in
                      ("faces", "yunet", "haar_keepout", "haar_index",
                       "yunet_ms_per_frame_1thread")}, indent=1))
    y = report["yunet"]["recall_all"]
    assert y >= report["haar_keepout"]["recall_all"]
    assert y >= report["haar_index"]["recall_all"]
    assert report["yunet_ms_per_frame_1thread"]["standard1_bound_mean"] <= 10


@acceptance
@local_only(fixture("w0a", "proxies", "V01_ai_bigger.mp4"),
            fixture("w0a", "proxies", "V02_flying_cars_60s.mp4"),
            RVM or "/no/rvm")
def test_at_0a_1_local_three_shard_simulation(tmp_path):
    tool = _tool("at0a1_sidecar_p95")
    out = tmp_path / "at0a1.json"
    rc = tool.main(["--local", "--trials", "1", "--json", str(out)])
    report = json.loads(out.read_text())
    print(json.dumps(report, indent=1))
    assert report["trials"][0]["failed"] == 0
    assert report["repeat_hit_s"] < 1.0
    assert rc == 0
