"""Track 1E devices in the BACKEND process, live (no preview switch).

An MCP client's apply_edit_batch runs in the backend, which loads the
worker's schemas.py / edit_batch.py by path with only backend/ on sys.path.
With 1E switched live, grade windows and finishes must validate, pass the
device-card gate, feed flash safety, survive the export preflight and be
removed there exactly as on the worker — and a refused shape must carry the
same reason. Runs in a clean subprocess (backend/ alone on sys.path).
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
assert {"grade_window", "finish"} <= set(s.FX_AUTHORABLE_TYPES), s.FX_TYPES
dur = 30.0
base = s.validate_edl(s.default_edl(dur), dur).model_dump()
sig = s.edl_signature(base)
ops = [
  {"action": "upsert", "layer": "fx", "id": "gw_mono", "value": {
    "id": "gw_mono", "type": "grade_window", "stage": "world", "start": 0.0,
    "end": 9.9, "saturation": 0.0, "ramp_out_f": 3,
    "device": "grade_section"}},
  {"action": "upsert", "layer": "fx", "id": "gw_iso", "value": {
    "id": "gw_iso", "type": "grade_window", "stage": "world", "start": 12.0,
    "end": 18.0, "isolate": {"hue": "#C0392B"}, "protect": ["skin"],
    "device": "grade_isolate_hue"}},
  {"action": "upsert", "layer": "fx", "id": "gw_lut", "value": {
    "id": "gw_lut", "type": "grade_window", "stage": "world", "start": 18.0,
    "end": 28.0, "lut": {"asset_key": "generated/1/lut-abc.cube",
    "strength": 0.8, "origin": "match", "match_t": 3.0},
    "device": "grade_shot_match"}},
  {"action": "upsert", "layer": "fx", "id": "fi_th", "value": {
    "id": "fi_th", "type": "finish", "stage": "world", "start": 24.0,
    "end": 24.12, "kind": "thermal", "matte_weighted": True,
    "device": "finish_thermal"}},
  {"action": "upsert", "layer": "fx", "id": "fi_ps", "value": {
    "id": "fi_ps", "type": "finish", "stage": "pre_captions", "start": 3.0,
    "end": 3.4, "kind": "pixel_stretch", "origin": 0.55,
    "direction": "left", "device": "finish_pixel_stretch"}},
]
out = b.apply_batch(base, ops, dur, {"generated/1/lut-abc.cube"})
assert [i["id"] for i in out["fx"]] == ["gw_mono", "gw_iso", "gw_lut",
                                        "fi_th", "fi_ps"], out["fx"]
assert out["fx"][1]["isolate"]["hue"] == "#c0392b"
events = len(video.wflash.flash_events(out))
preflight = video._export_edl_error(out, dur)
back = b.apply_batch(out, [{"action": "remove", "layer": "fx", "id": i}
                           for i in ("gw_mono", "gw_iso", "gw_lut", "fi_th",
                                     "fi_ps")], dur, set())
assert s.edl_signature(back) == sig
refusals = {}
for name, op in {
    "nothing": {"id": "x", "type": "grade_window", "stage": "world",
                "start": 1, "end": 2},
    "card": {"id": "x", "type": "grade_window", "stage": "world", "start": 1,
             "end": 2, "saturation": 0.0, "device": "finish_crt"},
    "palette": {"id": "x", "type": "finish", "stage": "world", "start": 1,
                "end": 2, "kind": "crt", "palette": "iron"},
    "media": {"id": "x", "type": "grade_window", "stage": "world",
              "start": 1, "end": 2, "lut": {"asset_key": "x/other.cube"}},
}.items():
    try:
        b.apply_batch(base, [{"action": "upsert", "layer": "fx", "id": "x",
                              "value": op}], dur, set())
        refusals[name] = None
    except Exception as e:
        refusals[name] = str(e)[:160]
print(json.dumps({"events": events, "preflight": preflight,
                  "refusals": refusals}))
'''


def test_1e_devices_live_in_the_backend_process():
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VALMERA_FX_PREVIEW")}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["events"] == 1                  # the 3-frame thermal flip
    assert got["preflight"] is None
    ref = got["refusals"]
    assert "changes at least one" in ref["nothing"]
    assert "writes fx type finish" in ref["card"]
    assert "takes no palette" in ref["palette"]
    assert "media attached" in ref["media"]
