"""SND3 (14 Nov): the sound design shaped like the references. One level hierarchy fitted to their spread, density
by section (a dense opening, a breathing middle, a build into the payoff), type, graphics and moves sounded before
cuts, no recording twice in a film and films that rotate the library, and music and sounds as one mix (the song
dipped under a hero hit, its own landing eased only when it would cover the hit, a hit the song still covers
taken out).

Signal only: the owner's library, synthetic voices and songs, no model, no network."""
import os
import shutil
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from scene import audio as AU  # noqa: E402
from score import sound as SD  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which(FF) is None, reason="needs ffmpeg")


def _film(dur=50.0, fps=30, seed=0):
    """A story film's events: picture cuts every ~0.9 s of mixed size, display type, cards, a counter and camera moves."""
    rng = np.random.default_rng(seed)
    ev, f = [], 20
    while f < dur * fps - 40:
        ev.append({"f": f, "kind": "picture", "size": float(rng.choice([0.25, 0.45, 0.7, 0.95]))})
        f += int(rng.integers(20, 36))
    for t in (0.4, 2.2, 9.5, 14.2, 21.0, 26.4, 33.3, 38.8, 44.1):
        ev.append({"f": int(t * fps) + 7, "kind": "display", "size": float(rng.choice([0.4, 0.7, 1.0]))})
    for t in (12.0, 30.2):
        ev.append({"f": int(t * fps) + 11, "kind": "card", "photo": True, "area": 0.3})
    ev.append({"f": int(18.0 * fps) + 5, "kind": "counter", "steps": [int(18.0 * fps) + 5 + 4 * k for k in range(6)]})
    for t in (6.4, 17.1, 24.6, 41.7):
        ev.append({"f": int(t * fps) + 13, "kind": "move"})
    ev.sort(key=lambda e: e["f"])
    out = []
    for e in ev:                                                          # one event a frame neighbourhood, as l1_events gives them
        if not out or e["f"] - out[-1]["f"] > 2:
            out.append(e)
    return out


def _plan(name="a", dur=50.0, fps=30, turn_s=31.0, seed=0):
    D = int(dur * fps)
    ctx = {"fps": fps, "dur_f": D, "existing": [], "speech": None, "voice": None, "words": [(0.2, dur - 2.5)], "bed": True,
           "np10": 11.0, "turn_f": int(turn_s * fps), "seed": SD.film_seed(name)}
    return SD.plan_sounds(_film(dur, fps, seed), ctx)


def _level(s):
    lib = SD.library()
    return float(s["db"]) + float(lib[s["s"]].get("hit_lufs", -15.0)) - SD.VOICE_LUFS


@needs_ffmpeg
def test_levels_follow_one_hierarchy_spread_like_the_references():
    """Hero hits at the voice, accents a few dB under, the body under that, ticks far under; the planned levels spread
    5.5 dB or more from their 25th to 75th percentile (the references 5.5-14; SND2 1.0-2.5)."""
    sounds, rep = _plan()
    by = {}
    for s in sounds:
        if s.get("layer") == "hit":
            by.setdefault(s["tier"], []).append(_level(s))
    med = {k: float(np.median(v)) for k, v in by.items()}
    assert {"hero", "accent", "tick"} <= set(med), med
    assert med["hero"] > med["accent"] > med["tick"] + 4.0, med
    if "body" in med:
        assert med["accent"] > med["body"] > med["tick"], med
    assert rep["shape"]["level_iqr_db"] >= 5.5, rep["shape"]


@needs_ffmpeg
def test_the_opening_is_dense_the_middle_breathes_and_the_turn_is_built_into():
    sounds, rep = _plan()
    sh = rep["shape"]
    first = {}
    for s in sounds:                                                      # one gesture once: a counter's roll or a train is one
        if s.get("layer") == "hit":
            g = s["id"].rsplit(".", 1)[0]
            first[g] = min(first.get(g, 1e9), s["event_f"] / 30.0)
    t = sorted(first.values())
    rate = lambda a, b: sum(1 for x in t if a <= x < b) / (b - a)
    assert sh["first_5s"] >= 5, sh                                        # the owner's 8 reels: a median of 6 in the first 5 s
    assert rate(0, sh["open_s"]) > 1.3 * rate(sh["open_s"], 20.0), (t, sh)
    build0 = sh["turn_s"] - min(SD.BUILD_S, 0.2 * 50.0)
    assert rate(build0, sh["turn_s"] + 0.05) >= rate(sh["open_s"], build0), (t, sh)
    hook = [s for s in sounds if s["part"] == "hook"]
    assert [s["layer"] for s in hook] == ["lead", "hit"]                  # a sweep flies into the first hit
    assert SD.class_of(hook[0]["s"]) in ("whoosh", "swish") or SD.measured_kind(hook[0]["s"]) == "whoosh"
    assert not [s for s in sounds if s["event_f"] / 30.0 > sh["close_s"] + 0.05]     # nothing after the close


