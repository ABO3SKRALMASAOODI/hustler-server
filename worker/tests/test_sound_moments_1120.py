"""Sound effects on the moments that matter, each one heard (lane sfx-moments, 20 Nov 2026).

Independent raters on the ten Cuban shorts: of a film's 13-21 sounds about 4 were audible ("most sit under speech"),
the hook, the turn and the payoff were dry in half the films, whooshes rode in-shot camera drifts, nothing sounded the
paper a picture showed. Screen placement (the workflow films only) now places fewer sounds and levels each on the
judge's own ruler (TEARDOWN 9: a 50 ms K-weighted peak against the voice's 400 ms level). Signals and the plan's own
curves only; the object a picture shows is named by a model (the workflow's picture check), never read from words."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from score import sound as SD  # noqa: E402
from score import sound_screen as SCR  # noqa: E402

AR, SR = SD.AR, SD.SR


def _voice(seconds=6.0, gap=None, seed=1):
    """A speech-like voice at AR: syllables of band noise at 4 a second, silent inside `gap` (t0, t1)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * AR)) / AR
    env = (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t)) ** 2
    x = rng.standard_normal(len(t))
    X = np.fft.rfft(x)
    hz = np.fft.rfftfreq(len(x), 1.0 / AR)
    X[(hz < 150) | (hz > 3500)] = 0
    v = np.fft.irfft(X, len(x)) * env
    v = 0.1 * v / (np.sqrt((v ** 2).mean()) + 1e-12)
    if gap:
        v[int(gap[0] * AR):int(gap[1] * AR)] = 0.0
    return v.astype(np.float32)


def _click(stereo=True, decorrelated=False, seed=2):
    """A 60 ms bright transient at the mix rate (a set recording as it plays)."""
    rng = np.random.default_rng(seed)
    n = int(0.06 * SR)
    env = np.exp(-np.arange(n) / (0.012 * SR))
    a = rng.standard_normal(n) * env * 0.5
    if not stereo:
        return a
    b = rng.standard_normal(n) * env * 0.5 if decorrelated else a
    return np.stack([a, b], axis=1)


def _item(tier="body", t=3.0, db=-20.0, fps=30.0):
    f = int(round(t * fps))
    return {"id": "snd.j0.0", "s": "c", "layer": "hit", "tier": tier, "for": "picture", "event_f": f,
            "start_s": f / fps - 0.013, "trim_s": 0.0, "db": db}


def test_a_hit_is_levelled_on_the_judges_ruler_and_heard():
    """Set at its tier's level over the voice's 400 ms level, raised only as far as being heard needs, never past its
    tier's ceiling: the raters' buried sounds sat 0.4-5 dB over their surroundings."""
    voice = _voice()
    smp = _click()
    for tier in ("hero", "accent", "body"):
        it = _item(tier, db=-40.0)
        p = SCR.level([it], voice, AR, lambda sid: smp, 30.0, srate=SR)
        assert p is not None and p >= SCR.HEARD_DB, (tier, p)
        assert it["heard_db"] == round(p, 1)
        assert SCR.LEVEL[tier] - 0.2 <= it["over_voice_db"] <= SCR.LEVEL_TOP[tier] + 0.2, (tier, it["over_voice_db"])


def test_a_hit_never_enters_the_speech_band_and_says_when_the_voice_still_hides_it():
    """The voice's own masking check holds it down; held down, it reads unheard, so the planner gives the moment to
    another recording or the next moment (fewer, heard), never a buried sound."""
    voice = _voice()
    smp = _click()
    free = _item("body", db=-40.0)
    held = _item("body", db=-40.0)
    p_free = SCR.level([free], voice, AR, lambda sid: smp, 30.0, srate=SR)
    p_held = SCR.level([held], voice, AR, lambda sid: smp, 30.0, srate=SR,
                       masking=lambda it: max(0.0, float(it["db"]) - (free["db"] - 14.0)))
    assert held["db"] <= free["db"] - 13.9
    assert p_held < p_free and p_held < SCR.HEARD_DB


