"""Track 1A (worker/devices/INTERFACES_W1.md §6.3, the behind_subject
rule): IN THE BACKEND PROCESS — the path an MCP client's apply_edit_batch
takes — a behind-subject motion graphic written with no measured mask is
accepted and names the program matte; a text whose ``behind`` names only
the program matte is filled in; both are removed exactly; and with the
program matte switched off (PROGRAM_MATTE_OFF=1) the Wave 0 refusal stands.

edit_batch loads worker/timeline.py through the backend's ``worker_timeline``
registration (routes/video.py), so this runs in a clean subprocess with
backend/ alone on sys.path, as on Render.

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
s, b = video.wschemas, video.wbatch
base = s.validate_edl({"keep": [[0.0, 10.0], [12.0, 30.0]], "motion": [
    {"id": "m1", "template": "word_slam", "start": 6.0, "end": 12.0,
     "params": {"text": "FLYING"}}]}, 40.0).model_dump()
sig = s.edl_signature(base)
out = {}
if os.environ.get("PROGRAM_MATTE_OFF") == "1":
    try:
        b.apply_batch(base, [{"action": "upsert", "layer": "motion",
                              "id": "m1", "value": {"id": "m1",
                              "layer": "behind_subject"}}], 40.0, {})
        out["refused"] = False
    except ValueError as e:
        out["refused"] = "subject mask" in str(e)
    print(json.dumps(out))
    raise SystemExit(0)
g = b.apply_batch(base, [{"action": "upsert", "layer": "motion", "id": "m1",
                          "value": {"id": "m1", "layer": "behind_subject"}}],
                  40.0, {})
out["motion_behind"] = g["motion"][0]["behind"]
back = b.apply_batch(g, [{"action": "upsert", "layer": "motion", "id": "m1",
                          "value": {"id": "m1", "layer": "above_captions"}}],
                     40.0, {})
out["motion_removed_exact"] = s.edl_signature(back) == sig
t = b.apply_batch(base, [{"action": "upsert", "layer": "texts", "id": "t1",
                          "value": {"id": "t1", "text": "ACROSS", "start": 8.0,
                                    "end": 13.0, "template": "title",
                                    "behind": {"asset_key": b.PROGRAM_MATTE_KEY}}}],
                  40.0, {})
out["text_behind"] = t["texts"][0]["behind"]
gone = b.apply_batch(t, [{"action": "remove", "layer": "texts", "id": "t1"}],
                     40.0, {})
out["text_removed_exact"] = s.edl_signature(gone) == sig
try:
    b.apply_batch(base, [{"action": "upsert", "layer": "texts", "id": "t2",
                          "value": {"id": "t2", "text": "X", "start": 1.0,
                                    "end": 3.0, "behind": {
                                        "asset_key": "matte/9/forged.mp4",
                                        "src_start": 1.0, "src_end": 3.0,
                                        "fp": "x"}}}], 40.0, {})
    out["forged_refused"] = False
except ValueError as e:
    out["forged_refused"] = "media attached" in str(e)
print(json.dumps(out))
'''


def _run(extra_env=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(SKIP_DB_INIT="1", DATABASE_URL="postgresql://stub/stub",
               PYTHONPATH=str(BACKEND))
    env.pop("PROGRAM_MATTE_OFF", None)
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_stale_client_puts_a_graphic_and_a_text_behind_the_subject():
    out = _run()
    mb = out["motion_behind"]
    # the window 6-12 s crosses the cut at program 10 s: source 6-14 s
    assert mb["asset_key"] == "sidecar:matte.person"
    assert (mb["src_start"], mb["src_end"]) == (6.0, 14.0)
    assert mb["fp"] == "program-matte" and mb["method"] == "program"
    assert out["motion_removed_exact"] is True
    tb = out["text_behind"]
    assert tb["asset_key"] == "sidecar:matte.person"
    assert (tb["src_start"], tb["src_end"]) == (8.0, 15.0)
    assert out["text_removed_exact"] is True
    # a measured-clip key the project never had is still refused
    assert out["forged_refused"] is True


def test_with_the_program_matte_off_the_wave0_refusal_stands():
    assert _run({"PROGRAM_MATTE_OFF": "1"}) == {"refused": True}
