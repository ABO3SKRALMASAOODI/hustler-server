"""Track 1F: proof_device on real renders (motion_qa.proof).

A child process with ``VALMERA_FX_PROBE=1`` (tests/w1f_proof_probe.py)
proves the engine-only ``fx_probe`` devices — a frame-exact negate at two
stages and a dissolve junction across a cut — through the changed-section
preview path (renderer._render_changed_sections), rendered with and without
them, on synthetic 25 / 29.97 / 24 / 59.94 fps sources. No footage: CI-safe.
"""

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import motion_qa   # noqa: E402


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    wd = str(tmp_path_factory.mktemp("w1f_proof"))
    env = dict(os.environ, VALMERA_FX_PROBE="1", SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub")
    env.pop("VALMERA_FX_PREVIEW", None)
    r = subprocess.run([sys.executable,
                        os.path.join(HERE, "w1f_proof_probe.py"), wd],
                       cwd=WORKER, env=env, capture_output=True, text=True,
                       timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for k, v in out.items():
        assert "error" not in v, (k, v)
    return out


@pytest.mark.parametrize("rate", ["25", "29.97", "24", "59.94"])
def test_proof_measures_each_landing_frame_exactly(report, rate):
    r = report[rate]
    assert r["pass"] is True, r
    for did in ("n1", "n2"):
        d = r["devices"][did]
        assert d["basis"] == "without_device"
        assert d["event_f"] == d["planned_f"] == d["landing_f"], (rate, d)
        assert d["metrics"]["land_err_f"]["value"] == 0.0
        assert d["metrics"]["flash_risk"]["pass"] is True
        assert d["strip"] and d["onion"]
    j = r["devices"]["j1"]
    assert abs(j["event_f"] - j["planned_f"]) <= 1, j
    integrity = j["metrics"]["duration_exact"]
    assert integrity["value"] == 0.0 and integrity["pass"] is True
    assert "collisions" not in j["metrics"]


def test_proof_renders_only_the_windows_around_the_devices(report):
    r = report["25"]
    covered = sum(b - a for a, b in r["window"])
    assert covered < 2.5          # of a 3.3 s program, three short windows
    assert r["reel_fps"] == pytest.approx(25.0)
    hi = report["59.94"]
    assert hi["reel_fps"] == pytest.approx(59.94, abs=0.01)   # full rate


def test_without_a_reference_the_strip_reads_its_own_change(report):
    t = report["temporal"]
    assert t["basis"] == "temporal"
    assert t["event_f"] == t["planned_f"] and t["land"] == 0.0
    assert any("strip's own" in n for n in t["notes"])


def test_proof_windows_and_landing_helpers():
    w = motion_qa.proof_windows([30, 33, 200], 30.0, 10.0)
    assert w[0][0] == pytest.approx(25 / 30.0, abs=0.01)
    assert len(w) == 2 and w[1][0] > w[0][1]
    item = {"id": "a", "type": "x", "stage": "pre_captions", "start": 2.0,
            "end": 2.04}
    assert motion_qa.landing(item, 25.0) == (50, 50)
    j = {"id": "j", "type": "x", "stage": "junction", "at": 4.0}
    assert motion_qa.landing(j, 25.0)[0] == 100


def test_device_items_and_ablation():
    edl = {"fx": [{"id": "a", "stage": "world", "start": 1.0, "end": 1.1},
                  {"id": "m", "stage": "music_bus", "music_id": "x"}],
           "motion": [{"id": "g", "start": 2.0, "end": 3.0,
                       "composite": {"blend": "difference"}},
                      {"id": "h", "start": 4.0, "end": 5.0,
                       "layer": "behind_subject"},
                      {"id": "p", "start": 6.0, "end": 7.0}]}
    devs = motion_qa.device_items(edl)
    assert [d["id"] for d in devs] == ["a", "g", "h"]
    assert [d["id"] for d in motion_qa.device_items(edl, window=(3.5, 6))] \
        == ["h"]
    prev = {"fx": [{"id": "a", "stage": "world", "start": 1.0,
                    "end": 1.1}], "motion": edl["motion"]}
    assert motion_qa.device_items(edl, previous_edl=prev) == []
    out = motion_qa.ablate(edl, devs)
    assert [i["id"] for i in out["fx"]] == ["m"]
    assert [m["id"] for m in out["motion"]] == ["g", "p"]
    assert out["motion"][0]["composite"] is None
    # the reference keeps the program's block clock (a 'source' frame)
    assert out["frame"] == {"ratio": "source", "mode": "crop"}
    framed = dict(edl, frame={"ratio": "9:16", "mode": "crop"})
    assert motion_qa.ablate(framed, devs)["frame"]["ratio"] == "9:16"


def test_summary_lines_flag_failures():
    res = {"edl_version": 4, "render": {"window": [[1, 2]], "wall_s": 3.1,
                                         "variant": "proof480"},
           "pass": False, "notes": ["x"], "devices": [
               {"id": "a", "type": "accent", "device": "accent_negative",
                "landing_f": 30, "planned_f": 28, "basis": "without_device",
                "rung": "full", "metrics": {
                    "land_err_f": {"value": 2.0, "bar": "<=1",
                                   "pass": False},
                    "flash_risk": {"value": 1.0, "bar": "<=3",
                                   "pass": True}}}]}
    text = motion_qa.summary_lines(res)
    assert "NEEDS ATTENTION" in text and "land_err_f +2f" in text
    assert "FAIL" in text and "Note: x" in text
