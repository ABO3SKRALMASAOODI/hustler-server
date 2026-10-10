"""Sound effects levelled against the voice, and placed where they belong.

Independent judges (Oct 10 2026) measured the showcase shorts: one absolute
gain per sound put a notification ding ~9 dB over the voice on the payoff
word '140' (it masked the word) and left a swish 12 dB under it, inaudible;
a shutter on the spoken word 'pictures' was a literal pun; the opening
whoosh on every short had nothing on screen to land on. The owner's rule:
podcast shorts default to ZERO sounds, at most 1-2, each with a visual
partner and a structural, non-pun reason.

sfx_mix sets a library recording's gain from the measured voice at its hit
(role targets: whoosh/swish ~8 dB under, tonal ~10 under, impacts louder only
below 150 Hz) — explicit gains still win — and sfx_placement flags (or, for
a bright sound on a payoff onset, nudges) the placements the judges named.
The approved recordings themselves are never altered.

Run:  python -m pytest tests/test_sfx_mix.py -q     (from worker/)
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_prompt  # noqa: E402
import agent_tools  # noqa: E402
import db as dbx  # noqa: E402
import dialogue_level  # noqa: E402
import motion_tools  # noqa: E402
import sfx_mix  # noqa: E402
import sfx_placement  # noqa: E402
import sound_library  # noqa: E402
import taste  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
GRID = 0.006


# ── the recordings' measured hit loudness ──────────────────────────────

def test_every_recording_carries_its_measured_hit_loudness():
    for r in sound_library.catalog():
        k, hp = sfx_mix.hit_level(r["id"])
        assert k is not None and hp is not None, r["id"]
        # the part above 150 Hz is never louder than the whole hit
        assert hp <= k + 0.15, r["id"]
    # the spread that made one suggested gain per file impossible
    assert sfx_mix.hit_level("ding_1")[0] - sfx_mix.hit_level("click_1")[0] > 15
    # impact and heartbeat carry their weight below 150 Hz
    for sid in ("impact_1", "heartbeat_1"):
        k, hp = sfx_mix.hit_level(sid)
        assert k - hp > 4, sid


@needs_ffmpeg
def test_hit_loudness_is_measured_from_the_untouched_files():
    for r in sound_library.catalog():
        k, hp = sfx_mix.measure_recording(sound_library.path(r["id"]), FFMPEG)
        assert abs(k - r["hit_lufs"]) <= 0.3, (r["id"], k, r["hit_lufs"])
        assert abs(hp - r["hit_lufs_hp150"]) <= 0.3, (r["id"], hp)


# ── role targets ───────────────────────────────────────────────────────

@pytest.mark.parametrize("voice", [-20.0, -26.5, -14.0])
def test_auto_gain_puts_every_recording_at_its_role_level(voice):
    for r in sound_library.catalog():
        sid = r["id"]
        g = sfx_mix.auto_gain(sid, voice)
        assert g is not None and g == round(g * 2) / 2, sid      # 0.5 dB grid
        k, hp = sfx_mix.hit_level(sid)
        sp = sfx_mix.spec(sid)
        if sp.get("band") == "hp150":
            # louder only below 150 Hz: the content above sits at the
            # target or under it, the whole hit never past the cap
            assert hp + g - voice <= sp["target"] + 0.26, sid
            assert k + g - voice <= sp["cap"] + 0.26, sid
        elif g < sfx_mix.AUTO_GAIN_MAX_DB:
            assert abs(k + g - voice - sp["target"]) <= 0.26, sid
        assert sfx_mix.judge_level(sid, g, voice)[0] is None, sid


def test_role_targets_follow_the_judges_brief():
    assert sfx_mix.ROLE_MIX["whoosh"]["target"] == sfx_mix.ROLE_MIX["swish"]["target"] == -8.0
    assert -10.0 <= sfx_mix.ROLE_MIX["whoosh"]["target"] <= -6.0
    for role in ("ding", "pop", "tick", "click", "shutter"):
        assert sfx_mix.ROLE_MIX[role]["target"] == -10.0, role
    assert sfx_mix.ROLE_MIX["impact"]["band"] == "hp150"


def test_the_judged_showcase_levels_are_named():
    # Thiel: ding_1 at -14 dB on '140' — level with the voice: too hot
    kind, why = sfx_mix.judge_level("ding_1", -14.0, -20.0)
    assert kind == "too_hot" and "competes with the words" in why
    # Elon: swish_1 at -16 dB, 13 dB under the voice: inaudible
    kind, why = sfx_mix.judge_level("swish_1", -16.0, -20.0)
    assert kind == "inaudible" and "will not be heard" in why
    # Jobs: shutter_1 at -14 dB was judged "audible as a tick": in range
    assert sfx_mix.judge_level("shutter_1", -14.0, -20.0)[0] is None
    # Jobs: impact_1 at -14 dB was "trailer-heavy, lower it 6 dB"
    assert sfx_mix.judge_level("impact_1", -14.0, -20.0)[0] == "too_hot"
    assert sfx_mix.auto_gain("impact_1", -20.0) <= -19.0
    # and the automatic levels move each the way the judges asked
    assert sfx_mix.auto_gain("ding_1", -20.0) <= -23.5
    assert sfx_mix.auto_gain("swish_1", -20.0) >= -11.5


def test_a_template_offset_is_kept_but_bounded():
    v = sfx_mix.nominal("test")
    base = sfx_mix.cue_gain("pop_1", v, None)
    assert sfx_mix.cue_gain("pop_1", v, -12.0) == base             # manifest gain: no offset
    assert sfx_mix.cue_gain("pop_1", v, -16.0) == base - 4.0       # a quieter second pop
    assert sfx_mix.cue_gain("pop_1", v, -2.0) == base + 2.0        # louder: at most +2
    assert sfx_mix.cue_gain("pop_1", v, -40.0) == base - 6.0       # quieter: at most -6


# ── the voice at a hit ─────────────────────────────────────────────────

def _speechlike(path, segments):
    """A stereo wav of syllable-gated pink noise: [(seconds, dB)] with
    dB=None for silence."""
    parts, filt = [], []
    for k, (dur, db) in enumerate(segments):
        if db is None:
            parts += ["-f", "lavfi", "-t", f"{dur}", "-i",
                      "anullsrc=r=48000:cl=stereo"]
            filt.append(f"[{k}:a]anull[s{k}]")
        else:
            parts += ["-f", "lavfi", "-t", f"{dur}", "-i",
                      "anoisesrc=r=48000:color=pink:amplitude=0.25:seed=7"]
            filt.append(f"[{k}:a]aformat=channel_layouts=stereo,"
                        f"volume='if(lt(mod(t,0.5),0.32),1,0.03)':eval=frame,"
                        f"volume={db}dB[s{k}]")
    graph = (";".join(filt) + ";" + "".join(f"[s{k}]" for k in range(len(segments)))
             + f"concat=n={len(segments)}:v=0:a=1[o]")
    subprocess.run([FFMPEG, "-y", "-v", "error", *parts, "-filter_complex", graph,
                    "-map", "[o]", "-ar", "48000", path], check=True)
    return path


def _edl(keep, **extra):
    edl = {"keep": keep, "frame": {"ratio": "9:16", "mode": "crop"}}
    edl.update(extra)
    return validate_edl(edl, 60.0).model_dump(exclude_none=True)


@needs_ffmpeg
def test_voice_level_is_measured_where_the_sound_hits(tmp_path):
    src = _speechlike(str(tmp_path / "s.wav"),
                      [(10.0, 0.0), (5.0, None), (10.0, -10.0), (5.0, None)])
    edl = _edl([[0.0, 8.0], [16.0, 24.0]])
    v = sfx_mix.voice_levels(src, edl, [4.0, 12.0], mastered=False)
    loud, quiet = v[4.0], v[12.0]          # program 12.0 is source 20.0
    assert loud["measured"] and quiet["measured"], v
    assert abs((loud["lufs"] - quiet["lufs"]) - 10.0) <= 1.0, v
    # the leveller moves both to the leveled dialogue level, keeping 20% of
    # the local difference (the renderer's dialogue_level does the same)
    lv = sfx_mix.voice_levels(src, edl, [4.0, 12.0], mastered=True)
    for h in (4.0, 12.0):
        assert lv[h]["leveled"]
        assert abs(lv[h]["lufs"] - dialogue_level.TARGET_LUFS) <= 2.0, lv
    # user volume automation at the hit is part of the voice there
    boosted = _edl([[0.0, 8.0], [16.0, 24.0]],
                   volume=[{"start": 16.0, "end": 24.0, "gain_db": 4.0}])
    vb = sfx_mix.voice_levels(src, boosted, [12.0], mastered=False)[12.0]
    assert abs(vb["lufs"] - quiet["lufs"] - 4.0) <= 0.2


@needs_ffmpeg
def test_no_speech_near_the_hit_falls_back_to_the_nominal_voice(tmp_path):
    src = _speechlike(str(tmp_path / "s.wav"), [(4.0, -6.0), (10.0, None)])
    edl = _edl([[0.0, 14.0]])
    v = sfx_mix.voice_levels(src, edl, [10.0], mastered=True)[10.0]
    assert not v["measured"] and v["lufs"] == sfx_mix.NOMINAL_VOICE_LUFS
    assert "no speech" in v["why"]
    # unreadable media never raises
    v = sfx_mix.voice_levels(str(tmp_path / "missing.wav"), edl, [2.0])[2.0]
    assert not v["measured"] and v["lufs"] == sfx_mix.NOMINAL_VOICE_LUFS


# ── add_sfx levels and reports ─────────────────────────────────────────

class _Db:
    def run(self, fn, *a, **k):
        if fn is dbx.asset_by_key and a[1] == "music/7/boom.wav":
            return {"kind": "music", "storage_key": a[1], "duration_s": 2.0,
                    "meta": {"filename": "boom.wav"}}
        return None


class _Ctx:
    project_id = 7
    edit_plan = None
    has_main_video = True

    def __init__(self, edl=None, words=(), media=None, duration=60.0):
        self.duration = duration
        self._edl = validate_edl(edl or default_edl(duration), duration).model_dump()
        self.writes = []
        self.db = _Db()
        self.media = media
        self.index = {"video": {"width": 1080, "height": 1920},
                      "words": [dict(w) for w in words]}

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": json.loads(json.dumps(self._edl))}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"

    def proxy_path(self):
        if not self.media:
            raise RuntimeError("no proxy available")
        return self.media

    def sfx(self, sid):
        return next(s for s in self._edl["sfx"] if s["id"] == sid)


@pytest.fixture
def lib(monkeypatch):
    monkeypatch.setattr(motion_tools, "ensure_library_asset",
                        lambda ctx, sid: sound_library.asset_key(ctx.project_id, sid))


def test_add_sfx_gain_is_optional_and_explicit_gain_wins():
    import inspect
    assert inspect.signature(agent_tools.add_sfx).parameters["gain_db"].default is None


@needs_ffmpeg
def test_add_sfx_levels_a_library_sound_against_the_measured_voice(lib, tmp_path):
    src = _speechlike(str(tmp_path / "s.wav"), [(30.0, -8.0)])
    ctx = _Ctx(_edl([[0.0, 30.0]]), media=src)
    out = agent_tools.add_sfx(ctx, "sound:ding_1", at=12.0)
    assert out.startswith("EDL v1"), out
    voice = sfx_mix.voice_levels(src, ctx._edl, [12.0])[12.0]
    assert voice["measured"]
    want = sfx_mix.auto_gain("ding_1", voice["lufs"])
    assert ctx.sfx("sx1")["gain_db"] == want
    assert "MIX: ding_1 at" in out and "(levelled automatically)" in out
    assert "sits 10 dB under the voice" in out and "after the dialogue leveller" in out
    # an explicit gain is kept, and the report says it is too hot
    out = agent_tools.add_sfx(ctx, "sound:ding_1", at=20.0, gain_db=-6)
    assert ctx.sfx("sx2")["gain_db"] == -6.0
    assert "(the gain set)" in out and "CHECK: " in out and "over the voice" in out
    assert "its role level here is" in out


def test_without_media_the_nominal_voice_sets_the_level(lib):
    ctx = _Ctx(_edl([[0.0, 30.0]]))
    out = agent_tools.add_sfx(ctx, "sound:swish_1", at=12.0)
    assert ctx.sfx("sx1")["gain_db"] == sfx_mix.auto_gain(
        "swish_1", sfx_mix.NOMINAL_VOICE_LUFS)
    assert "voice not measured here" in out
    # a sound that is not a library recording keeps the old default
    out = agent_tools.add_sfx(ctx, "music/7/boom.wav", at=5.0)
    assert ctx.sfx("sx2")["gain_db"] == agent_tools.SFX_DEFAULT_GAIN_DB == -6.0
    assert "MIX:" not in out


def test_template_cues_and_set_audio_gain_report_levels(lib, monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 4, "samples": 4,
                         "bboxes": [[.1, .3, .9, .5]]})
    ctx = _Ctx(_edl([[0.0, 30.0]]))
    out = motion_tools.add_motion_graphic(ctx, "counter", 10.0, 13.0, id="num",
                                          params={"value": "140"}, sfx=True)
    assert out.startswith("EDL v1"), out
    owned = [s for s in ctx._edl["sfx"] if s["id"].startswith("mg_num_sfx")]
    assert owned and "levelled against the voice" in out
    for s in owned:
        sid = sound_library.id_for_key(s["storage_key"])
        assert sfx_mix.judge_level(sid, s["gain_db"], sfx_mix.NOMINAL_VOICE_LUFS)[0] is None
    out = agent_tools.set_audio_gain(ctx, "sfx", owned[0]["id"], -40)
    assert "MIX:" in out and "CHECK:" in out and "will not be heard" in out


# ── placement: partner, payoff word, pun, opening, budget ─────────────

def _words(pairs):
    return [{"w": w, "t0": t0, "t1": t1} for w, t0, t1 in pairs]


THIEL_WORDS = _words([("flying", 30.40, 30.70), ("cars", 30.70, 31.00),
                      ("and", 31.00, 31.10), ("we", 31.10, 31.20),
                      ("got", 31.20, 31.40), ("140", 31.46, 31.95),
                      ("characters", 31.95, 32.40)]
                     + [(f"w{i}", 1.0 + i * 0.5, 1.3 + i * 0.5) for i in range(40)])


def _thiel_edl(sfx):
    return _edl([[0.0, 36.0]], motion=[
        {"id": "hook", "template": "word_slam", "start": 0.0, "end": 2.3,
         "params": {"text": "where did *progress* go?", "entrance": "pop"}},
        {"id": "num", "template": "counter", "start": 30.46, "end": 32.84,
         "params": {"value": "140"}}], sfx=sfx)


def _lib(sid, hit, **kw):
    pl = sound_library.place(sid, hit)
    item = {"id": kw.pop("id", sid), "storage_key": sound_library.asset_key(7, sid),
            "at": pl["at"],
            "gain_db": sfx_mix.auto_gain(sid, sfx_mix.NOMINAL_VOICE_LUFS), **kw}
    if pl["offset_s"]:
        item["offset_s"] = pl["offset_s"]
    return item


def _codes(edl, words):
    got = sfx_placement.check_edl(edl, {"words": words})
    return {k: {f["code"] for f in v} for k, v in got["items"].items()}, got


def test_the_judged_showcase_placements_are_flagged():
    edl = _thiel_edl([_lib("whoosh_soft_1", 0.25, id="s1"),
                      _lib("ding_1", 31.46, id="s3")])
    codes, got = _codes(edl, THIEL_WORDS)
    # the opening whoosh: nothing lands at 0.25 s (the hook popped on at 0)
    assert {"no_visual_partner", "opening_whoosh"} <= codes["s1"]
    # the ding on the spoken '140' the counter shows: the payoff onset
    assert codes["s3"] == {"on_payoff_word"}
    msg = next(f["message"] for f in got["items"]["s3"])
    assert "'140'" in msg and "counter graphic num shows it" in msg


def test_a_shutter_on_the_word_pictures_is_a_pun_unless_a_photo_is_shown():
    words = _words([("deal", 31.0, 31.3), ("in", 31.3, 31.45),
                    ("pictures.", 31.64, 32.1)]
                   + [(f"w{i}", 1.0 + i * 0.5, 1.3 + i * 0.5) for i in range(40)])
    slam = {"id": "pics", "template": "word_slam", "start": 31.64, "end": 32.34,
            "params": {"text": "*pictures*", "entrance": "pop"}}
    edl = _edl([[0.0, 40.0]], motion=[slam], sfx=[_lib("shutter_1", 31.64, id="s2")])
    codes, got = _codes(edl, words)
    assert "literal_pun" in codes["s2"] and "on_payoff_word" in codes["s2"]
    assert "no_visual_partner" not in codes["s2"]           # the slam lands there
    # the same shutter while a photo card is on screen is a real action
    cards = dict(slam, id="photo", template="photo_stack",
                 params={"images": [{"src": "x.jpg"}]})
    edl = _edl([[0.0, 40.0]], motion=[cards],
               sfx=[_lib("shutter_1", 31.64, id="s2")])
    codes, _ = _codes(edl, words)
    assert "literal_pun" not in codes.get("s2", set())


def test_partners_landings_and_sweeps():
    # the swish on the 1960s-vs-TODAY split: its partner is the split's slide
    edl = _edl([[0.0, 40.0]], motion=[
        {"id": "vs", "template": "versus_split", "start": 11.4, "end": 14.75,
         "params": {"left": "1960s", "right": "TODAY"}}],
        sfx=[_lib("swish_1", 11.6, id="s2")])
    codes, _ = _codes(edl, [])
    assert "s2" not in codes
    # an impact on a phrase_build row reveal has its partner
    edl = _edl([[0.0, 40.0]], motion=[
        {"id": "paper", "template": "phrase_build", "start": 32.34, "end": 37.84,
         "params": {"rows": [{"text": "no college student", "at": "0.7"},
                             {"text": "WITHOUT ONE", "at": "3.9"}]}}],
        sfx=[_lib("impact_1", 36.24, id="s3")])
    codes, _ = _codes(edl, [])
    assert "s3" not in codes
    # an ordinary cut inside one shot is not a partner (never a sound on
    # ordinary cuts); a cut to another shot is
    edl = _edl([[0.0, 10.0], [12.0, 20.0]], sfx=[_lib("pop_1", 10.0, id="p")])
    assert "no_visual_partner" in _codes(edl, [])[0]["p"]
    shots = {"shots": [{"id": 1, "start": 0.0, "end": 11.0},
                       {"id": 2, "start": 11.0, "end": 30.0}], "words": []}
    got = sfx_placement.check_edl(edl, shots)
    assert "p" not in got["items"]


def test_a_talking_short_carries_one_or_two_sounds():
    words = [(f"w{i}", 1.0 + i * 0.4, 1.3 + i * 0.4) for i in range(60)]
    three = [_lib("swish_1", 5.0, id="a"), _lib("pop_1", 12.0, id="b"),
             _lib("impact_1", 20.0, id="c")]
    got = sfx_placement.check_edl(_edl([[0.0, 30.0]], sfx=three),
                                  {"words": _words(words)})
    assert got["talking"] and got["budget"]["count"] == 3
    assert "default to zero and carry at most 1-2" in got["budget"]["message"]
    got = sfx_placement.check_edl(_edl([[0.0, 30.0]], sfx=three[:2]),
                                  {"words": _words(words)})
    assert got["budget"] is None
    # a graphic's own cue stack is one moment
    stack = [dict(_lib("pop_1", 12.0 + 0.3 * k), id=f"mg_list_sfx{k + 1}")
             for k in range(4)]
    assert len(sfx_placement.sound_events(three[:1] + stack)) == 2


def test_add_sfx_nudges_a_bright_sound_off_the_payoff_onset(lib):
    # '140' at 31.46 is shown by the counter; a marker graphic lands in the
    # breath after 'characters' (32.40-33.0): the ding moves there
    words = THIEL_WORDS + _words([("so", 33.0, 33.2)])
    edl = _edl([[0.0, 36.0]], motion=[
        {"id": "num", "template": "counter", "start": 30.46, "end": 32.6,
         "params": {"value": "140"}},
        {"id": "end", "template": "marker_text", "start": 32.6, "end": 35.0,
         "params": {"text": "flying cars"}}])
    ctx = _Ctx(edl, words=words)
    out = agent_tools.add_sfx(ctx, "sound:ding_1", at=31.46)
    assert "NUDGED" in out and "'140'" in out, out
    hit = sound_library.hit_at(ctx.sfx("sx1"))
    assert 32.4 - GRID <= hit <= 33.0 + GRID, hit
    assert "on_payoff_word" not in out
    # move_sfx puts it back deliberately, and the CHECK says what it costs
    out = agent_tools.move_sfx(ctx, "sx1", 31.46)
    assert abs(sound_library.hit_at(ctx.sfx("sx1")) - 31.46) <= GRID
    assert "CHECK:" in out and "'140'" in out


def test_a_pun_is_reported_not_moved(lib):
    words = _words([("deal", 31.0, 31.3), ("in", 31.3, 31.45),
                    ("pictures.", 31.64, 32.1), ("so", 32.6, 32.8)]
                   + [(f"w{i}", 1.0 + i * 0.5, 1.3 + i * 0.5) for i in range(40)])
    edl = _edl([[0.0, 40.0]], motion=[
        {"id": "pics", "template": "word_slam", "start": 31.64, "end": 32.34,
         "params": {"text": "*pictures*", "entrance": "pop"}}])
    ctx = _Ctx(edl, words=words)
    out = agent_tools.add_sfx(ctx, "sound:shutter_1", at=31.64)
    assert "NUDGED" not in out
    assert abs(sound_library.hit_at(ctx.sfx("sx1")) - 31.64) <= GRID
    assert "literal sound pun" in out


def test_audit_audio_mix_reports_levels_and_placement(lib):
    ctx = _Ctx(_thiel_edl([_lib("ding_1", 31.46, id="s3")]), words=THIEL_WORDS)
    ctx.db = type("D", (), {"run": lambda self, fn, *a, **k: None})()
    state = agent_tools._declared_mix_state(ctx, ctx._edl)
    warnings = agent_tools._sfx_mix_audit(ctx, ctx._edl, state["sfx"])
    row = state["sfx"][0]
    assert row["sound_id"] == "ding_1" and row["role"] == "ding"
    assert row["mix"]["verdict"] is None              # its role level
    assert row["mix"]["role_gain_db"] == sfx_mix.auto_gain(
        "ding_1", sfx_mix.NOMINAL_VOICE_LUFS)
    assert row["placement"] == ["on_payoff_word"]
    assert any("'140'" in w for w in warnings)


# ── the critic says it, never asks for more ───────────────────────────

def test_taste_names_placements_and_never_asks_for_more():
    edl = _thiel_edl([_lib("whoosh_soft_1", 0.25, id="s1"),
                      _lib("swish_1", 11.6, id="s2"),
                      _lib("ding_1", 31.46, id="s3")])
    tl = Timeline(edl["keep"], [], [])
    index = {"video": {"width": 1080, "height": 1920}, "words": THIEL_WORDS}
    found = taste.critique(edl, index, tl, 1080, 1920, "")
    joined = " | ".join(found)
    assert "at most 1-2" in joined and "'140'" in joined
    for f in found:
        assert "add_sfx" not in f and "add a sound" not in f
    # advisory evidence that does not depend on how the request was phrased
    assert taste.critique(edl, index, tl, 1080, 1920,
                          "add some sound effects") == found
    # the restrained version draws none of it
    quiet = _thiel_edl([_lib("swish_1", 11.6, id="s2")])
    found = taste.critique(quiet, index, tl, 1080, 1920, "")
    assert not any("at most 1-2" in f or "'140'" in f or "whoosh" in f
                   for f in found), found


# ── the rule reaches every surface that places sound ──────────────────

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_PLUGIN = os.path.join(_ROOT, "plugins", "valmera", "skills",
                       "valmera-podcast-shorts")


def _flat(text):
    return " ".join(text.split())


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_sound_rule_is_stated_verbatim_on_every_surface():
    rule = _flat(agent_prompt.SOUND_RULE)
    for phrase in ("DEFAULT TO ZERO", "at most 1-2 per short",
                   "a visual partner within ~50 ms",
                   "reflexive opening whoosh",
                   "on the onset of a payoff or emphasis word",
                   "a shutter on the word 'pictures'",
                   "a cash register on the word 'money' when nothing on "
                   "screen is a payment", "Leave gain_db unset"):
        assert phrase in rule, phrase
    mcp = _read(_ROOT, "backend", "routes", "mcp.py")
    surfaces = {"core prompt": agent_prompt.CORE_PROMPT,
                "mcp workflow": mcp[mcp.index('WORKFLOW = """'):],
                "plugin SKILL.md": _read(_PLUGIN, "SKILL.md")}
    for name in ("audio", "short-form-direction", "motion-design",
                 "transitions", "review"):
        surfaces["skill " + name] = agent_prompt.read_skill_text(name)
    for where, text in surfaces.items():
        assert rule in _flat(text), where


def test_no_surface_prescribes_a_pun_or_a_fixed_gain():
    retired = ("always pass the suggested gain", "gain_db=<suggested>",
               "a cash register on a money figure", "cash register on money",
               "a shutter on a photo or \"pictures\"", "ding_1 at 33.6",
               "the -6 dB default is too loud")
    surfaces = {"core prompt": agent_prompt.CORE_PROMPT,
                "add_sfx contract": agent_tools._COMPACT_CONTRACTS["add_sfx"],
                "list_sound_library contract":
                    agent_tools._COMPACT_CONTRACTS["list_sound_library"],
                "add_sfx tool": agent_tools.TOOLS["add_sfx"][1],
                "sound policy": motion_tools.SOUND_POLICY}
    for name in agent_prompt.skill_names():
        surfaces["skill " + name] = agent_prompt.read_skill_text(name)
    for rel in ("SKILL.md", "references/looks.md", "references/editing.md",
                "references/review.md"):
        surfaces["plugin " + rel] = _read(_PLUGIN, *rel.split("/"))
    for where, text in surfaces.items():
        low = _flat(text).lower()
        for phrase in retired:
            assert phrase.lower() not in low, (where, phrase)
