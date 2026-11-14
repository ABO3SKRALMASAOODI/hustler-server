"""The one SFX quality filter, at every sound path (owner, 15 Nov 2026: our sound was "terrible", the sting "a mountain
fall"; the independent check found the first filter covered only sound.py's own picks).

worker/sfx_filter.py reads lane STING-REF's signal audit (score/sfx_quality.json) and replaces a flagged recording by
its nearest unflagged one on the audit's ruler. No path may play a flagged file: sound.py's pass-through of a
workflow's sounds (and with the design off, asked or failed), the scene engine's mixer, the editor's listing, search,
template picks and add_sfx (by id or by an asset key the project already holds). Signal only: no model, no network."""
import json
import os
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import sfx_filter as SF  # noqa: E402
import sound_library  # noqa: E402
from score import sound as SD  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which(FF) is None, reason="needs ffmpeg")
Q = json.load(open(os.path.join(os.path.dirname(HERE), "score", "sfx_quality.json")))


def test_every_flagged_recording_has_a_clean_replacement_nearest_on_the_audit_ruler():
    bad = SF.flagged()
    assert bad == set(Q["excluded"]) and len(bad) >= 18
    rep = SF.replacements()
    lib = {r["id"]: r for r in sound_library.catalog()}
    for sid, new in rep.items():
        assert new and new not in bad and new in lib, (sid, new)
        same = [r for r in SF.ranking(sid)]
        assert same[0][1] == new                                    # the nearest by the audit's distance
        roles_clean = [k for k, r in lib.items() if r["role"] == lib[sid]["role"] and k not in bad]
        if roles_clean:
            assert lib[new]["role"] == lib[sid]["role"], (sid, new)  # the gesture keeps its kind
        assert not lib[new].get("family") or lib[new].get("family") == lib[sid].get("family")
    # the ones the workflows named: each lands on a clean recording of its kind
    assert rep["impact_1"] == "impact_9" and rep["impact_10"] == "impact_8"
    assert rep["click_1"] == "click_2" and rep["cash_register_1"] == "click_10"
    assert SF.nearest("whoosh_soft_1") == "whoosh_soft_1"            # an unflagged one is itself


def test_swap_keeps_the_hit_and_its_loudness():
    x1, x9 = sound_library.get("impact_1"), sound_library.get("impact_9")
    it = dict(id="a", s="impact_1", file="impact_1.flac", event_f=300, start_s=round(10.0 - x1["peak_s"], 4), trim_s=0.0, db=-8.0,
              peak_s=x1["peak_s"])
    out, ch = SF.swap(it)
    assert ch and out["s"] == "impact_9" and out["file"] == "impact_9.flac" and out["replaced"]["id"] == "impact_1"
    assert abs(out["start_s"] - out["trim_s"] + out["peak_s"] - 10.0) < 1e-3          # the hit lands where it did
    assert abs(out["db"] - (-8.0 + x1["hit_lufs"] - x9["hit_lufs"])) < 0.01            # at the same hit loudness
    # a hit near 0 s (the old file skipped 0.7 s in to land at 0.055 s): the new one lands there too
    out, _ = SF.swap(dict(it, start_s=0.0, trim_s=0.7, peak_s=x1["peak_s"]))
    assert abs(out["start_s"] - out["trim_s"] + out["peak_s"] - (x1["peak_s"] - 0.7)) < 1e-3 and out["start_s"] >= 0.0
    # sound.py's items carry a lead over the hit (LEAD_S): kept
    lead = SD.LEAD_S
    it2 = dict(it, peak_s=round(float(x1["hit_s"]) + lead, 4), start_s=round(10.0 - float(x1["hit_s"]) - lead, 4))
    out, _ = SF.swap(it2)
    assert abs(out["peak_s"] - (float(x9.get("hit_s") or x9["peak_s"]) + lead)) < 1e-3
    for keep in (dict(it, s="whoosh_soft_1", file="whoosh_soft_1.flac"), dict(id="e", s="echo", event_f=3)):
        assert SF.swap(keep) == (keep, False)


def _film(dur=40.0, fps=30, seed=0):
    rng = np.random.default_rng(seed)
    ev, f = [], 20
    while f < dur * fps - 40:
        ev.append({"f": f, "kind": "picture", "size": float(rng.choice([0.25, 0.45, 0.7, 0.95]))})
        f += int(rng.integers(20, 36))
    for t in (0.4, 9.5, 21.0, 33.3):
        ev.append({"f": int(t * fps) + 7, "kind": "display", "size": 0.7})
    return sorted(ev, key=lambda e: e["f"])


def _existing(fps=30):
    """A workflow's own sounds as a skill writes them: impact_1 on the payoff (podcast shorts, long-form), heartbeat_1
    (wf1's recipes), click_1 (marketing), cash_register_1 (stories)."""
    out = []
    for k, (sid, t) in enumerate((("impact_1", 2.0), ("heartbeat_1", 12.0), ("click_1", 18.0), ("cash_register_1", 26.0),
                                  ("impact_10", 36.5))):
        x = sound_library.get(sid)
        out.append(dict(id=f"wf{k}", s=sid, file=x["file"], event_f=int(t * fps), start_s=round(t - x["peak_s"], 4), trim_s=0.0,
                        db=-10.0, peak_s=x["peak_s"]))
    return out