def test_a_hit_in_a_breath_is_heard_at_its_level_without_a_lift():
    voice = _voice(gap=(2.7, 3.6))
    smp = _click()
    it = _item("accent", t=3.0, db=-40.0)
    p = SCR.level([it], voice, AR, lambda sid: smp, 30.0, srate=SR)
    assert p >= SCR.HEARD_DB and abs(it["over_voice_db"] - SCR.LEVEL["accent"]) <= 0.3


def test_a_wide_stereo_sweep_reads_as_it_plays():
    """The mean of two decorrelated channels reads 3 dB under either channel: the ruler sums channel powers, as
    loudness does, so a wide sweep is not set 3 dB louder than it plays."""
    wide = _click(decorrelated=True)
    p_ch = SCR.k_power(wide, SR)
    p_mean = SCR.k_power(wide.mean(axis=1), SR)
    assert np.allclose(p_ch, SCR.k_power(wide[:, 0], SR) / 2 + SCR.k_power(wide[:, 1], SR) / 2)
    assert 10 * np.log10(p_ch.max() / p_mean.max()) > 2.0


def test_the_ceiling_is_the_final_level_after_any_lift(monkeypatch):
    """score.audibility's broadband lift rides up to the voice's loudest syllables; the screen ceilings bring every
    hit back under LEVEL_TOP over the voice and HF_OVER over its top band."""
    voice = _voice()
    smp = _click()
    lib = {"c": {"id": "c", "file": "c.flac", "screen": {"hf_db": -30.0}}}
    it = _item("body", db=-40.0)
    SCR.level([it], voice, AR, lambda sid: smp, 30.0, srate=SR, lib=lib)
    lifted = dict(it, db=it["db"] + 12.0)
    out = SCR.restrain([lifted], voice, AR, 0.0, lib, fps=30.0, sample=lambda sid: smp, srate=SR)
    r = SCR.reading(out[0], smp, voice, AR, 30.0, srate=SR)
    over = float(SCR._db(r[2][r[3]].max())) + out[0]["db"] - r[5]
    assert over <= SCR.LEVEL_TOP["body"] + 0.1


def _plain_l1(n=12, fps=30, gap=45):
    """A podcast short's cutaways: plain cuts to still clips every 1.5 s, nothing travelling."""
    still = [[0.2, 0.0, 0.8, 1.0]] * gap
    items = [dict(id=f"s{i}", k="shot", pic=f"v{i:02d}", f=[i * gap, (i + 1) * gap], src=[0, gap / fps], crop=still) for i in range(n)]
    assets = {f"v{i:02d}": {"kind": "video", "path": f"v{i:02d}.mp4"} for i in range(n)}
    return dict(fps=fps, dur_f=n * gap, assets=assets, items=items)


def _ctx(l1, **kw):
    ctx = {"fps": 30, "dur_f": l1["dur_f"], "existing": [], "speech": None, "voice": None, "words": [(0.2, 15.5)], "bed": True,
           "np10": 6.0, "seed": SD.film_seed("moments"), "screen": True, "tick_10s": 1.5, "turn_f": 271, "close_s": 16.5}
    ctx.update(kw)
    return ctx


def test_the_hook_the_turn_and_the_close_land_even_on_plain_cuts():
    """The raters: no hit on the hook, the turn or the payoff in half the films; only 'motivated' events could carry
    them, and a podcast's cutaways are plain cuts. A new picture on the story's beat triggers its hit."""
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1)
    assert not any(SD.motivated(e) for e in ev)
    sounds, rep = SD.plan_sounds(ev, _ctx(l1))
    parts = {s.get("part") for s in sounds if s.get("layer") == "hit"}
    assert {"hook", "payoff", "close"} <= parts
    assert all(s.get("part") in ("hook", "payoff", "close") for s in sounds)       # nothing else: plain cuts stay quiet
    turn = next(s for s in sounds if s.get("part") == "payoff" and s.get("layer") == "hit")
    assert abs(turn["event_f"] - 271) <= SCR.SNAP_S * 30
    # a customer's film keeps its own placement: no hero lands on a plain cut there
    plain, _ = SD.plan_sounds(SD.l1_events(l1), dict(_ctx(l1), screen=False))
    assert not plain


