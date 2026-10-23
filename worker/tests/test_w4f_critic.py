"""Track 4F: the motion critic and photosensitivity (INTERFACES_W4.md §6).

CI-safe: every render here is a synthetic lavfi clip made on the spot
(ffmpeg 8.1 in CI). The footage-backed seeded-defect acceptance (AT-4F-1,
AT-4F-2) lives in benchmarks/refbench/at4f.py and tests/test_w4f_local.py.
"""

import copy
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import agent_tools                                         # noqa: E402
import motion_critic                                       # noqa: E402
import photosens                                           # noqa: E402
import schemas                                             # noqa: E402
import tools_ext                                           # noqa: E402

FFMPEG = os.environ.get("FFMPEG_BIN") or "ffmpeg"


def _ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True,
                   capture_output=True)


def _frames(pattern, w=32, h=18):
    """RGB frames from a list of grey levels (0..255) or RGB triples."""
    out = []
    for v in pattern:
        c = v if isinstance(v, (tuple, list)) else (v, v, v)
        out.append(np.full((h, w, 3), c, np.uint8))
    return out


# ── photosens ────────────────────────────────────────────────────────────

def test_six_white_flashes_a_second_are_over_the_limit():
    # 30 fps, a 1-frame white hit every 5 frames = 6 flashes/s for 2 s
    pat = [255 if i % 5 == 0 else 40 for i in range(60)]
    res = photosens.analyse(_frames(pat), 30.0)
    assert res["peak_per_s"] >= 5 and res["over_limit"]
    assert res["over"] and res["over"][0]["t0"] < 0.2
    assert not res["contains_flashing"]


def test_two_flashes_a_second_pass_and_opt_in_flags_the_export():
    pat = [255 if i % 15 == 0 else 40 for i in range(60)]
    res = photosens.analyse(_frames(pat), 30.0)
    assert res["peak_per_s"] <= 3 and not res["over_limit"]
    hot = [255 if i % 5 == 0 else 40 for i in range(60)]
    opted = photosens.analyse(_frames(hot), 30.0, opted_in=True)
    assert opted["over_limit"] and opted["contains_flashing"]


def test_red_flashes_are_counted_as_red():
    red, dark = (255, 0, 0), (30, 0, 0)
    pat = [red if i % 6 == 0 else dark for i in range(60)]
    res = photosens.analyse(_frames(pat), 30.0)
    assert any(f["red"] for f in res["flashes"])
    assert res["red_peak_per_s"] >= 3


def test_a_slow_fade_is_not_a_flash():
    pat = [int(40 + 200 * i / 59) for i in range(60)]
    res = photosens.analyse(_frames(pat), 30.0)
    assert res["peak_per_s"] == 0


def _hits_edl(times, prog=12.0):
    fx = [{"id": f"hit{k}", "type": "accent", "stage": "pre_captions",
           "start": round(t, 2), "end": round(t + 0.04, 2),
           "kind": "white_hit", "device": "accent_white_hit"}
          for k, t in enumerate(times)]
    edl = {"keep": [[0.0, prog]], "fx": fx}
    return schemas.validate_edl(edl, prog).model_dump()


def test_safe_plan_keeps_the_downbeats_and_meets_the_limit():
    # 22 white hits on a 16th grid of a 129 BPM track (0.1163 s apart)
    beat = 60.0 / 129.0
    times = [1.0 + k * beat / 4 for k in range(22)]
    downbeats = [1.0, 1.0 + 4 * beat]
    edl = _hits_edl(times)
    plan = photosens.safe_plan(edl, fps=25.0, downbeats=downbeats)
    assert plan["peak_before"] > 3 and plan["peak_after"] <= 3
    assert plan["kept_downbeats"] == 2
    kept_ids = {e["id"] for e in plan["kept"]}
    assert {"hit0", "hit16"} <= kept_ids         # the downbeat hits
    after = copy.deepcopy(edl)
    gone = {op["id"] for op in plan["ops"]}
    after["fx"] = [i for i in after["fx"] if i["id"] not in gone]
    import flash_safety
    assert flash_safety.assess(after, fps=25.0)["peak_per_s"] <= 3


def test_safe_plan_keeps_everything_when_the_user_asked_for_strobing():
    times = [1.0 + k * 0.1 for k in range(12)]
    plan = photosens.safe_plan(_hits_edl(times), fps=25.0,
                               request_text="make it strobe like ref 19")
    assert plan["opted_in"] and not plan["ops"]


