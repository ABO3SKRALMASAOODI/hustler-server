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
    assert got["types"] == sorted(["subject", "background", "region",
                                   "accent", "strobe", "junction",
                                   "grade_window", "finish"])
    assert got["slots"] == ["MotionItem.composite"]
    assert got["ok"] >= 31 and got["refused"] == got["bad"]
    assert got["events"] >= 1