@needs_ffmpeg
@pytest.mark.parametrize("own_tiers", [False, True])
def test_sound_design_never_passes_a_flagged_workflow_sound_through(own_tiers):
    """sound.py keeps a workflow's sounds and may raise them to hero level (own_tiers): a flagged one plays its nearest
    clean recording, never as written."""
    dur, fps = 40.0, 30
    ctx = {"fps": fps, "dur_f": int(dur * fps), "existing": _existing(fps), "speech": None, "voice": None,
           "words": [(0.2, dur - 2.5)], "bed": True, "np10": 11.0, "seed": SD.film_seed("pass-through"), "own_tiers": own_tiers}
    sounds, rep = SD.plan_sounds(_film(dur, fps), ctx)
    played = {s["s"] for s in sounds} | {os.path.basename(s.get("file", ""))[:-5] for s in sounds if s.get("file")}
    assert not played & SF.flagged(), sorted(played & SF.flagged())
    assert {r["id"] for r in rep["replaced"]} == {"impact_1", "heartbeat_1", "click_1", "cash_register_1", "impact_10"}
    assert {s["s"] for s in sounds if s.get("replaced")} >= {"impact_9", "impact_8"}


def test_with_the_design_off_asked_or_failed_no_flagged_sound_stays(monkeypatch):
    l1 = {"fps": 30, "dur_f": 1200, "sound": _existing(), "items": [], "assets": {}}
    # an ask bound to the sound department: the Director's lines stand, but never a flagged file
    sounds, rep = SD.design(json.loads(json.dumps(l1)), asks={"a1": {"id": "a1", "probe": "sfx_count"}})
    assert rep.get("asked") and len(sounds) == 5
    for s in sounds:
        assert s["s"] not in SF.flagged()
    monkeypatch.setenv("VALMERA_SOUND_DESIGN", "0")
    got = SD.apply(json.loads(json.dumps(l1)))
    assert not {s["s"] for s in got["sound"]} & SF.flagged()
    monkeypatch.delenv("VALMERA_SOUND_DESIGN")
    monkeypatch.setattr(SD, "design", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = SD.apply(json.loads(json.dumps(l1)))
    assert got["sound_design"].get("fault") and not {s["s"] for s in got["sound"]} & SF.flagged()


@needs_ffmpeg
def test_the_scene_mixer_never_plays_a_flagged_file(monkeypatch):
    from scene import audio as AU
    read = []
    real = AU.pcm

    def spy(path, seconds=None):
        read.append(os.path.basename(path))
        return real(path, seconds)
    monkeypatch.setattr(AU, "pcm", spy)
    plan = SimpleNamespace(dur=40 * 30, fps=30, doc={"spine": [], "sound": _existing()}, assets={})
    _voice, fx, rep = AU.split_audio(plan)
    assert read and not {r[:-5] for r in read} & SF.flagged(), read
    assert {r["id"] for r in rep["replaced"]} == {"impact_1", "heartbeat_1", "click_1", "cash_register_1", "impact_10"}
    assert rep["sounds"] == 5 and np.abs(np.asarray(fx)).max() > 0


def test_the_editor_never_lists_or_picks_a_flagged_recording():
    import motion_tools
    bad = SF.flagged()
    listing = motion_tools.list_sound_library(None)
    for role in sound_library.roles():
        listing += motion_tools.list_sound_library(None, role=role)
    for b in bad:
        assert f"sound:{b} " not in listing, b
    # a role whose every recording is flagged lists its nearest clean ones instead of nothing
    for role in ("ding", "cash", "heartbeat"):
        assert "sound:" in motion_tools.list_sound_library(None, role=role), role
    assert not {r["id"] for r in sound_library.playable()} & bad
    assert not {sound_library.pick(k, str(i))["id"] for k in sound_library.roles() for i in range(16)} & bad
    for q in ("cash register money", "heartbeat", "ding notification", "boom impact", "click button", "riser tension"):
        assert not {r["id"] for r in sound_library.search(q)} & bad, q


def test_add_sfx_resolves_a_flagged_id_or_key_to_its_replacement(monkeypatch):
    import agent_tools
    import motion_tools
    monkeypatch.setattr(motion_tools, "ensure_library_asset", lambda ctx, sid: sound_library.asset_key(7, sid))
    ctx = SimpleNamespace(project_id=7)
    for sid in sorted(SF.flagged()):
        got, err = motion_tools.resolve_library_reference(ctx, f"sound:{sid}")
        assert not err and got["sound_id"] == SF.nearest(sid) and got["replaced"] == sid, (sid, got, err)
        assert sid in got["note"] and got["sound_id"] in got["note"]
        assert sound_library.id_for_key(got["storage_key"]) == SF.nearest(sid)
        # the project already holds the flagged recording's upload (an earlier add_sfx): it resolves the same way
        got2, err2 = agent_tools._resolve_sfx(ctx, sound_library.asset_key(7, sid))
        assert not err2 and got2["sound_id"] == SF.nearest(sid), (sid, got2, err2)
    got, _ = motion_tools.resolve_library_reference(ctx, "sound:whoosh_soft_1")
    assert got["sound_id"] == "whoosh_soft_1" and "replaced" not in got