# ── the critic on synthetic renders ─────────────────────────────────────

@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("critic")
    colour = str(d / "colour.mp4")
    mono = str(d / "mono.mp4")
    strobe = str(d / "strobe.mp4")
    common = ["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
              "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
              "-preset", "ultrafast", "-c:a", "aac", "-shortest"]
    _ff("-f", "lavfi", "-i", "testsrc2=size=270x480:rate=25", *common,
        colour)
    _ff("-f", "lavfi", "-i", "testsrc2=size=270x480:rate=25,hue=s=0",
        *common, mono)
    # a white frame every 4th frame (6.25/s) over the first 4 s
    _ff("-f", "lavfi", "-i",
        "color=c=0x303030:size=270x480:rate=25,"
        "geq=lum='if(lt(N,100)*eq(mod(N,4),0),235,48)':cb=128:cr=128",
        *common, strobe)
    return {"colour": colour, "mono": mono, "strobe": strobe}


def _edl(prog=8.0, **extra):
    edl = {"keep": [[0.0, prog]], **extra}
    return schemas.validate_edl(edl, prog).model_dump()


def _codes(res):
    return {f["code"] for f in res["findings"]}


def test_a_constant_black_and_white_short_is_a_look_not_a_device(clips):
    edl = _edl(effects={"grade": "bw"})
    res = motion_critic.critique(clips["mono"], edl, {}, deep=False)
    f = [x for x in res["findings"] if x["code"] == "bw_as_look"]
    assert f and f[0]["severity"] == "fix"
    assert res["measures"]["colour"]["bw_share"] > 0.9
    # the fix turns the look into a device: the preset goes
    ops = f[0].get("ops") or []
    assert any(op["layer"] == "effects" and op["value"].get("grade") is None
               for op in ops)


def test_black_and_white_the_user_asked_for_is_not_a_finding(clips):
    res = motion_critic.critique(clips["mono"], _edl(), {}, deep=False,
                                 request_text="make it black and white")
    assert "bw_as_look" not in _codes(res)
    res = motion_critic.critique(clips["colour"], _edl(), {}, deep=False)
    assert "bw_as_look" not in _codes(res)
    assert res["measures"]["colour"]["bw_share"] < 0.1


def test_flashing_render_is_blocking_and_names_the_limit(clips):
    res = motion_critic.critique(clips["strobe"], _edl(), {}, deep=False)
    f = [x for x in res["findings"] if x["code"] == "flash_rate"]
    assert f and f[0]["severity"] == "blocking"
    assert res["measures"]["flash"]["peak_per_s"] > 3
    opted = motion_critic.critique(clips["strobe"], _edl(), {}, deep=False,
                                   request_text="strobe it hard")
    assert "flash_rate" not in _codes(opted)


def test_critique_never_raises_and_scores_a_clean_clip_high(clips, tmp_path):
    res = motion_critic.critique(clips["colour"], _edl(), {},
                                 board_dir=str(tmp_path))
    assert res["score"] is not None and res["score"] >= 8.0
    assert not [f for f in res["findings"] if f["severity"] == "blocking"]
    assert res["board"] and os.path.exists(res["board"])
    text = motion_critic.critique_text(res)
    assert text.startswith("CRITIC v1")
    bad = motion_critic.critique(str(tmp_path / "missing.mp4"), _edl(), {})
    assert bad["findings"][0]["code"] == "unreadable"


# ── words and audio-verified joins ──────────────────────────────────────

@pytest.fixture(scope="module")
def two_tone(tmp_path_factory):
    """4 s: speech-level tone from 0-2.5 s, silence 2.5-3.0 s, tone again."""
    d = tmp_path_factory.mktemp("words")
    out = str(d / "join.mp4")
    _ff("-f", "lavfi", "-i", "color=c=gray:size=160x284:rate=25",
        "-f", "lavfi", "-i",
        "aevalsrc='0.4*sin(2*PI*220*t)*(lt(t,2.5)+gte(t,3.0))':s=48000",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset",
        "ultrafast", "-c:a", "aac", out)
    return out