def test_fewer_sounds_no_quiet_layer():
    """No tick layer and at most RATE_10S a 10 s: what is placed is meant to be heard."""
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1)
    ev += [{"f": 30 * k + 7, "kind": "display", "id": f"w{k}"} for k in range(1, 17, 1)]
    sounds, rep = SD.plan_sounds(ev, _ctx(l1))
    assert rep["allow_ticks"] == 0 and not rep.get("ticks")
    assert not any(s.get("tier") == "tick" for s in sounds)
    dur = l1["dur_f"] / 30
    gestures = {s["id"].rsplit(".", 1)[0] for s in sounds}
    assert len(gestures) <= int(SCR.RATE_10S * dur / 10) + 3          # the three heroes always land
    old, rep_old = SD.plan_sounds(ev, dict(_ctx(l1), screen=False))
    assert rep_old["allow_ticks"] > 0


def test_an_in_shot_drift_never_sweeps_a_whip_still_does():
    drift = {"kind": "move", "f": 300, "travel_s": 0.6, "speed": 0.3}
    whip = {"kind": "move", "f": 300, "travel_s": 0.15, "speed": 0.9}
    assert not SCR.whip_move(drift) and SCR.whip_move(whip)
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1) + [dict(drift, id="s6", f=200), dict(whip, id="s8", f=380)]
    sounds, rep = SD.plan_sounds(sorted(ev, key=lambda e: e["f"]), _ctx(l1))
    frames = {s["event_f"] for s in sounds}
    assert 200 not in frames and rep["drifts"] == 1
    assert 380 in frames


def test_a_cut_whose_crop_only_drifts_is_a_plain_cut():
    slow = [[0.2 + 0.001 * i, 0.0, 0.8 + 0.001 * i, 1.0] for i in range(45)]
    l1 = _plain_l1()
    l1["items"][4]["crop"] = slow
    ev = {e["id"]: e for e in SCR.annotate(SD.l1_events(l1), l1)}
    assert not ev["s4"].get("travel_s") and not SD.motivated(ev["s4"])


def test_an_object_a_model_named_lands_with_its_own_sound():
    """The workflow's picture check names the object a picture shows (asset foley, a family of the set): its landing
    is a moment that sounds, as that object, and an accent of the film."""
    from tests.test_sound_screen import LIB, _st, _pre
    l1 = _plain_l1()
    l1["assets"]["v05"]["foley"] = "paper"
    ev = {e["id"]: e for e in SCR.annotate(SD.l1_events(l1), l1)}
    e = ev["s5"]
    assert e["foley"] == "paper" and "paper" in SCR.fits(e) and SD.motivated(e) and SCR.matters(e) >= 3.0
    got, cls = SCR.layers(dict(e, _w=0.5), "plain", "accent", 7.5, _st(), LIB, _pre)
    assert got and LIB[got[0][0]].get("family") == "paper"
    # the object answers only the picture that shows it
    other, _ = SCR.layers(dict(ev["s6"], _w=0.5), "plain", "accent", 9.0, _st(), LIB, _pre)
    assert not other or LIB[other[0][0]].get("family") != "paper"
    # a customer's events carry no foley: unchanged
    assert not any(x.get("foley") for x in SD.l1_events(l1))


