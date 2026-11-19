"""The workflow films' sound-effect set (lane sfx, 20 Nov 2026): every workflow run uses it, placed by what the picture
does; the set builder trims each effect as an editor would and leaves out what a phone would play as a defect."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import chain  # noqa: E402
import sfx_set  # noqa: E402

SR = sfx_set.SR


def _set(tmp_path):
    root = tmp_path / "sfx_ref"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps([{"id": "mx_whoosh_1", "file": "mx_whoosh_1.flac"}]))
    return str(root)


def test_every_run_designs_and_mixes_with_the_set(tmp_path, monkeypatch):
    monkeypatch.delenv(chain.SOUND_ENV, raising=False)
    monkeypatch.delenv(chain.PLACEMENT_ENV, raising=False)
    monkeypatch.setattr(chain, "SOUND_SET", _set(tmp_path))
    assert chain.use_sound_set() == chain.SOUND_SET
    assert os.environ[chain.SOUND_ENV] == chain.SOUND_SET and os.environ[chain.PLACEMENT_ENV] == "screen"
    out = subprocess.run([sys.executable, "-c", f"import os; print(os.environ.get('{chain.SOUND_ENV}'), os.environ.get('{chain.PLACEMENT_ENV}'))"],
                         capture_output=True, text=True).stdout.split()
    assert out == [chain.SOUND_SET, "screen"]                     # the render and the film build inherit it
    assert chain.sound_manifest() == os.path.join(chain.SOUND_SET, "manifest.json")


def test_an_explicit_setting_wins_and_without_the_set_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, "SOUND_SET", str(tmp_path / "missing"))
    monkeypatch.delenv(chain.SOUND_ENV, raising=False)
    monkeypatch.delenv(chain.PLACEMENT_ENV, raising=False)
    assert chain.use_sound_set() is None
    assert chain.SOUND_ENV not in os.environ and chain.PLACEMENT_ENV not in os.environ
    monkeypatch.setenv(chain.SOUND_ENV, "/elsewhere/set")
    assert chain.use_sound_set() == "/elsewhere/set"


def test_a_loaded_sound_module_follows_the_set(tmp_path, monkeypatch):
    class M:
        SOUNDS = "/product/library"
        _lib = {"t": {"x": 1}}
    monkeypatch.setitem(sys.modules, "score.sound", M)
    monkeypatch.delenv(chain.SOUND_ENV, raising=False)
    monkeypatch.delenv(chain.PLACEMENT_ENV, raising=False)
    monkeypatch.setattr(chain, "SOUND_SET", _set(tmp_path))
    chain.use_sound_set()
    assert M.SOUNDS == chain.SOUND_SET and M._lib == {}


def _burst(dur, peak_at, tail, sr=SR, pad=0.4, seed=0):
    rng = np.random.default_rng(seed)
    n = int((dur + 2 * pad) * sr)
    t = np.arange(n) / sr
    env = np.where(t < pad + peak_at, np.clip((t - pad) / max(peak_at, 1e-3), 0, 1) ** 2,
                   np.exp(-(t - pad - peak_at) / max(tail, 1e-3)))
    env[t < pad] = 0
    x = (rng.standard_normal(n) * env * 0.5).astype(np.float32)
    return np.stack([x, x], axis=1)


def test_an_editor_trim_for_each_class():
    sw, how = sfx_set.cut(_burst(3.0, 0.3, 0.8), "whoosh")
    assert len(sw) / SR <= sfx_set.CUT["whoosh"]["max_len"] + 1e-3 and how["cut_s"][0] >= 0.35
    assert np.abs(sw[-int(0.005 * SR):]).max() < 0.02 * np.abs(sw).max()        # it fades out, never stops dead
    rs, _ = sfx_set.cut(_burst(6.0, 5.0, 0.05), "riser")
    pk = int(np.argmax(np.abs(rs).mean(axis=1)))
    assert len(rs) / SR <= sfx_set.CUT["riser"]["climb"] + 0.1 and len(rs) - pk <= int(0.05 * SR)   # its climb, cut at the top
    im, _ = sfx_set.cut(_burst(5.0, 0.02, 1.5), "impact")
    assert len(im) / SR <= sfx_set.CUT["impact"]["ring"] + 0.1
    assert np.abs(im[-int(0.01 * SR):]).max() < 0.02 * np.abs(im).max()


def test_left_out_for_what_a_phone_would_play(tmp_path):
    row = {"id": "x", "src": "mixkit:1", "class": "hit"}
    good = dict(top_hz=21000, wall_db=40, end_db=-70, src_floor_db=-90, src_clip_runs=0)
    assert sfx_set.defects(row, good) == []
    assert sfx_set.defects(row, dict(good, top_hz=11000, wall_db=50))                # an encoder wall a phone plays
    assert sfx_set.defects(row, dict(good, src_clip_runs=3))
    assert sfx_set.defects(row, dict(good, src_floor_db=-30))                         # hiss under a short sound
    assert sfx_set.defects(row, dict(good, end_db=-20))                               # a chopped tail
    assert sfx_set.defects(dict(row, **{"class": "riser"}), dict(good, end_db=-2)) == []   # a riser is cut dead by design
    assert sfx_set.defects(dict(row, src="lib:tick_1"), dict(good, src_floor_db=-20)) == []  # the library's own, audited


def test_the_source_table_names_every_sound_a_workflow_calls_by_name():
    rows = {r["id"]: r for r in json.load(open(sfx_set.SOURCES))["rows"]}
    for sid in ("whoosh_soft_1", "whoosh_soft_2", "shutter_1", "shutter_2", "tick_1", "pop_1", "click_2", "typing_2", "riser_2"):
        assert sid in rows, sid
    assert all(r["class"] in sfx_set.CUT for r in rows.values())
    assert all(r.get("family") in (None, "paper", "shutter", "type", "glitch") for r in rows.values())
