"""Track 1B's fx types (``subject``, ``background``) IN THE BACKEND PROCESS,
live (no preview switch): the path an MCP client's apply_edit_batch takes.

The backend loads worker/schemas.py, edit_batch.py and flash_safety.py by
file path with only backend/ on sys.path, so fx_schemas/subject.py must
import nothing but pydantic and the standard library, and the device-card
gate must read worker/devices/cards/*.json from there. Every kind is written
with its card, the flash reading is the member's own, and a removal restores
the edit exactly. Runs in a clean subprocess (backend/ alone on sys.path).
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
assert "VALMERA_FX_PREVIEW" not in os.environ
from routes import video
s, b = video.wschemas, video.wbatch
assert {"subject", "background"} <= set(s.FX_AUTHORABLE_TYPES)
dur = 30.0
base = s.validate_edl(s.default_edl(dur), dur).model_dump()
sig = s.edl_signature(base)
keys = {"media/p1/clip.mp4": {}}
items = [
  {"id": "f", "type": "subject", "stage": "world", "start": 4.0, "end": 4.12,
   "kind": "fill", "colour": "white", "decay_f": 2,
   "device": "subject_fill_flash",
   "bind": {"on": "stress", "ref": 3, "rhythm": "speech"}},
  {"id": "p", "type": "subject", "stage": "world", "start": 5.0, "end": 7.0,
   "kind": "poster", "field": "accent", "device": "subject_poster"},
  {"id": "g", "type": "subject", "stage": "screen", "start": 7.0, "end": 9.0,
   "kind": "glow", "colour": "#ffd23f", "device": "subject_glow"},
  {"id": "o", "type": "subject", "stage": "world", "start": 2.0, "end": 3.0,
   "kind": "outline", "colour": "white", "device": "subject_outline"},
  {"id": "n", "type": "subject", "stage": "world", "start": 1.0, "end": 1.08,
   "kind": "negate", "device": "subject_negate"},
  {"id": "x", "type": "subject", "stage": "world", "start": 12.0,
   "end": 14.0, "kind": "exposure", "scope": "background", "ev": -1.5,
   "device": "subject_exposure"},
  {"id": "i", "type": "subject", "stage": "world", "start": 14.0,
   "end": 20.0, "kind": "isolate", "hue": "#c0392b",
   "device": "subject_isolate"},
  {"id": "c", "type": "subject", "stage": "world", "start": 20.0,
   "end": 21.0, "kind": "echo", "echo": {"offsets_f": [4, 8]},
   "device": "subject_echo"},
  {"id": "bs", "type": "background", "stage": "world", "start": 21.0,
   "end": 22.0, "kind": "source", "device": "background_swap",
   "source": {"kind": "asset", "asset_key": "media/p1/clip.mp4"}},
  {"id": "bb", "type": "background", "stage": "world", "start": 22.0,
   "end": 23.0, "kind": "blur", "device": "background_blur"},
  {"id": "bh", "type": "background", "stage": "world", "start": 23.0,
   "end": 23.6, "kind": "solid", "colour": "black", "hold_subject": True,
   "device": "background_subject_hold"},
  {"id": "st", "type": "background", "stage": "world", "start": 25.0,
   "end": 25.4, "kind": "strobe", "hold_f": 4, "device": "background_strobe",
   "sources": [{"kind": "colour", "colour": "white"},
               {"kind": "source", "src_t": 2.0}]},
]
ops = [{"action": "upsert", "layer": "fx", "id": i["id"], "value": i}
       for i in items]
out = b.apply_batch(base, ops, dur, keys)
events = [e["id"] for e in video.wflash.flash_events(out)]
back = b.apply_batch(out, [{"action": "remove", "layer": "fx", "id": i["id"]}
                           for i in items], dur, keys)
assert s.edl_signature(back) == sig
refused = []
for bad in ({"id": "z", "type": "subject", "stage": "world", "start": 1.0,
             "end": 2.0, "kind": "fill", "device": "background_blur"},
            {"id": "z", "type": "subject", "stage": "over_type",
             "start": 1.0, "end": 2.0, "kind": "glow"},
            {"id": "z", "type": "background", "stage": "world", "start": 1.0,
             "end": 2.0, "kind": "source", "source": {
                 "kind": "asset", "asset_key": "media/other/x.mp4"}}):
    try:
        b.apply_batch(base, [{"action": "upsert", "layer": "fx", "id": "z",
                              "value": bad}], dur, keys)
    except Exception as e:
        refused.append(str(e)[:80])
print(json.dumps({"written": [i["id"] for i in out["fx"]],
                  "events": events, "refused": len(refused),
                  "worker_modules": sorted(m for m in sys.modules
                                           if m in ("config", "renderer",
                                                    "subject_fx"))}))
'''


def test_subject_and_background_items_in_the_backend_process():
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VALMERA_FX_PREVIEW")}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert len(got["written"]) == 12
    # the fill, the 2-frame negate, the hold swap, each strobe change (at
    # the fastest render rate, 60 fps: 0.4 s of hold_f 4 is 6 swaps)
    assert got["events"].count("f") == 1 and got["events"].count("n") == 1
    assert got["events"].count("bh") == 1 and got["events"].count("st") == 6
    assert got["refused"] == 3
    # the compiler never reaches the backend: only the schema member does
    assert "subject_fx" not in got["worker_modules"]