def test_a_beat_the_director_held_lands_a_dry_hit_not_a_trailer_impact():
    """Raters, 20 Nov: 'a trailer-style impact on a suicide line is tonally wrong'. The Director (a model) gives each
    story beat its energy; a hero landing on a beat it held is a short hit at an accent's level, no climb."""
    from tests.test_sound_screen import LIB, _st, _pre
    soft, cls = SCR.layers({"kind": "picture", "f": 45, "room_s": 3.0, "soft": True, "_w": 0.5}, "hook", "hero", 1.5, _st(), LIB, _pre)
    assert cls == "hit" and [w for _s, w in soft] == ["hit"] and LIB[soft[0][0]]["reference"]["class"] == "hit"
    loud, cls2 = SCR.layers({"kind": "picture", "f": 45, "room_s": 3.0, "_w": 0.5}, "close", "hero", 1.5, _st(), LIB, _pre)
    assert cls2 == "impact"
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1)
    held, rep = SD.plan_sounds([dict(e) for e in ev], _ctx(l1, beats=[{"f": 45, "role": "hook", "energy": "hold"},
                                                                      {"f": 271, "role": "payoff", "energy": "strobe"}]))
    hook = next(s for s in held if s.get("part") == "hook" and s.get("layer") == "hit")
    turn = next(s for s in held if s.get("part") == "payoff" and s.get("layer") == "hit")
    assert hook.get("soft") and not turn.get("soft")
    row = next(r for r in rep["placed"] if r["part"] == "hook")
    assert row["class"] != "impact"
    # the ruler sets a held beat's hit at an accent's level
    voice = _voice()
    smp = _click()
    it = dict(_item("hero", db=-40.0), soft=True)
    SCR.level([it], voice, AR, lambda sid: smp, 30.0, srate=SR)
    assert it["over_voice_db"] <= SCR.LEVEL_TOP["accent"] + 0.2


# ----------------------------------------------------------------- round 2 (review of 20 Nov): never over the word,
# one landing for the turn and the drop, levels the film plays, no filler

def test_a_hero_is_raised_inside_its_masking_bound_never_over_the_word():
    """A hero still under HEARD_DB is raised toward its ceiling only while the voice's masking check (MASK_DB less
    HERO_MASK_DB for a hero) stays clear: unheard there, it stays under the word (plan_sounds then moves it)."""
    voice = _voice()
    smp = _click()
    free = _item("hero", db=-40.0)
    SCR.level([free], voice, AR, lambda sid: smp, 30.0, srate=SR)
    bound = free["db"] - 12.0
    held = _item("hero", db=-40.0)
    p = SCR.level([held], voice, AR, lambda sid: smp, 30.0, srate=SR, masking=lambda it: max(0.0, float(it["db"]) - bound))
    assert held["db"] <= bound + 0.05 and p < SCR.HEARD_DB


def test_the_signal_check_holds_a_hero_to_its_bound(monkeypatch):
    """No hero is exempt from the final masking check: its bound is the margin less HERO_MASK_DB, and it is eased by
    what it still needs, never set to zero need."""
    monkeypatch.setattr(SD, "_voice_levels", lambda ctx: (np.zeros(2000), None))
    monkeypatch.setattr(SD, "_masking", lambda it, vb, speech, fps: 9.0)
    it = {"id": "snd.j0.0", "s": "nope", "layer": "hit", "tier": "hero", "for": "picture", "event_f": 30, "start_s": 1.0, "db": -5.0}
    placed = [{"t": 1.0, "ev": {"kind": "picture", "f": 30}, "part": "hook", "tier": "hero", "items": [it], "ref": -20.0}]
    out, rep = SD._signal_check(placed, {"fps": 30.0, "speech": np.ones(2000, bool), "screen": True}, {"dropped": []})
    assert out and out[0]["db"] == round(-5.0 - (9.0 - SCR.HERO_MASK_DB), 1)


def _heard_by_frame(clear):
    """A stand-in for score.sound._heard: a hit is heard (9 dB) on the frames in `clear` (a breath, a syllable gap),
    held under the word everywhere else (2 dB)."""
    def heard(group, ctx, ref, under, structural, tier="body"):
        p = None
        for it in group:
            if it.get("layer") == "hit":
                it["heard_db"] = 9.0 if int(it["event_f"]) in clear else 2.0
                p = it["heard_db"] if p is None else min(p, it["heard_db"])
        return None if p is None or p >= SCR.HEARD_DB else "held under the word"
    return heard


