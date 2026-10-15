"""Track 1D's devices validate, batch and are removed IN THE BACKEND PROCESS
(the path an MCP client's apply_edit_batch and a Studio batch take).

The backend loads worker/schemas.py, edit_batch.py and flash_safety.py by
path with only backend/ on sys.path; the 1D member module
(worker/fx_schemas/accents.py) must load there and judge every accent,
strobe and junction exactly as the worker does: the same items accepted,
the same shapes refused with the same reason, the same flash reading.

    cd backend && python -m pytest tests -q
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
s, b, fl = video.wschemas, video.wbatch, video.wflash
base = s.validate_edl({"keep": [[0.0, 10.0], [12.0, 30.0]],
                       "motion": [{"id": "m1", "template": "word_slam",
                                   "start": 6.0, "end": 9.0,
                                   "params": {"text": "FLYING"}}]},
                      30.0).model_dump()
sig = s.edl_signature(base)
keys = {"media/p1/a.png"}
ok = [
 {"id": "n1", "type": "accent", "stage": "over_type", "start": 6.0,
  "end": 6.04, "kind": "negative", "device": "accent_negative"},
 {"id": "w1", "type": "accent", "stage": "pre_captions", "start": 16.0,
  "end": 16.04, "kind": "white_hit", "decay_f": 4,
  "region": {"shape": "card"}, "device": "accent_white_hit"},
 {"id": "b1", "type": "accent", "stage": "pre_captions", "start": 17.0,
  "end": 17.08, "kind": "box_invert", "bands": 4, "seed": 7,
  "device": "accent_box_invert"},
 {"id": "s1", "type": "strobe", "stage": "pre_captions", "start": 22.0,
  "end": 22.4, "device": "strobe_frames", "frames": [
   {"source": {"kind": "asset", "asset_key": "media/p1/a.png"}},
   {"source": {"kind": "source", "src_t": 25.0}, "negate": True}]},
 {"id": "j1", "type": "junction", "stage": "junction", "at": 10.0,
  "style": "box_steps", "duration_s": 0.8, "device": "junction_box_steps"},
 {"id": "j2", "type": "junction", "stage": "junction", "at": 10.0,
  "style": "letter_window", "origin": {"kind": "glyph", "ref": "m1"},
  "device": "junction_letter_window"},
 {"id": "j3", "type": "junction", "stage": "junction", "at": 10.0,
  "style": "hit_flash", "device": "junction_hit_flash"}]
bad = [
 ({"id": "x1", "type": "accent", "stage": "pre_captions", "start": 1.0,
   "end": 1.04, "kind": "negative", "decay_f": 3}, "decay_f belongs"),
 ({"id": "x2", "type": "junction", "stage": "junction", "at": 10.0,
   "style": "datamosh", "device": "junction_datamosh"}, "dark"),
 ({"id": "x3", "type": "junction", "stage": "junction", "at": 10.0,
   "style": "iris", "origin": {"kind": "glyph", "ref": "nope"}},
  "not in this edit"),
 ({"id": "x4", "type": "strobe", "stage": "pre_captions", "start": 1.0,
   "end": 1.4, "frames": [{"source": {"kind": "next"}}]}, "a strobe frame")]
done = 0
for it in ok:
    up = [{"action": "upsert", "layer": "fx", "id": it["id"], "value": it}]
    out = b.apply_batch(base, up, 30.0, keys)
    assert any(i["id"] == it["id"] for i in out["fx"])
    back = b.apply_batch(out, [{"action": "remove", "layer": "fx",
                                "id": it["id"]}], 30.0, keys)
    assert s.edl_signature(back) == sig, it["id"]
    done += 1
allfx = b.apply_batch(base, [{"action": "upsert", "layer": "fx",
                              "id": i["id"], "value": i} for i in ok],
                      30.0, keys)
events = sorted((e["id"], e["t"]) for e in fl.flash_events(allfx))
refused = 0
for it, match in bad:
    try:
        b.apply_batch(base, [{"action": "upsert", "layer": "fx",
                              "id": it["id"], "value": it}], 30.0, keys)
    except Exception as e:
        assert match in str(e), (it["id"], str(e))
        refused += 1
print(json.dumps({"types": sorted(s.FX_TYPES), "ok": done,
                  "refused": refused, "events": events}))
'''


def test_1d_devices_validate_batch_and_remove_in_the_backend_process():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    env.pop("VALMERA_FX_PREVIEW", None)
    r = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert {"accent", "strobe", "junction"} <= set(got["types"])
    assert got["ok"] == 7 and got["refused"] == 4
    ids = {i for i, _t in got["events"]}
    # negatives, card hits, seeded boxes, strobe changes and the hit flash
    # are flashes; box steps' small first step is not, its large ones are
    assert {"n1", "w1", "b1", "s1", "j3"} <= ids
    assert ("n1", 6.0) in [tuple(e) for e in got["events"]]