@needs_ffmpeg
def test_type_graphics_and_moves_are_sounded_before_cuts():
    sounds, rep = _plan()
    sh = rep["shape"]
    assert sh["on_cut_share"] <= 0.5, sh                                  # the references 15-43%; SND2 64-83%
    later = [s for s in sounds if s["event_f"] / 30 >= sh["open_s"] and s.get("tier") != "hero"]
    cut_later = sum(1 for s in later if s["for"] in ("picture", "cut", "burst", "page")) / max(1, len(later))
    assert cut_later <= SD.CUT_SHARE + 0.06, (cut_later, sh)                # past the opening's cluster, the budget holds
    fors = {s["for"] for s in sounds}
    assert {"display", "move"} <= fors and ("card" in fors or "counter" in fors), fors
    # a film with nothing but cuts still carries its amount: the cuts fill what the graphics cannot
    fps, D = 30, 1500
    ev = [{"f": f, "kind": "picture", "size": 0.4 + 0.5 * ((f // 27) % 2)} for f in range(20, D - 40, 27)]
    s2, r2 = SD.plan_sounds(ev, {"fps": fps, "dur_f": D, "existing": [], "speech": None, "voice": None, "words": [], "bed": True, "np10": 11.0})
    assert len(r2["placed"]) + len(r2["body"]) >= 0.8 * r2["allow"], r2["shape"]


@needs_ffmpeg
def test_no_recording_twice_in_a_film_and_two_films_rotate_the_library():
    a, ra = _plan("fedex", seed=0)
    b, rb = _plan("uber", seed=0)                                         # the same pictures, another film
    for sounds, rep in ((a, ra), (b, rb)):
        assert rep["shape"]["repeats"] == []
        gest = {}
        for s in sounds:
            if s["for"] != "burst" and s["for"] != "counter":
                gest.setdefault(s["s"], set()).add(s["id"].rsplit(".", 1)[0])
        assert all(len(g) == 1 for g in gest.values()), gest
    for keep in (lambda s: s["tier"] != "tick",):                          # the audible layer (the library's short clicks are few)
        ra_ids = {s["s"] for s in a if keep(s)}
        rb_ids = {s["s"] for s in b if keep(s)}
        assert len(ra_ids & rb_ids) <= 0.65 * min(len(ra_ids), len(rb_ids)), (len(ra_ids & rb_ids), len(ra_ids), len(rb_ids))


@needs_ffmpeg
def test_an_accent_is_never_a_tick_and_the_body_never_an_impact():
    sounds, rep = _plan()
    for s in sounds:
        if s.get("layer") != "hit":
            continue
        k = SD.measured_kind(s["s"]) or SD.class_of(s["s"])
        if s["tier"] == "accent":
            assert k != "click", s
        if s["tier"] in ("body", "tick"):
            assert k != "impact", s


def _song(n, level=0.1, seed=4):
    return (np.random.default_rng(seed).standard_normal((n, 2)) * level).astype(np.float32)


def test_the_song_dips_under_a_hero_hit_and_its_own_landing_only_when_it_covers_the_hit():
    SR = AU.SR
    n = 4 * SR
    at = 2 * SR
    own = np.zeros((n, 2), np.float32)
    own[at:at + int(0.3 * SR)] = 0.2                                      # a hero hit
    bed = _song(n)
    g, rep = AU.duck_curve(bed, own, [(at, 8.0)], n)
    assert rep[0]["db"] >= AU.DUCK_MIN_DB and float(g[at + int(0.05 * SR)]) < 0.8
    assert abs(float(g[0]) - 1.0) < 1e-6 and abs(float(g[-1]) - 1.0) < 1e-6            # the song comes back
    hole = _song(n, 0.3)
    hole[at - int(0.3 * SR):at] *= 0.01                                   # the song's own hole, then its landing, louder than the hit
    g2, rep2 = AU.duck_curve(hole, own, [(at, 8.0)], n)
    assert 0 < rep2[0]["db"] <= AU.DUCK_LANDING_DB, rep2
    quiet = _song(n, 0.02)
    quiet[at - int(0.3 * SR):at] *= 0.01                                  # a landing well under the hit: left alone
    g3, rep3 = AU.duck_curve(quiet, own, [(at, 8.0)], n)
    assert rep3[0]["db"] == 0.0 and float(g3.min()) == 1.0


@needs_ffmpeg
def test_a_quiet_hit_the_song_still_covers_is_taken_out_and_a_hero_is_kept():
    fps, dur = 30, 6.0
    n = int(dur * SD.AR)
    voice = np.zeros(n, np.float32)
    loud = np.random.default_rng(3).standard_normal(n).astype(np.float32) * 0.5
    tick = dict(SD._item("x.0", "click_2", 120, fps, -30.0, {"kind": "display"}, "plain", "hit"), tier="tick")
    hero = dict(SD._item("y.0", "click_2", 120, fps, -30.0, {"kind": "display"}, "hook", "hit"), tier="hero")
    assert SD.present_over([tick], voice, loud, fps) == []
    assert len(SD.present_over([hero], voice, loud, fps)) == 1
    quiet = np.random.default_rng(3).standard_normal(n).astype(np.float32) * 1e-5
    assert len(SD.present_over([dict(tick, db=-6.0)], voice, quiet, fps)) == 1


@needs_ffmpeg
def test_master_dips_a_song_laid_on_the_sounds_track(tmp_path):
    """The production seam: music.py lays the song onto split_audio's own track; master() finds it and dips it."""
    SR = AU.SR
    n = 3 * SR
    own = np.zeros((n, 2), np.float32)
    own[SR:SR + int(0.2 * SR)] = 0.2
    fx = own.copy().view(AU.Sounds)
    fx.own, fx.ducks = own.copy(), [(SR, 8.0)]
    fx[:n] += _song(n, 0.1)
    rep = AU.master(None, fx, str(tmp_path / "m.m4a"), str(tmp_path), 3.0)
    assert rep.get("ducks") and rep["ducks"][0]["db"] >= AU.DUCK_MIN_DB


def test_two_films_draw_the_library_independently():
    """The rotation's draws are a real hash of the film and the recording: CRC32 is linear, and two films' draws for
    recordings of one name length moved together (both 14 Nov proof films closed on impact_10 and riser_11)."""
    a, b = SD.film_seed("fedex", "film.mp4"), SD.film_seed("wf1_uber", "film.mp4")
    ids = sorted(SD.library())
    ua, ub = np.array([SD._u(a, s) for s in ids]), np.array([SD._u(b, s) for s in ids])
    assert abs(float(np.corrcoef(ua, ub)[0, 1])) < 0.35
    assert 0.0 <= ua.min() and ua.max() < 1.0
    seeds = [SD.film_seed(f"film{k}") for k in range(40)]
    lows = [min(ids, key=lambda s: SD._u(x, s)) for x in seeds]
    assert len(set(lows)) >= 20                                           # forty films do not all favour one recording


@needs_ffmpeg
def test_a_hero_hit_with_its_own_fly_in_takes_no_lead_and_a_lift_stays_under_the_tier_above():
    assert SD.preroll("impact_7") >= SD.PREROLL_S > SD.preroll("impact_10")       # impact_7 is cut from a trailer whoosh-in
    sounds, rep = _plan("b")
    lib = SD.library()
    for gid in {s["id"].rsplit(".", 1)[0] for s in sounds if s.get("tier") == "hero"}:
        g = [s for s in sounds if s["id"].rsplit(".", 1)[0] == gid]
        if any(s.get("layer") == "lead" for s in g):
            assert all(SD.preroll(s["s"]) < SD.PREROLL_S for s in g if s.get("layer") == "hit"), g
    for s in sounds:
        if s.get("tier") in ("accent", "body", "tick") and s.get("layer") == "hit":
            assert _level(s) <= SD.TIER_CEIL[s["tier"]] + 0.06, (s["s"], s["tier"], _level(s))
    assert lib["impact_10"].get("hit_lufs") is not None