def test_a_hero_the_word_keeps_unheard_moves_into_the_syllable_gap(monkeypatch):
    """The hook lands on a picture on the first word: inside its masking bound no recording is heard there. It is
    never lifted over the word; it moves a few frames into the gap after the syllable, where it is heard."""
    monkeypatch.setattr(SD, "_heard", _heard_by_frame({51, 52, 53}))
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1)
    sounds, rep = SD.plan_sounds(ev, _ctx(l1))
    hook = next(s for s in sounds if s.get("part") == "hook" and s.get("layer") == "hit")
    assert hook["event_f"] == 51 and hook["heard_db"] >= SCR.HEARD_DB
    mv = next(m for m in rep["moved"] if m["part"] == "hook")
    assert mv["from_f"] == 45 and mv["to_f"] == 51
    # nowhere near is clear: the hero stays on its landing, heard best of its tries, still under the word's bound
    monkeypatch.setattr(SD, "_heard", _heard_by_frame(set()))
    sounds2, rep2 = SD.plan_sounds(SCR.annotate(SD.l1_events(l1), l1), _ctx(l1))
    hook2 = next(s for s in sounds2 if s.get("part") == "hook" and s.get("layer") == "hit")
    assert hook2["event_f"] == 45 and not rep2.get("moved")


def test_the_turn_takes_the_landing_the_music_snaps_to_and_carries_the_drop():
    """One rule with music.arrange: the nearest landing within SNAP_S (0.3 s); none there, the nearest within the
    turn's sentence, never left dry. Its hit carries `drop`, which the music's drop lands on."""
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1)
    assert SCR.SNAP_S == SD.SNAP_S
    near, _ = SD.plan_sounds([dict(e) for e in ev], _ctx(l1, turn_f=274))
    turn = next(s for s in near if s.get("part") == "payoff" and s.get("layer") == "hit")
    assert turn["event_f"] == 270 and turn.get("drop")
    off, rep = SD.plan_sounds([dict(e) for e in ev], _ctx(l1, turn_f=292))       # 0.73 s from 270, 0.77 s from 315
    turn = next(s for s in off if s.get("part") == "payoff" and s.get("layer") == "hit")
    assert turn["event_f"] == 270 and turn.get("drop")
    assert sum(1 for s in off if s.get("drop")) == sum(1 for s in off if s.get("drop") and s["event_f"] == 270)


def test_the_close_is_found_among_every_landing_so_the_turn_never_falls_after_it():
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1)
    sounds, rep = SD.plan_sounds(ev, _ctx(l1, close_s=None, words=[(0.2, 17.95)], turn_f=480))
    parts = {s.get("part"): s["event_f"] for s in sounds if s.get("layer") == "hit"}
    assert "payoff" in parts or parts.get("close") == 495          # the turn lands, or it is the close (one landing)
    assert all(f <= parts["close"] for f in parts.values())


def test_the_music_drop_lands_on_the_sounds_turn_hit():
    l1 = dict(_plain_l1(), sound=[{"id": "snd.j1.0", "s": "x", "layer": "hit", "part": "payoff", "tier": "hero", "drop": True,
                                   "event_f": 292, "start_s": 9.7}])
    from score import music as MU
    marks = MU.marks_from_l1(l1, None, payoff_s=9.4)
    assert abs(marks["drop_landing"] - 292 / 30) < 1e-3
    assert "drop_landing" not in MU.marks_from_l1(dict(l1, sound=[dict(l1["sound"][0], drop=None)]), None, payoff_s=9.4)


def test_sounds_on_the_drop_find_the_turn_hit_by_its_part():
    from score import music as MU
    plan = {"drop_at": 14.2, "drop_on": "payoff", "turn_s": None, "payoff_s": 13.0}
    sounds = [{"id": "snd.j1.0", "event_f": 420, "start_s": 13.9, "drop": True, "layer": "hit"},
              {"id": "snd.j1.1", "event_f": 420, "start_s": 12.4, "layer": "lead"},
              {"id": "snd.j2.0", "event_f": 391, "start_s": 13.0, "layer": "hit"}]
    out = MU.sounds_on_the_drop(sounds, plan, 30.0)
    assert [s["event_f"] for s in out] == [426, 426, 391]
    assert abs(out[1]["start_s"] - 12.6) < 1e-6
    # the drop already on the hit: nothing moves
    assert MU.sounds_on_the_drop(sounds, dict(plan, drop_at=14.0), 30.0)[0]["event_f"] == 420