def test_a_cut_through_a_sounding_word_is_blocking(two_tone):
    # program join at 2.0 s (keep 0-2 | 10-12): the render is loud on both
    # sides of the join and a word spans the out-point
    edl = {"keep": [[0.0, 2.0], [10.0, 12.0]]}
    edl = schemas.validate_edl(edl, 20.0).model_dump()
    index = {"words": [{"w": "never", "t0": 1.8, "t1": 2.3},
                       {"w": "again", "t0": 10.3, "t1": 10.8}]}
    plan = {"words": [{"w": "never", "t0": 1.8, "t1": 2.0},
                      {"w": "again", "t0": 2.3, "t1": 2.8}]}
    got = motion_critic.lens_words(edl, index, plan, 4.0, two_tone)
    f = [x for x in got if x["code"] == "cut_mid_word"]
    assert f and f[0]["severity"] == "blocking"
    assert f[0]["op"]["layer"] == "keep"
    # the op names the edge the keep tools place there without the sound
    # (one rule: cut_audio.place_edge), not the bare transcript edge
    import cut_audio
    want = cut_audio.place_edge("end", 2.0, index["words"], None)["t"]
    assert f[0]["op"]["value"][0][1] == pytest.approx(want)


def test_a_cut_in_silence_inside_an_asr_word_span_is_clean(two_tone):
    # join at 2.75 s program, inside the silent stretch of the render
    edl = {"keep": [[0.0, 2.75], [10.0, 11.25]]}
    edl = schemas.validate_edl(edl, 20.0).model_dump()
    index = {"words": [{"w": "and", "t0": 2.4, "t1": 3.2},
                       {"w": "so", "t0": 10.4, "t1": 10.7}]}
    got = motion_critic.lens_words(edl, index, {"words": []}, 4.0, two_tone)
    assert not [x for x in got if x["code"] == "cut_mid_word"]


def test_payoff_hold_short_is_a_fix():
    plan = {"words": [{"w": "end", "t0": 7.3, "t1": 7.6}]}
    got = motion_critic.lens_words({"keep": [[0, 8.0]]}, {}, plan, 8.0)
    assert [x for x in got if x["code"] == "payoff_hold_short"]


# ── range: invention over a house style ─────────────────────────────────

def _styled(devs):
    fx = []
    for k, d in enumerate(devs):
        fx.append({"id": f"f{k}", "type": "finish", "stage": "pre_captions",
                   "start": 1.0 + k, "end": 1.5 + k, "kind": d,
                   "device": f"finish_{d}"})
    return {"keep": [[0.0, 10.0]], "fx": fx,
            "motion": [{"id": "m1", "template": "word_slam", "start": 0.2,
                        "end": 1.5, "params": {"text": "HELLO"}}]}


def test_the_same_device_set_across_shorts_is_a_house_style():
    mine = _styled(["thermal", "crt"])
    prev = [_styled(["thermal", "crt"]), _styled(["thermal", "crt"])]
    got, meas = motion_critic.lens_range(mine, {"moments": []}, prev)
    assert [f for f in got if f["code"] == "house_style"]
    fresh = [_styled(["halftone"]), _styled(["film", "lcd"])]
    got, _ = motion_critic.lens_range(mine, {"moments": []}, fresh)
    assert not [f for f in got if f["code"] == "house_style"]


def test_untapped_moments_get_at_most_three_fresh_bound_ideas():
    plan = {"moments": [
        {"t": 2.0 + k, "kind": "stress", "strength": 0.9 - 0.05 * k,
         "tags": ["number"] if k == 0 else ["contrast"],
         "word": f"w{k}", "word_raw": f"w{k}",
         "bind": {"on": "stress", "ref": k}} for k in range(6)],
        "payoff": {"t": 7.0}}
    got, meas = motion_critic.lens_range({"keep": [[0, 10]]}, plan, [])
    ideas = [f for f in got if f["code"] == "untapped_moment"]
    assert 1 <= len(ideas) <= 3
    assert len({i["verb"] for i in meas["ideas"]}) == len(meas["ideas"])
    assert all(f["severity"] == "advisory" for f in ideas)


# ── the verb ────────────────────────────────────────────────────────────

def test_critique_short_is_a_read_only_verify_verb():
    assert "critique_short" in agent_tools.TOOLS
    assert "critique_short" in tools_ext.EXT_DEPARTMENTS["verify"]
    assert "critique_short" not in agent_tools.WRITE_TOOLS
    assert "critique_short" not in tools_ext.core_names(agent_tools.TOOLS)
    desc = agent_tools.TOOLS["critique_short"][1]
    assert desc.startswith("READ") and len(desc) <= 900
