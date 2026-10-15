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
    # Wave 2 types (``arrange``) register beside the pinned Wave 1 set
    assert sorted(set(got["types"]) - W2_TYPES) == sorted([
        "subject", "background", "region", "accent", "strobe", "junction",
        "grade_window", "finish"])
    assert got["slots"] == ["MotionItem.composite"]
    assert got["ok"] >= 31 and got["refused"] == got["bad"]
    assert got["events"] >= 1


def test_w1_devices_are_live_in_the_backend_process_as_production_runs():
    """The same, WITHOUT the preview switch (production never sets
    VALMERA_FX_PREVIEW): the tracks' LIVE flags alone register every Wave 1
    type in the backend, and the pure rules the backend loads by path for
    the MCP batch (mono_policy, hooks, flash_safety) import with backend/
    alone on sys.path (review w1r)."""
    snippet = W1_SNIPPET + r'''
assert video.wmono.whole_mono({"keep": [[0, 20]], "effects": {"grade": "bw"}})
assert video.wmono.whole_mono({"keep": [[0, 20]], "effects": {
    "grade_custom": {"saturation": 0.0}}})
edl, notes = video.whooks.on_keep_change({"keep": [[0.0, 10.0]]},
                                         {"keep": [[0.0, 8.0]]})
print(json.dumps({"mono": True, "hooks": True}))
'''
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VALMERA_FX_PREVIEW")}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-c", snippet], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    lines = r.stdout.strip().splitlines()
    got = json.loads(lines[-2])
    # later waves' types (W2_TYPES ...) register beside the pinned Wave 1 set
    assert sorted(set(got["types"]) - W2_TYPES) == sorted([
        "subject", "background", "region", "accent", "strobe", "junction",
        "grade_window", "finish"])
    assert got["ok"] >= 31 and got["refused"] == got["bad"]
    assert json.loads(lines[-1]) == {"mono": True, "hooks": True}


# ── Wave 2 (worker/devices/INTERFACES_W2.md §5, §10): the music-bus
# ``arrange`` type, the typeset lockup and the moving still validate, batch
# and are removed IN THE BACKEND PROCESS; an arrangement of a track nobody
# chose is refused there too (agents never choose music).
W2_TYPES = {"arrange"}
W2_SNIPPET = r'''
import json, os, sys
sys.path = [p for p in sys.path if not p.rstrip("/").endswith("worker")]
sys.path.insert(0, os.getcwd())
from routes import video
s, b = video.wschemas, video.wbatch
base = s.default_edl(60.0)
base["music"] = [{"id": "m1", "storage_key": "music/user.mp3", "start": 0.0,
                  "end": 20.0, "provenance": "user_upload"},
                 {"id": "m2", "storage_key": "music/lib.mp3", "start": 0.0,
                  "end": 20.0, "provenance": "legacy"}]
base = s.validate_edl(base, 60.0).model_dump()
sig = s.edl_signature(base)
allowed = {"music/user.mp3", "music/lib.mp3", "img/a.jpg"}
ops = [{"action": "upsert", "layer": "fx", "id": "h1",
        "value": {"id": "h1", "type": "arrange", "stage": "music_bus",
                  "kind": "hold", "music_id": "m1", "start": 8.5, "end": 9.0}},
       {"action": "upsert", "layer": "fx", "id": "f1",
        "value": {"id": "f1", "type": "arrange", "stage": "music_bus",
                  "kind": "filter", "filter": "lowpass", "music_id": "m1",
                  "start": 0.0, "end": 9.0}},
       {"action": "upsert", "layer": "fx", "id": "mt1_in",
        "value": {"id": "mt1_in", "type": "arrange", "stage": "music_bus",
                  "kind": "montage_intent", "music_id": "m1",
                  "params": {"t0": 0, "t1": 10, "pool": []}}},
       {"action": "upsert", "layer": "motion", "id": "ts1",
        "value": {"id": "ts1", "template": "typeset", "start": 2.0,
                  "end": 4.0, "params": {"text": "the *internet*",
                                         "voice": "magazine"}}},
       {"action": "upsert", "layer": "motion", "id": "st1",
        "value": {"id": "st1", "template": "still_motion", "start": 5.0,
                  "end": 7.0, "layer": "below_captions",
                  "params": {"image": "img/a.jpg", "path": "push_in"}}}]
out = b.apply_batch(base, ops, 60.0, allowed)
undo = [{"action": "remove", "layer": o["layer"], "id": o["id"]} for o in ops]
back = b.apply_batch(out, undo, 60.0, allowed)
assert s.edl_signature(back) == sig
refused = False
try:
    b.apply_batch(base, [{"action": "upsert", "layer": "fx", "id": "h2",
                          "value": {"id": "h2", "type": "arrange",
                                    "stage": "music_bus", "kind": "hole",
                                    "music_id": "m2", "start": 1.0,
                                    "end": 1.5}}], 60.0, allowed)
except Exception as e:
    refused = "user" in str(e)
print(json.dumps({"fx": sorted(i["id"] for i in out["fx"]),
                  "motion": sorted(m["id"] for m in out["motion"]),
                  "refused": refused, "types": sorted(s.FX_TYPES)}))
'''


def test_w2_devices_validate_batch_and_remove_in_the_backend_process():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-c", W2_SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["fx"] == ["f1", "h1", "mt1_in"]
    assert got["motion"] == ["st1", "ts1"]
    assert got["refused"] is True
    assert W2_TYPES <= set(got["types"])
