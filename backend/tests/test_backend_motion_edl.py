"""An EDL holding a motion graphic validates, batches and passes the export
preflight IN THE BACKEND PROCESS — not only on the worker.

worker/schemas.py checks motion graphics against worker/motion_templates and
worker/edit_batch checks their asset params, both by plain module name. The
backend loads those worker files by path with only backend/ on sys.path, so
from Oct 9 to Oct 11 2026 every apply_edit_batch and export_final of such an
EDL failed with ModuleNotFoundError — "internal error" and "This timeline has
no renderable footage yet" — while the export tests stubbed the preflight out.

The check runs in a clean subprocess (backend/ alone on sys.path, as on
Render), so another test that happens to put worker/ on sys.path cannot hide
a regression.

    cd backend && python -m pytest tests -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SNIPPET = r'''
import json, os, sys
sys.path = [p for p in sys.path if not p.rstrip("/").endswith("worker")]
sys.path.insert(0, os.getcwd())
from routes import video
assert "motion_templates" in sys.modules, "backend did not register motion_templates"
edl = video.wschemas.default_edl(60.0)
edl["motion"] = [{"id": "m1", "template": "word_slam", "start": 1.0, "end": 3.0,
                  "params": {"text": "HELLO"}}]
ok = video.wschemas.validate_edl(edl, 60.0).model_dump()
assert ok["motion"][0]["template"] == "word_slam"
assert video._export_edl_error(edl, 60.0) is None, video._export_edl_error(edl, 60.0)
base = video.wschemas.default_edl(60.0)
out = video.wbatch.apply_batch(base, [{"action": "upsert", "layer": "motion", "id": "m2",
                                       "value": {"id": "m2", "template": "word_slam",
                                                 "start": 2.0, "end": 4.0,
                                                 "params": {"text": "WORLD"}}}], 60.0, {})
print(json.dumps({"motion": [m["id"] for m in out.get("motion") or []]}))
'''


def test_motion_edl_validates_batches_and_exports_in_the_backend_process():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(BACKEND), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == {"motion": ["m2"]}


# ── Wave 1 (worker/devices/INTERFACES_W1.md §5): every Wave 1 device type
# validates, batches and is removed IN THE BACKEND PROCESS, the path an MCP
# client's apply_edit_batch takes. Run with every Wave 1 member registered
# (VALMERA_FX_PREVIEW=1), so this holds before and after each track flips
# its module live. A new device type extends the worker fixture
# (tests/fixtures/w1_stale_client_ops.json), not this file.
W1_SNIPPET = r'''
import json, os, re, sys
sys.path = [p for p in sys.path if not p.rstrip("/").endswith("worker")]
sys.path.insert(0, os.getcwd())
from routes import video
s, b = video.wschemas, video.wbatch
fx = json.load(open(os.path.join(os.getcwd(), "..", "worker", "tests",
                                 "fixtures", "w1_stale_client_ops.json")))
dur, allowed = fx["duration"], set(fx["allowed_keys"])
base = s.validate_edl(fx["base"], dur).model_dump()
sig = s.edl_signature(base)
done, ops, undo = [], [], []
for case in fx["ok"]:
    out = b.apply_batch(base, case["ops"], dur, allowed)
    back = b.apply_batch(out, case["undo"], dur, allowed)
    assert s.edl_signature(back) == sig, case["name"]
    if case["ops"][0]["layer"] == "fx":
        ops += case["ops"]; undo += case["undo"]
    done.append(case["name"])
allfx = b.apply_batch(base, ops, dur, allowed)
events = len(video.wflash.flash_events(allfx))
assert s.edl_signature(b.apply_batch(allfx, undo, dur, allowed)) == sig
refused = 0
for case in fx["bad"]:
    try:
        b.apply_batch(base, case["ops"], dur, allowed)
    except Exception as e:
        assert re.search(case["match"], str(e)), (case["name"], str(e))
        refused += 1
print(json.dumps({"types": sorted(s.FX_TYPES), "slots": sorted(s._FX_SLOT_TYPES),
                  "ok": len(done), "refused": refused, "bad": len(fx["bad"]),
                  "events": events}))
'''


def test_w1_devices_validate_batch_and_remove_in_the_backend_process():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND), VALMERA_FX_PREVIEW="1")
    r = subprocess.run([sys.executable, "-c", W1_SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    # later waves declare their own fx tags (worker/tests/fixtures/waves)
    later = set()
    wd = BACKEND.parent / "worker" / "tests" / "fixtures" / "waves"
    for f in sorted(wd.glob("w*.json")) if wd.is_dir() else []:
        later |= set(json.loads(f.read_text()).get("fx_tags") or [])
    assert sorted(set(got["types"]) - later) == sorted([
        "subject", "background", "region", "accent", "strobe", "junction",
        "grade_window", "finish"])
    assert got["slots"] == ["MotionItem.composite"]
    assert got["ok"] >= 31 and got["refused"] == got["bad"]
    assert got["events"] >= 1


W4_SNIPPET = r'''
import json, os, sys
sys.path = [p for p in sys.path if not p.rstrip("/").endswith("worker")]
sys.path.insert(0, os.getcwd())
from routes import video
s, b = video.wschemas, video.wbatch
dur = 30.0
base = s.validate_edl({"keep": [[0.0, 30.0]], "frame": {"ratio": "4:3"}},
                      dur).model_dump()
sig = s.edl_signature(base)
graph = {"v": 1, "W": 1080, "H": 1920, "dur": 4.0, "scenes": [0.0],
         "objects": [{"id": "screen", "kind": "media",
                      "design": [70, 600, 940, 528],
                      "segs": [[0.0, 4.0, 70, 600, 940, 528, 70, 600, 940,
                                528, 1, 1, "linear"]],
                      "iris": None, "enter": 0.0}], "irises": [], "u": 3.0}
credit = {"provider": "nasa", "license": "Public domain (NASA media "
          "guidelines)", "author": "NASA", "page_url": "https://images.nasa.gov/"}
ops = [
    {"action": "upsert", "layer": "fx", "id": "dp1", "value": {
        "id": "dp1", "type": "depth", "stage": "world", "start": 2.0,
        "end": 4.0, "kind": "dof", "device": "depth_dof"}},
    {"action": "upsert", "layer": "fx", "id": "tm1", "value": {
        "id": "tm1", "type": "time", "stage": "world", "start": 5.0,
        "end": 6.0, "kind": "echo", "device": "time_echo"}},
    {"action": "upsert", "layer": "fx", "id": "br1", "value": {
        "id": "br1", "type": "broll", "stage": "screen", "start": 8.0,
        "end": 10.0, "source": {"kind": "asset", "asset_key": "media/1/b.mp4"},
        "credit": credit, "device": "broll_cutaway"}},
    {"action": "upsert", "layer": "motion", "id": "ui1", "value": {
        "id": "ui1", "template": "ui_scene", "start": 12.0, "end": 16.0,
        "params": {"graph": graph, "shown": [], "backdrop": "solid"}}},
    {"action": "upsert", "layer": "fx", "id": "um1", "value": {
        "id": "um1", "type": "ui_media", "stage": "pre_captions",
        "start": 12.0, "end": 16.0, "motion_id": "ui1", "slot": "screen",
        "source": {"kind": "asset", "asset_key": "media/1/b.mp4"},
        "device": "ui_media_slot"}},
]
allowed = {"media/1/b.mp4"}
out = b.apply_batch(base, ops, dur, allowed)
undo = [{"action": "remove", "layer": "fx", "id": i}
        for i in ("um1", "dp1", "tm1", "br1")] + [
        {"action": "remove", "layer": "motion", "id": "ui1"}]
back = b.apply_batch(out, undo, dur, allowed)
assert s.edl_signature(back) == sig
refused = 0
for bad in (dict(ops[2]["value"], credit=dict(credit, license="CC BY-NC 4.0")),
            dict(ops[4]["value"], slot="nope")):
    try:
        b.apply_batch(out if bad.get("slot") else base,
                      [{"action": "upsert", "layer": "fx", "id": bad["id"],
                        "value": bad}], dur, allowed)
    except Exception:
        refused += 1
print(json.dumps({"types": sorted(s.FX_TYPES), "fx": sorted(
    i["id"] for i in out["fx"]), "refused": refused,
    "ratio": out["frame"]["ratio"]}))
'''


def test_w4_types_validate_batch_and_remove_in_the_backend_process():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-c", W4_SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert {"depth", "time", "broll", "ui_media"} <= set(got["types"])
    assert got["fx"] == ["br1", "dp1", "tm1", "um1"]
    assert got["refused"] == 2 and got["ratio"] == "4:3"