def test_restrain_writes_the_levels_the_film_plays():
    """rep['final_sounds'] records each item: after a lift and the ceilings, over_voice_db and heard_db are read again
    at the level that plays (they were left at the plan's)."""
    voice = _voice()
    smp = _click()
    lib = {"c": {"id": "c", "file": "c.flac"}}
    it = _item("accent", db=-40.0)
    SCR.level([it], voice, AR, lambda sid: smp, 30.0, srate=SR, lib=lib)
    stale = dict(it, db=it["db"] + 12.0, over_voice_db=9.5, heard_db=30.0)
    bed = 0.3 * _voice(seed=7)
    out = SCR.restrain([stale], voice, AR, 0.0, lib, fps=30.0, sample=lambda sid: smp, srate=SR, bed=bed)[0]
    r = SCR.reading(out, smp, voice + bed, AR, 30.0, voice=voice, srate=SR)
    assert out["over_voice_db"] == round(float(SCR._db(r[2][r[3]].max())) + out["db"] - r[5], 1)
    assert out["over_voice_db"] <= SCR.LEVEL_TOP["accent"] + 0.1
    assert out["heard_db"] == round(SCR.prominence(r, out["db"]), 1) == out["over_bed_db"]
    # a later lift that put it over a word comes down by what the voice's masking check asks
    lifted = dict(it, db=it["db"] + 2.0)
    got = SCR.restrain([lifted], voice, AR, 0.0, lib, fps=30.0, sample=lambda sid: smp, srate=SR,
                       masking=lambda x: max(0.0, float(x["db"]) - (it["db"] - 3.0)))[0]
    assert got["db"] <= it["db"] - 3.0 + 0.05


def test_display_pops_and_low_moments_are_the_musics():
    """Raters: 'ticks on display words', the same generic click on every display word. A display word sounds only
    when it is typed in; the rest pass takes only the moments that matter (an object, a glitch, typing, a hard
    landing, real travel, a whip)."""
    l1 = _plain_l1()
    ev = SCR.annotate(SD.l1_events(l1), l1)
    shown = [{"f": 30 * k + 7, "kind": "display", "id": f"w{k}"} for k in range(2, 16, 2)]
    sounds, rep = SD.plan_sounds(sorted(ev + shown, key=lambda e: e["f"]), _ctx(l1))
    assert rep["display_pops"] == len(shown)
    assert not any(s["event_f"] in {e["f"] for e in shown} for s in sounds)
    assert not SCR.worth_the_rest({"kind": "display"}) and not SCR.worth_the_rest({"kind": "card"})
    assert SCR.worth_the_rest({"kind": "display", "typed": True}) and SCR.worth_the_rest({"kind": "picture", "slam": True})
    assert SCR.worth_the_rest({"kind": "move", "speed": 0.9}) and SCR.worth_the_rest({"kind": "picture", "foley": "paper"})


def test_arrange_lands_the_drop_on_the_sounds_turn_hit(tmp_path_factory):
    """The drop takes the sound's own turn hit (marks drop_landing, within DROP_REACH_S of the mark) over the nearest
    cut, so the impact and the drop are one landing; a hit far from the mark, or one with no bar of build after the
    story's turn, leaves today's snap."""
    import shutil
    import pytest
    from score import music as MU
    if shutil.which(os.environ.get("FFMPEG_BIN", "ffmpeg")) is None:
        pytest.skip("needs ffmpeg")
    from tests.test_music_turn_drop import _marks
    from tests.test_music_film import _wav
    song = MU.analyse(_wav(tmp_path_factory.mktemp("drop"), "full", bars=40, intro_bars=8), sung=False)
    base = MU.arrange(song, _marks(32.0, 8.0, 16.0, turn_drop=True))
    plan = MU.arrange(song, _marks(32.0, 8.0, 16.0, turn_drop=True, drop_landing=16.6))
    assert plan["ok"] and plan["drop_on"] == "payoff" and plan["drop_at"] == 16.6 and base["drop_at"] != 16.6
    far = MU.arrange(song, _marks(32.0, 8.0, 16.0, turn_drop=True, drop_landing=16.0 + MU.DROP_REACH_S + 0.5))
    assert far["drop_at"] == base["drop_at"]
    no_build = MU.arrange(song, _marks(32.0, 15.5, 16.0, turn_drop=True, drop_landing=15.6))
    assert no_build["drop_at"] != 15.6
