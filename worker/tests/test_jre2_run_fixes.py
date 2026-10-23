"""The Valmera defects the JRE #2054 Elon Musk part 2 podcast run (Oct 16-22,
podcast-tAJUwiAqW38-20261016-part2) recorded, each pinned to its evidence."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import agent_tools  # noqa: E402
import config  # noqa: E402
import motion_tools  # noqa: E402
import remote  # noqa: E402
import schemas  # noqa: E402
import timeline  # noqa: E402
import test_music_library_and_looks as looks_t  # noqa: E402


# ── fx write gates read the Cloudflare Worker's build features ──────────
# 97 writes refused in the week to 23 Oct with "the render service does not
# draw <type> yet": source_broll (p30 3847, p36, p39), add_time_effect (p27
# 3844, p25 3842), set_grade_window (p41 3858), add_accent, add_depth_effect,
# add_finish, set_junction... The Worker's root /health lists build features
# only, never "fx:<type>".

WORKER_HEALTH = {"provider": "cloudflare", "status": "ok",
                 "features": ["custom_filter", "stems", "skew_guard",
                              "fence", "alt_slots_32"]}


@pytest.fixture
def cloudflare(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://valmera-executor.example.workers.dev")
    body = dict(WORKER_HEALTH)
    monkeypatch.setattr(remote, "executor_health", lambda **k: body)
    return body


@pytest.mark.parametrize("fx_type", ["time", "broll", "grade_window",
                                     "accent", "depth", "finish", "subject",
                                     "junction", "region", "ui_media"])
def test_a_skew_guarded_executor_draws_every_registered_fx_type(cloudflare,
                                                                fx_type):
    assert fx_type in schemas.FX_TYPES
    assert remote.executor_supports(f"fx:{fx_type}") is True


def test_an_unknown_fx_type_or_a_missing_build_feature_is_still_no(
        cloudflare):
    assert remote.executor_supports("fx:hologram") is False
    assert remote.executor_supports("demucs_v9") is False
    assert remote.executor_supports("custom_filter") is True


def test_without_the_skew_guard_the_listed_features_decide(cloudflare):
    cloudflare["features"] = ["custom_filter"]
    assert remote.executor_supports("fx:time") is False
    cloudflare["features"] = ["custom_filter", "fx:time"]
    assert remote.executor_supports("fx:time") is True


def test_the_time_and_cutaway_gates_no_longer_refuse(cloudflare):
    from tools_ext import broll_tools, depth_tools, finish_tools
    assert depth_tools._unavailable("time") == ""
    assert depth_tools._unavailable("depth") == ""
    assert broll_tools._unavailable() == ""
    assert finish_tools._unavailable("grade_window") == ""


# ── a look's dip transition keeps to media joins on a talking short ─────
# apply_look('cinematic_doc') wrote dip_black 0.45 s at scope 'scene'; on a
# podcast short every camera change is a 'scene' junction, so the picture
# blanked mid-sentence at each one (project 3850, job 88629).

@pytest.fixture
def looks_env(monkeypatch):
    monkeypatch.setattr(motion_tools.motion_engine, "authorable",
                        lambda: True)
    monkeypatch.setattr(agent_tools, "_get_perception",
                        lambda ctx: (_ for _ in ()).throw(RuntimeError("off")),
                        raising=False)


def _two_camera_ctx(with_broll):
    # Joe / Elon alternating shots every 4 s, one keep per shot
    dur = 32.0
    shots = [{"id": i, "start": i * 4.0, "end": (i + 1) * 4.0}
             for i in range(8)]
    edl = schemas.default_edl(dur)
    edl["keep"] = [[i * 4.0 + 0.1, (i + 1) * 4.0 - 0.1] for i in range(8)]
    if with_broll:
        edl["inserts"] = [{"id": "ins1", "asset_key": "img/7/0.png",
                           "kind": "image", "at_output_s": 11.4,
                           "duration_s": 2.0}]
    ctx = looks_t._Ctx(ratio="9:16", duration=dur, edl=edl, shots=shots)
    ctx.index["words"] = looks_t._words(n=60, step=0.5)
    return ctx


@pytest.mark.parametrize("name", ["cinematic_doc", "editorial"])
def test_a_look_on_a_talking_short_skips_camera_changes(looks_env, name):
    ctx = _two_camera_ctx(with_broll=False)
    out = agent_tools.apply_look(ctx, name)
    assert out.startswith("EDL v1"), out
    edl = ctx.latest_edl()["json"]
    tr = edl["effects"]["transition"]
    assert tr["scope"] == "media"
    assert "camera changes inside the conversation stay hard cuts" in out
    assert timeline.transition_junctions(edl, ctx.index) == set()


def test_the_dip_still_lands_where_broll_enters_and_leaves(looks_env):
    ctx = _two_camera_ctx(with_broll=True)
    agent_tools.apply_look(ctx, "cinematic_doc")
    edl = ctx.latest_edl()["json"]
    ks = timeline.transition_junctions(edl, ctx.index)
    blocks = timeline.program_blocks(edl)
    assert ks and all(blocks[k]["kind"] == "insert"
                      or blocks[k + 1]["kind"] == "insert" for k in ks)
    # the same edit at scope 'scene' fires on every camera change too
    edl["effects"]["transition"]["scope"] = "scene"
    assert len(timeline.transition_junctions(edl, ctx.index)) > len(ks)


def test_every_look_base_on_a_talking_short_keeps_to_media(looks_env):
    # 41 set_transitions('none') after 54 apply_look calls in the run
    for name in ("creator_punch", "mono_noir"):
        ctx = _two_camera_ctx(with_broll=False)
        agent_tools.apply_look(ctx, name)
        edl = ctx.latest_edl()["json"]
        assert edl["effects"]["transition"]["scope"] == "media", name
        assert timeline.transition_junctions(edl, ctx.index) == set()


def test_a_landscape_cut_keeps_scene_scope(looks_env):
    wide = _two_camera_ctx(with_broll=False)
    wide._edl["frame"] = dict(wide._edl.get("frame") or {}, ratio="16:9")
    agent_tools.apply_look(wide, "cinematic_doc")
    assert wide.latest_edl()["json"]["effects"]["transition"]["scope"] \
        == "scene"


def test_set_transitions_accepts_the_media_scope(looks_env):
    ctx = _two_camera_ctx(with_broll=True)
    out = agent_tools.set_transitions(ctx, "dip_white", 0.2, scope="media")
    assert out.startswith("EDL v1"), out
    assert "media joins" in out
    assert ctx.latest_edl()["json"]["effects"]["transition"]["scope"] \
        == "media"


# ── a keep change leaves the hook on the opening ────────────────────────
# p25 (3842) v28 -> v29: an earlier first start moved the hook word_slam
# from 0-1.37 to 0.88-2.25 s; v32 -> v33: a 0.84 s head trim left it
# 0-0.77 s (job 89412). p14's collapsed to 0.0-0.61 s.

P25_V32 = [[3194.12, 3196.38], [3196.7, 3197.74], [3199.34, 3200.61],
           [3202.54, 3205.26]]


def _hook_edl(keep, hook=(0.0, 1.6)):
    return {"keep": [list(k) for k in keep], "inserts": [],
            "motion": [{"id": "hook", "template": "word_slam",
                        "start": hook[0], "end": hook[1], "params": {}},
                       {"id": "ts_gov", "template": "typeset",
                        "start": 5.2, "end": 7.28, "params": {}}]}


def _recut(edl, new_keep):
    old = timeline.Timeline(edl["keep"], [], [])
    edl["keep"] = [list(k) for k in new_keep]
    new = timeline.Timeline(edl["keep"], [], [])
    return timeline.remap_program_items(edl, old, new)


def test_a_head_trim_keeps_the_hook_readable_on_the_opening():
    edl = _hook_edl(P25_V32)
    v33 = [list(k) for k in P25_V32]
    v33[0][0] = 3194.96
    notes = _recut(edl, v33)
    mo = {m["id"]: m for m in edl["motion"]}
    assert (mo["hook"]["start"], mo["hook"]["end"]) == (0.0, 1.2)
    assert any("keeps the opening" in n for n in notes)
    # a cued graphic later in the short still follows its words
    assert mo["ts_gov"]["start"] == pytest.approx(5.2 - 0.84, abs=0.02)


def test_an_earlier_first_start_keeps_the_hook_opening_the_short():
    v28 = [list(k) for k in P25_V32]
    v28[0][0] = 3195.01
    edl = _hook_edl(v28, hook=(0.0, 1.37))
    v29 = [list(k) for k in v28]
    v29[0][0] = 3194.12
    _recut(edl, v29)
    hook = edl["motion"][0]
    assert hook["start"] == 0.0
    assert hook["end"] == pytest.approx(1.37 + 0.89, abs=0.02)


def test_a_graphic_after_the_opening_still_moves_with_its_moment():
    edl = _hook_edl(P25_V32, hook=(0.4, 1.6))
    v33 = [list(k) for k in P25_V32]
    v33[0][0] = 3194.32
    _recut(edl, v33)
    assert edl["motion"][0]["start"] == pytest.approx(0.2, abs=0.02)


# ── set_picture_card: an end a hair past the programme is its end ───────
# 3859 job 89471: 'picture card: end 23.04 exceeds the limit 23.0s'.

def test_a_card_end_a_hair_past_the_programme_ends_with_it():
    from test_kamath_run_fixes import _CardCtx
    ctx = _CardCtx(duration=23.0)
    res = agent_tools.set_picture_card(
        ctx, "card", 0.0, 23.04, box=[0, 0.245, 1, 0.794], fit="crop",
        source=[0.222, 0, 0.793, 0.99], follow=False)
    assert res.startswith("EDL v"), res
    assert ctx.card("card")["end"] == 23.0
    assert "ends with it at 23s" in res
    # a real overrun still reaches validation (the editor's ctx refuses it)
    with pytest.raises(Exception, match="exceeds the limit"):
        agent_tools.set_picture_card(ctx, "card2", 0.0, 24.0,
                                     box=[0, 0, 1, 1], follow=False)


# ── behind-subject type: the legibility note no longer blesses lost letters
# 3856 job 89180: a hero ANYWHERE behind Elon, subject over 73% of its width;
# the note said "Right for a giant hero word" and the render read 'ANYW.RE'.

def test_a_word_the_subject_mostly_covers_is_warned_not_blessed():
    stats = {"coverage": 0.619, "coverage_max": 0.705, "engine": "rvm",
             "text_covered": 0.31, "text_width_covered": 0.73,
             "text_visible_mean": 0.6}
    item = {"start": 16.3, "end": 18.6, "template": "word_slam"}
    out = motion_tools._behind_report(stats, {"effects": {}}, item)
    assert "Right for a giant hero word" not in out
    assert "loses whole letters" in out and "look_at" in out
    assert "60% of the graphic stays visible" in out


def test_a_light_crossing_still_reads_as_legible_depth():
    stats = {"coverage": 0.3, "coverage_max": 0.35, "engine": "rvm",
             "text_covered": 0.08, "text_width_covered": 0.2,
             "text_visible_mean": 0.9}
    out = motion_tools._behind_report(stats, {"effects": {}},
                                      {"start": 1.0, "end": 3.0})
    assert "stays legible" in out and "LEGIBILITY" not in out


# ── word_slam: the text budget counts what reads, an empty tier is unset ─
# 12 refusals 'word_slam.text is 41 characters; keep it to 40' on hooks of
# 36-39 characters of type; 5 refusals of "tier": "" (jobs 92022-92027).

import motion_templates  # noqa: E402


def test_markup_does_not_count_against_the_word_slam_budget():
    text = 'EX-TWITTER STAFF / *“SHIFT TO THE RIGHT”*'
    assert len(text) == 41
    out = motion_templates.check_params(
        "word_slam", {"text": text, "tier": "hook"})
    assert out["text"] == text
    with pytest.raises(ValueError, match="on screen; keep it to 40"):
        motion_templates.check_params("word_slam", {
            "text": "physics, I would say, is the study of that which has "
                    "/ *predictive* value"})


def test_an_empty_enum_reads_as_its_default():
    out = motion_templates.check_params(
        "word_slam", {"text": "TWO AGAINST / *10,000*", "tier": ""})
    assert out["tier"] == "display"
    with pytest.raises(ValueError, match="tier must be one of"):
        motion_templates.check_params("word_slam",
                                      {"text": "X", "tier": "kicker"})


# ── stock picks keep the thumbnail evidence across MCP calls ────────────
# 10 add_stock_media refusals ('no evidence of what the shot actually
# depicts', e.g. 3881 job 92240 after search 92235 attached the thumbnails):
# the hits were saved before their thumbnails were delivered.

import json as _json  # noqa: E402

import db as dbx  # noqa: E402
import stock  # noqa: E402


class _KvDb:
    def __init__(self):
        self.kv = {}

    def run(self, fn, *a, **k):
        if fn is dbx.kv_merge_json_map:
            key, value = a[0], a[1]
            cur = _json.loads(self.kv.get(key) or "{}")
            cur.update(_json.loads(value))
            self.kv[key] = _json.dumps(cur)
            return None
        if fn is dbx.kv_get:
            return self.kv.get(a[0])
        return None


class _StockCtx:
    project_id = 3881

    def __init__(self, db):
        self.db = db
        self.stock_results = {}
        self.workdir = "/tmp"

    def latest_edl(self):
        return {"version": 1, "json": {"frame": {"ratio": "9:16"}}}


def test_a_search_saves_which_thumbnails_the_editor_saw(monkeypatch):
    hits = [{"id": "pexels:video:20624465", "kind": "video",
             "description": "", "_thumb": "https://x/t.jpg"}]
    monkeypatch.setattr(stock, "available", lambda: True)
    monkeypatch.setattr(stock, "search", lambda *a, **k: [dict(h)
                                                           for h in hits])

    def deliver(ctx, found):
        for h in found:
            h["_thumbnail_delivered"] = True
        return len(found)
    monkeypatch.setattr(agent_tools, "_queue_candidate_thumbs", deliver)
    db = _KvDb()
    out = agent_tools.search_stock(_StockCtx(db), "silverback gorilla")
    assert "stock video" in out
    # the next MCP call runs in a fresh process: only the saved copy
    fresh = _StockCtx(db)
    got, _err = agent_tools._recover_search_hit(
        fresh, "stock", "pexels:video:20624465", None)
    assert got and got.get("_thumbnail_delivered") is True


# ── an exact-time keep judges a "mid-word" edge on its sound ────────────
# p25 (3842) keep_segments(snap_to_words=false) at 3228.71 / 3239.62 /
# 3279.70, the starts the render's MID-WORD AUDIT asked for, answered
# "WARNING: ... lands inside the word": the ASR filed the pause before the
# word under it.

import shutil  # noqa: E402

import test_cut_hygiene_r7 as cut_t  # noqa: E402

EARLY_ASR = [{"w": "somebody", "t0": 0.60, "t1": 1.00},
             {"w": "was", "t0": 1.00, "t1": 1.30},
             {"w": "like,", "t0": 1.50, "t1": 1.95},    # sound from 1.60
             {"w": "ever", "t0": 2.40, "t1": 2.70}]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_an_exact_edge_in_the_pause_is_not_warned_as_mid_word(tmp_path):
    wav = str(tmp_path / "s.wav")
    cut_t._speech_wav(wav)
    ctx = cut_t._Ctx(sound=wav, words=EARLY_ASR)
    res = agent_tools.keep_segments(ctx, [[0.0, 1.0], [1.53, 5.0]],
                                    snap_to_words=False)
    assert ctx._edl["keep"] == [[0.0, 1.0], [1.53, 5.0]]
    assert "lands inside the word 'like,'" not in res
    # an edge inside the word's own sound still warns
    ctx2 = cut_t._Ctx(sound=wav, words=EARLY_ASR)
    res = agent_tools.keep_segments(ctx2, [[0.0, 1.15], [2.45, 5.0]],
                                    snap_to_words=False)
    assert "lands inside the word 'was'" in res


def test_without_sound_the_transcript_still_warns():
    ctx = cut_t._Ctx(sound=None, words=EARLY_ASR)
    res = agent_tools.keep_segments(ctx, [[0.0, 1.0], [1.53, 5.0]],
                                    snap_to_words=False)
    assert "lands inside the word 'like,'" in res


# ── check_speech without a window reads the whole short ─────────────────
# 3 refusals "check_speech() missing 2 required positional arguments".

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_check_speech_without_a_window_reads_the_whole_short(tmp_path):
    import subprocess
    from tools_ext import rhythm_tools
    wav = str(tmp_path / "src.wav")
    tones = "+".join(f"between(t,{a},{b})*sin(2*PI*220*t)*0.5"
                     for a, b in ((1.0, 1.5), (5.5, 6.0)))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='{tones}':s=16000:d=8", wav], check=True)

    class Ctx:
        index = {"words": [{"w": "one", "t0": 1.0, "t1": 1.5},
                           {"w": "two", "t0": 5.5, "t1": 6.0}]}

        def latest_edl(self):
            return {"version": 3, "json": {"keep": [[0.0, 2.0], [5.0, 7.0]]}}

        def proxy_path(self):
            return wav

    out = rhythm_tools.check_speech(Ctx())
    assert "program 0.00-4.00s" in out and "two@2.50" in out, out


# ── audit_captions names a word shown twice in a row ────────────────────
# p59 shipped 'COULD COULD'; p30 a run of stuttered 'at's. The critic reads
# frames, not words.

def test_doubled_caption_words_are_named_once_per_phrase():
    states = [{"start": 3.1, "end": 3.4, "text_lines": ["WE COULD COULD"]},
              {"start": 3.4, "end": 3.9, "text_lines": ["WE COULD COULD"]},
              {"start": 7.0, "end": 7.5, "text_lines": ["at at service"]},
              {"start": 9.0, "end": 9.5, "text_lines": ["I had had enough"]},
              {"start": 11.0, "end": 11.5, "text_lines": ["no, no, no"]},
              {"start": 12.0, "end": 12.5, "text_lines": ["1 1 0"]}]
    got = agent_tools.caption_doubled_words(states)
    assert [g["text"] for g in got] == ["WE COULD COULD", "at at service"]
    assert got[0]["start"] == 3.1


# ── argument spellings editors paid a refusal for (week to 23 Oct) ──────

def test_overlay_and_asset_spellings_are_read():
    norm = agent_tools._normalize_tool_call
    got = norm("add_overlay", {"media_key": "fetched/4383/stock/a.mp4",
                               "start": "33.0", "end": "37.0",
                               "fit": "picture"})[1]
    assert got["asset_key"] == "fetched/4383/stock/a.mp4"
    assert got["duration_s"] == 4.0 and "end" not in got
    got = norm("add_overlay", {"media": "x.mp4", "start": 32.45,
                               "end": "33.95"})[1]
    assert got["asset_key"] == "x.mp4" and got["duration_s"] == 1.5
    got = norm("look_at_asset",
               {"storage_key": "generated/4250/freeze-7f.png"})[1]
    assert got == {"asset_key": "generated/4250/freeze-7f.png"}
    # a real argument is never overwritten, a bad end is left to refuse
    got = norm("add_overlay", {"asset_key": "a", "media": "b", "start": 1,
                               "end": 0.5})[1]
    assert got["asset_key"] == "a" and "end" in got


# ── set_caption_fixes answers with the audit it needs, not every state ──
# 216 calls averaged 12.8 KB of reply (the whole audit_captions JSON).

def test_the_correction_preview_keeps_the_verdict_and_the_fixed_states():
    states = [{"start": i * 0.5, "end": i * 0.5 + 0.5,
               "text_lines": [f"line {i} words here"]} for i in range(120)]
    states[40]["text_lines"] = ["at sea level"]
    audit = {"version": 9, "status": "warnings", "warnings": ["w"],
             "corrections_not_applied": None, "event_page": states,
             "density_violations": [], "qa_output_times": list(range(50))}
    out = agent_tools._compact_caption_preview(
        _json.dumps(audit), [{"from": "at service", "to": "at sea level"}])
    got = _json.loads(out)
    assert got["status"] == "warnings" and got["warnings"] == ["w"]
    assert got["corrected_states"] == [
        {"start": 20.0, "end": 20.5, "text": "at sea level"}]
    assert "event_page" not in got and len(out) < 600


# ── template parameters passed at the top level reach params ────────────
# add_motion_graphic(tier='hook') was refused "unexpected keyword argument".

def test_top_level_template_params_are_hoisted_into_params():
    norm = agent_tools._normalize_tool_call
    got = norm("add_motion_graphic", {"template": "word_slam", "start": 0,
                                      "end": 1.6, "tier": "hook",
                                      "params": {"text": "*FRAGILE*"}})[1]
    assert got["params"] == {"text": "*FRAGILE*", "tier": "hook"}
    assert "tier" not in got
    # an unknown key is left for the tool to name; params win over strays
    got = norm("add_motion_graphic", {"template": "word_slam", "start": 0,
                                      "text": "A", "bogus": 1,
                                      "params": {"text": "B"}})[1]
    assert got["params"] == {"text": "B"} and got["bogus"] == 1


# ── a typeset that fits nowhere is not told to try what it just tried ───

def test_the_no_fit_refusal_skips_options_the_call_already_used():
    from tools_ext import typeset_tools
    tried = {"text": "really *any* cognitive task",
             "voice": "editorial_serif", "layout": "side_column",
             "depth": "front"}
    out = typeset_tools._no_fit_options(tried, 21.19, 22.87)
    assert "editorial_serif" not in out and "side_column" not in out
    assert "depth front" not in out and "fewer words" in out
    assert "word_slam" in out


def test_hoisting_survives_the_dispatcher_normalizing_the_payload_first():
    # job 94714: skew_guard._mcp_problem normalized the payload on the
    # dispatcher and filled the caller's params dict in place; the executor
    # then saw 'tier' in params and at the top level, and refused.
    norm = agent_tools._normalize_tool_call
    payload_args = {"template": "word_slam", "start": 10.3, "end": 11.8,
                    "tier": "display", "params": {"text": "*PIZZA*"}}
    first = norm("add_motion_graphic", payload_args)[1]
    assert payload_args["params"] == {"text": "*PIZZA*"}     # untouched
    second = norm("add_motion_graphic", payload_args)[1]
    assert first == second and "tier" not in second
    assert second["params"] == {"text": "*PIZZA*", "tier": "display"}
    # a stray that params already holds is dropped, params wins
    got = norm("add_motion_graphic", {"template": "word_slam", "start": 0,
                                      "tier": "hook",
                                      "params": {"text": "A",
                                                 "tier": "payoff"}})[1]
    assert "tier" not in got and got["params"]["tier"] == "payoff"


# ── add_depth_effect takes the focus names its schema offers ────────────
# project 4425 jobs 94775/94781: focus="subject" (the schema's enum) was
# refused "focus must be an object" on the first depth writes after the
# render gates opened.

from test_w4_review import ctx as depth_ctx  # noqa: E402,F401


def test_a_focus_name_is_read_as_its_target(depth_ctx):
    from tools_ext import depth_tools
    out = depth_tools.add_depth_effect(depth_ctx, "dof", start=2.0, end=4.0,
                                       focus="subject")
    assert out.startswith("EDL "), out
    item = [i for i in depth_ctx._edl["fx"] if i.get("kind") == "dof"][-1]
    assert item["focus"]["on"] == "subject"
    out = depth_tools.add_depth_effect(
        depth_ctx, "rack_focus", word="growth", focus="subject",
        focus_from="background")
    assert out.startswith("EDL "), out
    rack = [i for i in depth_ctx._edl["fx"]
            if i.get("kind") == "rack_focus"][-1]
    assert rack["focus_from"]["on"] == "background"


def test_overlay_coordinates_sent_as_text_are_read():
    assert agent_tools._parse_anim_float("0.215", "x") == (0.215, None)
    got, err = agent_tools._parse_anim_float('[{"t":0,"v":0.8}]', "x")
    assert err is None and got == [{"t": 0, "v": 0.8}]
    assert agent_tools._parse_anim_float("left", "x")[1].startswith(
        "REJECTED")
