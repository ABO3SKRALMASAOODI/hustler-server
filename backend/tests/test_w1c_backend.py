"""Track 1C in the BACKEND process: the ``region`` fx type, the
``MotionItem.composite`` slot and the ``type_window`` template validate,
batch and are removed exactly as on the worker — the path an MCP client's
apply_edit_batch takes (worker/devices/INTERFACES_W1.md §5).

The backend loads worker/schemas.py, edit_batch.py and the motion modules
by FILE PATH with only backend/ on sys.path; fx_schemas/composite.py must
import nothing but pydantic, the standard library and its own package. The
check runs in a clean subprocess (backend/ alone on sys.path, as on Render)
with track 1C live (LIVE = True in its module, no preview switch).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SNIPPET = r'''
import json, os, re, sys
sys.path = [p for p in sys.path if not p.rstrip("/").endswith("worker")]
sys.path.insert(0, os.getcwd())
from routes import video
s, b = video.wschemas, video.wbatch
assert "region" in s.FX_TYPES, s.FX_TYPES
assert "MotionItem.composite" in s._FX_SLOT_TYPES
dur = 30.0
base = s.validate_edl({"keep": [[0.0, 10.0], [12.0, 20.0]],
    "motion": [{"id": "m1", "template": "word_slam", "start": 2.0,
                "end": 4.0, "params": {"text": "HELLO"}}]}, dur).model_dump()
sig = s.edl_signature(base)
keys = {"media/p1/broll.mp4"}
ok = [
  ([{"action": "upsert", "layer": "motion", "id": "m1", "value": {"id": "m1",
     "composite": {"blend": "auto", "ghost": "auto", "region": {"shape":
     "ellipse", "box": [0.1, 0.1, 0.9, 0.6]}}}}],
   [{"action": "upsert", "layer": "motion", "id": "m1", "value": {"id": "m1",
     "composite": None}}]),
  ([{"action": "upsert", "layer": "motion", "id": "tw1", "value": {"id": "tw1",
     "template": "type_window", "start": 1.0, "end": 4.0,
     "layer": "above_captions", "params": {"text": "YOU SHOULD KNOW I",
     "reveal": "words_open"}, "composite": {"fill": {"kind": "asset",
     "asset_key": "media/p1/broll.mp4", "loop": True}, "field": "black"}}}],
   [{"action": "remove", "layer": "motion", "id": "tw1"}]),
  ([{"action": "upsert", "layer": "fx", "id": "rg1", "value": {"id": "rg1",
     "type": "region", "stage": "pre_captions", "start": 1.0, "end": 3.0,
     "effect": "solarise", "colour": "#ff00ff", "device": "region_tone",
     "region": {"shape": "polygon", "keys": [
       {"t": 0, "points": [[0.3, 0.4], [0.7, 0.4], [0.5, 0.7]]},
       {"t": 1.5, "points": [[0.2, 0.3], [0.8, 0.35], [0.5, 0.8]],
        "ease": "in_out"}]}}}],
   [{"action": "remove", "layer": "fx", "id": "rg1"}]),
  ([{"action": "upsert", "layer": "fx", "id": "rg2", "value": {"id": "rg2",
     "type": "region", "stage": "over_type", "start": 2.0, "end": 4.0,
     "effect": "refraction", "device": "region_material",
     "region": {"shape": "glyph", "ref": "m1"}}}],
   [{"action": "remove", "layer": "fx", "id": "rg2"}]),
]
done = 0
for ops, undo in ok:
    out = b.apply_batch(base, ops, dur, keys)
    back = b.apply_batch(out, undo, dur, keys)
    assert s.edl_signature(back) == sig, ops
    done += 1
bad = [
  ([{"action": "upsert", "layer": "motion", "id": "m1", "value": {"id": "m1",
     "composite": {"fill": {"kind": "program", "offset_s": 9}}}}],
   "at most 2 s away"),
  ([{"action": "upsert", "layer": "fx", "id": "r", "value": {"id": "r",
     "type": "region", "stage": "world", "start": 1, "end": 2,
     "effect": "negate", "device": "composite_blend",
     "region": {"shape": "rect", "box": [0, 0, 1, 1]}}}],
   "region_\\* card"),
  ([{"action": "upsert", "layer": "fx", "id": "r", "value": {"id": "r",
     "type": "region", "stage": "world", "start": 1, "end": 2,
     "effect": "negate", "region": {"shape": "glyph", "ref": "zz"}}}],
   "not in this edit"),
]
refused = 0
for ops, match in bad:
    try:
        b.apply_batch(base, ops, dur, keys)
    except Exception as e:
        assert re.search(match, str(e)), (match, str(e))
        refused += 1
flash = video.wflash.flash_events(b.apply_batch(base, [{"action": "upsert",
    "layer": "fx", "id": "f", "value": {"id": "f", "type": "region",
    "stage": "world", "start": 1.0, "end": 1.2, "effect": "negate",
    "region": {"shape": "rect", "box": [0, 0, 1, 1]}}}], dur, keys))
print(json.dumps({"ok": done, "refused": refused, "bad": len(bad),
                  "flash": [e["t"] for e in flash]}))
'''


def test_1c_devices_validate_batch_and_remove_in_the_backend_process():
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VALMERA_FX_PREVIEW")}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["ok"] == 4 and got["refused"] == got["bad"] == 3
    assert got["flash"] == [1.0]
