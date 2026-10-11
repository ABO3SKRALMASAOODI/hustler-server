"""Refbench tools added for the Wave 0 baseline board and AT-0D-1: the
fixture clock (vfl), exact ink from the renderer's own clips (ink), the
fixture catalogue, syllable templates, verdict re-judging and the baseline
EDL move. CI-safe: synthetic inputs only, no footage, no references.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

from benchmarks.refbench import catalog, ink, labels          # noqa: E402
from benchmarks.refbench import verdict                       # noqa: E402
from benchmarks.refbench import vfl as vflmod                 # noqa: E402

# A window like V04/rogan_gamers: 29.97 fps, video starting 38 ms into the
# fixture, file_t0 not a multiple of 0.01 (the case that moved a cut).
WIN = {"fps": 29.97003, "file_start": 0.038467, "file_t0": 110.8056,
       "file_span": [110.844, 162.244], "sha256": "0" * 64}


def _frame(w, t_source):
    """The source frame index a boundary at t_source starts on."""
    return vflmod._frame_at(w, t_source - w["file_t0"])


# ── fixture clock ─────────────────────────────────────────────────────────

def test_plain_shift_can_move_a_cut_by_a_frame():
    t = 130.80
    plain = round(t - WIN["file_t0"], 2)               # what an EDL stores
    assert vflmod._frame_at(WIN, plain) != _frame(WIN, t)


def test_file_time_lands_every_boundary_on_its_source_frame():
    rng = np.random.default_rng(3)
    for t in np.round(rng.uniform(112.0, 160.0, 400), 2):
        ft = vflmod.file_time(WIN, float(t))
        assert round(ft, 2) == ft                      # still 0.01 s
        assert vflmod._frame_at(WIN, ft) == _frame(WIN, float(t))


def test_edl_to_fixture_moves_every_source_clock_field():
    edl = {"keep": [[129.29, 129.81], [130.8, 138.62]],
           "speed": [{"start": 131.0, "end": 132.0, "factor": 1.5}],
           "volume": [{"start": 130.9, "end": 131.5, "gain_db": -3}],
           "frame": {"follow": [{"t0": 120.0, "t1": 138.04,
                                 "k": [[136.085, 0.5, 0.5]]}],
                     "focus_track": [{"t0": 120.0, "t1": 138.0, "x": 0.5,
                                      "y": 0.5}]},
           "effects": {"picture_cards": [{
               "follow": [{"t0": 0.0, "t1": 3314.8,
                           "k": [[131.795, 0.57, 0.42]]}],
               "cut_steps": [{"t0": 131.0, "t1": 134.48, "scale": 0.9}],
               "panels": [{"follow": [{"t0": 130.0, "t1": 140.0,
                                       "k": [[133.0, 0.5, 0.5]]}]}]}]},
           "inserts": [{"hold": {"room_tone": [130.26, 130.66]}}],
           "texts": [{"start": 1.0, "end": 2.0,
                      "behind": {"src_start": 131.2, "src_end": 132.0}}],
           "motion": [{"start": 3.0, "end": 4.0}]}
    out, q, nudged = vflmod.edl_to_fixture(WIN, edl)
    assert round(q, 2) == q
    assert vflmod.unshifted(WIN, edl, out) == []
    for (a, b), (fa, fb) in zip(edl["keep"], out["keep"]):
        assert vflmod._frame_at(WIN, fa) == _frame(WIN, a)
        assert vflmod._frame_at(WIN, fb) == _frame(WIN, b)
    # program-clock fields are left alone
    assert out["texts"][0]["start"] == 1.0
    assert out["motion"][0] == {"start": 3.0, "end": 4.0}
    card = out["effects"]["picture_cards"][0]
    assert card["follow"][0]["k"][0][0] == pytest.approx(131.795 - q)
    assert card["cut_steps"][0]["t0"] == pytest.approx(131.0 - q)


def test_unshifted_names_a_forgotten_field():
    edl = {"keep": [[130.0, 131.0]], "mystery": {"at": 140.5}}
    out, _q, _n = vflmod.edl_to_fixture(WIN, edl)
    assert vflmod.unshifted(WIN, edl, out) == ["edl.mystery.at"]


def test_render_source_restamps_only_far_into_the_fixture(monkeypatch):
    monkeypatch.setattr(vflmod, "restamped", lambda w, q: f"restamped:{q}")
    win = dict(WIN, path="/fixture.mp4")
    e, src, q, nudged, mode = vflmod.render_source(
        win, {"keep": [[130.8, 132.0]]})
    assert mode == "copyts" and src == "restamped:110.8" and nudged == []
    assert e["keep"][0][0] == pytest.approx(20.0)      # 130.80 - 110.80
    e, src, q, nudged, mode = vflmod.render_source(
        win, {"keep": [[112.0, 113.0]]})
    assert mode == "plain" and src == "/fixture.mp4"


# ── ink: the renderer's own clip ──────────────────────────────────────────

def _clip(n=10, h=40, w=60):
    """A clip whose glyph (alpha 255, red) appears on frame 2 and settles."""
    c = np.zeros((n, h, w, 4), np.uint8)
    for k in range(2, n):
        c[k, 10:30, 10:50] = (230, 20, 20, 255)
        c[k, 8:10, 10:50] = (0, 0, 0, 90)              # a soft shadow
    return c


@pytest.fixture
def rec(monkeypatch):
    clip = _clip()
    monkeypatch.setattr(ink, "clip_frames", lambda p: clip)
    return {"path": "clip.mov", "x": 100, "y": 50, "w": 60, "h": 40,
            "start": 1.0, "end": 2.0}


def test_clip_index_and_window(rec):
    fps = 10.0
    assert ink.clip_index(rec, 9, fps) is None         # before the item
    assert ink.clip_index(rec, 10, fps) == 0
    assert ink.clip_index(rec, 15, fps) == 5
    assert ink.clip_index(rec, 20, fps) is None        # end is exclusive


def test_glyphs_place_the_clip_on_the_canvas(rec):
    g = ink.glyphs(rec, 14, 10.0, (200, 300))
    assert g["alpha"].shape == (200, 300)
    ys, xs = np.nonzero(g["alpha"] >= ink.GLYPH_ALPHA)
    assert (ys.min(), xs.min()) == (60, 110)           # y 50+10, x 100+10
    assert ink.glyphs(rec, 9, 10.0, (200, 300)) is None


def test_visible_and_item_metrics_see_occlusion(rec):
    frame = np.full((200, 300, 3), 15, np.uint8)
    frame[60:80, 110:150] = (230, 20, 20)              # glyph drawn ...
    frame[60:80, 130:150] = (40, 40, 40)               # ... half covered
    m = ink.item_metrics(frame, rec, 14, 10.0)
    assert m["glyph_visible"]["value"] == pytest.approx(0.5, abs=0.08)
    assert m["legibility"]["value"] > 30


def test_landing_frame_from_rendered_frames(rec, monkeypatch):
    """The glyph shows from clip frame 2 = program frame 12 at 10 fps."""
    import qa_metrics
    fps = 10.0
    rendered = {}
    for f in range(0, 22):
        im = np.full((200, 300, 3), 15, np.uint8)
        k = ink.clip_index(rec, f, fps)
        if k is not None and k >= 2:
            im[60:80, 110:150] = (230, 20, 20)
        rendered[f] = im

    def read_frames(path, frames=None, **kw):
        keep = sorted(set(frames) & set(rendered))
        return keep, [rendered[f] for f in keep]
    monkeypatch.setattr(qa_metrics, "probe",
                        lambda p: {"width": 300, "height": 200, "fps": fps})
    monkeypatch.setattr(qa_metrics, "read_frames", read_frames)
    ev, det = ink.landing_frame("render.mp4", rec, fps)
    assert ev == 12
    assert det["base_share"] == 0.0


# ── catalogue ─────────────────────────────────────────────────────────────

def test_catalog_items_and_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_FIXTURES_DIR", str(tmp_path))
    media = tmp_path / "footage" / "HC-C" / "c1"
    media.mkdir(parents=True)
    (media / "a.mp4").write_bytes(b"abc")
    sha = vflmod.sha256_file(str(media / "a.mp4"))
    data = {"schema": catalog.SCHEMA, "root": str(tmp_path),
            "roles": {"HC-C": {"best": ["HC-C/c1"]}},
            "items": [{"id": "HC-C/c1", "role": "HC-C", "secondary_roles": [],
                       "status": "interim stand-in",
                       "primary": {"path": "footage/HC-C/c1/a.mp4",
                                   "sha256": sha}}]}
    (tmp_path / "catalog.json").write_text(json.dumps(data))
    assert [i["id"] for i in catalog.items("HC-C")] == ["HC-C/c1"]
    assert catalog.best("HC-C") == ["HC-C/c1"]
    assert catalog.verify() == {"HC-C/c1": "ok"}
    (media / "a.mp4").write_bytes(b"abd")
    assert catalog.verify() == {"HC-C/c1": "sha mismatch"}
    assert catalog.media_path(catalog.item("HC-C/c1")).endswith("a.mp4")


def test_catalog_absent_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_FIXTURES_DIR", str(tmp_path))
    assert catalog.load() is None and catalog.items() == []


# ── labels: honest coverage ───────────────────────────────────────────────

def test_syllable_template_and_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_FIXTURES_DIR", str(tmp_path))
    (tmp_path / "asr" / "V02").mkdir(parents=True)
    words = [{"w": f"w{i}", "t0": 840.0 + i, "t1": 840.5 + i}
             for i in range(10)]
    (tmp_path / "asr" / "V02" / "thiel_flying_cars.json").write_text(
        json.dumps({"words": words}))
    p = labels.syllable_template("V02", "thiel_flying_cars",
                                 words=[2, 5])
    on = json.load(open(p))
    assert [w["w"] for w in on["words"]] == ["w2", "w5"]
    assert on["status"] == "todo"
    assert all(w["syllables"] == [] for w in on["words"])
    win = vflmod.window("V02", "thiel_flying_cars")
    cov = labels.coverage(win, "syllables")
    assert cov["ok"] is False and cov["filled"] == 0
    for w in on["words"]:
        w["syllables"] = [w["asr"][0]]
    on["status"] = "reviewed"
    json.dump(on, open(p, "w"))
    assert labels.coverage(win, "syllables")["ok"] is True


def test_unreviewed_points_do_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_FIXTURES_DIR", str(tmp_path))
    d = tmp_path / "labels" / "V12" / "gestures" / "fingertips"
    d.mkdir(parents=True)
    pts = {"status": "bootstrap_unreviewed",
           "frames": {str(f): {"only": {"index_tip": [1, 2]}}
                      for f in range(30)}}
    (d / "points.json").write_text(json.dumps(pts))
    win = vflmod.window("V12", "gestures")
    assert labels.coverage(win, "fingertips")["ok"] is False
    pts["status"] = "reviewed"
    (d / "points.json").write_text(json.dumps(pts))
    assert labels.coverage(win, "fingertips")["ok"] is True


# ── verdicts ──────────────────────────────────────────────────────────────

def _case():
    return {"id": "AT-0D-1c", "judges": {"lenses": ["motion", "craft",
                                                    "owner_rules"],
                                         "pass": "2of3_no_major"}}


def test_a_metric_without_a_bar_is_reported_not_judged():
    m = {"land_err_f": {"value": 0, "bar": "<=1", "pass": True},
         "cost_ratio": {"value": 1.4, "bar": None, "pass": None}}
    js = [{"lens": lens, "verdict": "matches", "major_defect": False,
           "notes": ""} for lens in ("motion", "craft", "owner_rules")]
    assert verdict.decide(_case(), m, {"render_ok": True}, js) is True
    m["land_err_f"]["pass"] = False
    assert verdict.decide(_case(), m, {"render_ok": True}, js) is False


def test_rejudge_recomputes_pass(tmp_path):
    case = _case()
    v = verdict.build(case, render_path=None, variant="final",
                      metrics={"land_err_f": {"value": 0, "bar": "<=1",
                                              "pass": True}},
                      standing={"render_ok": True}, judges=[])
    verdict.write(v, str(tmp_path))
    assert v["pass"] is None
    for lens in ("motion", "craft", "owner_rules"):
        json.dump({"lens": lens, "verdict": "below" if lens == "craft"
                   else "matches", "major_defect": lens == "craft",
                   "notes": ""},
                  open(tmp_path / f"judge_{lens}.verdict.json", "w"))
    out = verdict.rejudge(case, str(tmp_path))
    assert out["pass"] is False and len(out["judges"]) == 3


# ── the baseline's EDL and index move ─────────────────────────────────────

def test_shift_index_moves_words_and_spatial_samples():
    from benchmarks.refbench import baseline
    idx = {"words": [{"w": "a", "t0": 130.0, "t1": 130.2}],
           "spatial": {"v": 1, "samples": [{"t": 100.0}, {"t": 131.0},
                                           {"t": 300.0}]},
           "video": {"width": 1, "height": 1, "duration": 9999}}
    win = dict(WIN, width=1920, height=1080)
    out = baseline.shift_index(idx, 110.8, win)
    assert out["words"][0]["t0"] == pytest.approx(19.2)
    assert [s["t"] for s in out["spatial"]["samples"]] == \
        [pytest.approx(20.2)]
    assert out["video"]["width"] == 1920


def test_localise_inserts_maps_library_sfx_and_refuses_others(tmp_path):
    import sound_library
    from benchmarks.refbench import baseline
    sid = sound_library.catalog()[0]["id"]
    key = sound_library.asset_key(7, sid)
    edl = {"sfx": [{"storage_key": key, "at": 5.0}],
           "inserts": [{"asset_key": "local:/x/freeze_1.png"}]}
    loc = baseline._localise_inserts(edl, str(tmp_path))
    assert loc[key] == sound_library.path(sid)
    assert loc["local:/x/freeze_1.png"] == str(tmp_path / "freeze_1.png")
    with pytest.raises(RuntimeError):
        baseline._localise_inserts(
            {"sfx": [{"storage_key": "sfx/7/web-thing.flac", "at": 1}]},
            str(tmp_path))
