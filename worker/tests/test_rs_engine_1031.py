"""RS_ENGINE (31 Oct 2026): the trial users' 2-3/10 sessions, as tests.

#55 user 2670: a range listed twice in keep_segments merged to one copy, so
     a part could not be looped to the length asked.
#54 user 2670: "the first 8.2 seconds of X, then a clip of Y" was read as an
     8.2 s TOTAL.
#52 user 2669: the First cut cut a 39 s, 9-word clip to its 3.4 s of speech
     under the fragment hook "SEEM LIKE THEY WILL DO".
#56 user 2672: stacked looks (custom contrast + shadow drop + cinematic +
     vignette) crushed dark gameplay; nothing measured it.
user 2665: music asked for, the audio muted, no track offered.
"""
import json
import os
import subprocess

import pytest

import agent_tools
import first_cut
import look_guard
import music_offer
import quality_verifier

ASK_2670A = ("Can you cut the video so for the first 8.2 seconds, it is a "
             "portion of someone standing stationary, and then just a small "
             "snipit of someone taking a first touch from a pass, and thats it")
ASK_2670B = ("just have the first 8.2 seconds of the person standing still, it "
             "doesn't have to be a literal 8.2 second clip from the video, you "
             "can take a specific part and copy it till it makes 8.2 seconds, "
             "and then just one clip of a first touch")
ASK_2665 = ("Cut out the audio and play something else relating to nature and "
            "outdoors and active but chilly vibes. Give colour gradings "
            "cinematic vibes . Correct the angle (if possible) make it looj "
            "mire dramatic but not to lose the actual original outlines .")
ASK_2672 = ("Enhance this Dead by Daylight gaming video with professional "
            "cinematic colors. Make the colors vibrant and rich, improve "
            "contrast, add sharp details, create dramatic lighting and deep "
            "shadows. Keep the original gameplay natural and clear. Make it "
            "look like a professional TikTok gaming edit. Avoid oversaturation "
            "and do not change the original content.")

STAND = [100.69, 102.53]
TOUCH = [164.25, 166.58]


# ---- #55 repeats -----------------------------------------------------------

def test_a_range_listed_five_times_stays_five_times():
    base, replays = agent_tools._plan_keep_repeats([STAND] * 5 + [TOUCH])
    assert base == [STAND, TOUCH]
    assert [a for a, _ in replays] == [0, 0, 0, 0]
    assert all(r == STAND for _a, r in replays)


def test_a_list_without_repeats_keeps_the_old_sorted_merge():
    base, replays = agent_tools._plan_keep_repeats([[5, 6], [1, 2], [2, 3]])
    assert base == [[1, 3], [5, 6]] and replays == []


def test_once_a_list_repeats_the_listed_order_is_the_played_order():
    base, replays = agent_tools._plan_keep_repeats([[50, 52], [50, 52], [10, 12]])
    assert base == [[50, 52]]
    assert replays == [(0, [50.0, 52.0]), (0, [10.0, 12.0])]


class _Db:
    def __init__(self, orig):
        self.orig = orig

    def run(self, fn, *a, **k):
        assert fn is agent_tools.dbx.latest_asset
        return self.orig


class _Ctx:
    """keep_segments' surface: clamp, latest_edl, write_edl, db."""

    def __init__(self, frame=None, request=""):
        self.duration = 397.0
        self.project_id = 5277
        self.index = {"words": []}
        self.user_message = request
        self.verification_request = None
        self.db = _Db({"storage_key": "originals/5277/f29fcb2bfb80.mp4",
                       "width": 2560, "height": 1440, "meta": {}})
        self.versions = [{"version": 1, "json": {
            "keep": [[0.0, 30.0]], "inserts": [], "speed": [],
            "frame": frame or {}}}]

    def clamp(self, v):
        return float(v)

    def latest_edl(self):
        return self.versions[-1]

    def write_edl(self, edl, desc):
        v = self.versions[-1]["version"] + 1
        self.versions.append({"version": v, "json": json.loads(json.dumps(edl))})
        return f"EDL v{v}: {desc}"


def _fake_write_keep(ctx, merged, desc, **kw):
    edl = dict(ctx.latest_edl()["json"])
    edl["keep"] = [list(x) for x in merged]
    return ctx.write_edl(edl, desc)


def test_keep_segments_splices_the_repeats_on_the_programs_framing(monkeypatch):
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    frame = {"mode": "crop", "ratio": "9:16", "focus_x": 0.5, "focus_y": 0.42,
             "focus_track": [{"x": 0.79, "y": 0.42, "t0": 100.0, "t1": 103.0,
                              "mode": "crop"}]}
    ctx = _Ctx(frame, request=ASK_2670B)
    res = agent_tools.keep_segments(ctx, [STAND] * 5 + [TOUCH])
    assert res.startswith("EDL v2") and "REPEATS KEPT" in res
    edl = ctx.latest_edl()["json"]
    assert edl["keep"] == [STAND, TOUCH]
    reps = [i for i in edl["inserts"] if i["id"].startswith("rep")]
    assert len(reps) == 4
    assert all(i["at_output_s"] == 1.84 and i["source_start_s"] == 100.69
               and i["duration_s"] == 1.84 and i["asset_key"].startswith("originals/")
               for i in reps)
    # the program's own 9:16 window at x = 0.79 of a 16:9 frame
    x0, y0, x1, y1 = reps[0]["crop"]
    assert reps[0]["fit"] == "pad" and y0 == 0.0 and y1 == 1.0
    assert abs((x1 - x0) - (9 / 16) / (16 / 9)) < 1e-3 and x1 <= 1.0
    # part 1 = 5 x 1.84 s = 9.2 s >= the 8.2 s asked
    assert "PART CHECK: part 1 runs 9.20s" in res
    assert agent_tools.program_duration(edl) == pytest.approx(11.53, abs=0.02)


def test_a_later_keep_list_drops_the_earlier_repeats(monkeypatch):
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    ctx = _Ctx()
    agent_tools.keep_segments(ctx, [STAND] * 3 + [TOUCH])
    res = agent_tools.keep_segments(ctx, [STAND, TOUCH])
    assert "Removed the 2 repeat(s)" in res
    assert not ctx.latest_edl()["json"]["inserts"]


def test_part_check_names_a_short_first_part(monkeypatch):
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    ctx = _Ctx(request=ASK_2670A)
    res = agent_tools.keep_segments(ctx, [[100.8, 104.2], [163.0, 166.6]])
    assert "PART CHECK: part 1 runs only 3.40s" in res and "2 more" in res


# ---- #54 part lengths --------------------------------------------------------

def test_part_lengths_are_not_a_total():
    for ask in (ASK_2670A, ASK_2670B):
        got = quality_verifier.requested_part_lengths(ask)
        assert got["parts"] == [8.2, None] and got["min_total_s"] == 9.2
        # the old code read ASK_2670B as "about 8.2 s" for the whole video
        assert quality_verifier.requested_duration_target(ask) is None
    assert "NOT the total" in quality_verifier.part_lengths_line(ASK_2670A)
    assert quality_verifier.requested_part_lengths("make it 30 seconds") is None
    assert quality_verifier.requested_duration_target(
        "make it 30 seconds")["target_s"] == 30.0
    assert quality_verifier.requested_part_lengths(
        "3 seconds of the crowd, then 5 seconds of the goal")["parts"] == [3.0, 5.0]
    assert quality_verifier.requested_part_lengths("trim the first 5 seconds") is None


def test_a_programme_shorter_than_its_parts_is_a_finding():
    edl = {"keep": [[100.8, 104.2], [112.5, 113.2], [163.0, 166.6]]}   # v3, 7.7 s
    keys = [f["key"] if isinstance(f, dict) and "key" in f else
            (f.get("code") if isinstance(f, dict) else None)
            for f in quality_verifier._request_findings(edl, ASK_2670A)]
    assert "requested_part_lengths_short" in json.dumps(
        quality_verifier._request_findings(edl, ASK_2670A))
    ok = {"keep": [[100.69, 102.53], [164.25, 166.58]],
          "inserts": [{"duration_s": 1.84}] * 4}
    assert "requested_part_lengths_short" not in json.dumps(
        quality_verifier._request_findings(ok, ASK_2670A))


# ---- #52 First cut on sparse speech -----------------------------------------

WORDS_2669 = [{"w": w, "t0": t, "t1": t + 0.2} for w, t in (
    ("Seem", 1.33), ("like", 1.91), ("they", 2.09), ("will", 2.29), ("do", 2.47),
    ("feel", 4.01), ("a", 4.33), ("bit", 4.53), ("alive", 4.75))]


def test_nine_words_in_39_seconds_is_sparse_speech():
    assert first_cut.sparse_speech({"words": WORDS_2669}, 39.19)
    talk = [{"w": "word", "t0": i * 0.4, "t1": i * 0.4 + 0.3} for i in range(150)]
    assert not first_cut.sparse_speech({"words": talk}, 60.0)


def test_a_short_source_never_loses_most_of_it_without_a_reason():
    assert first_cut.kept_share_restore([[1.28, 2.8], [3.92, 5.76]], 39.19) \
        == [[0.0, 39.19]]
    assert first_cut.kept_share_restore([[0, 30]], 39.19) is None
    assert first_cut.kept_share_restore([[1, 5]], 39.19, {"how": "motion"}) is None
    assert first_cut.kept_share_restore([[100, 160]], 600.0) is None


def test_the_hook_is_never_the_first_words_of_a_longer_sentence():
    rows = [{"text": "Seem like they will do feel a bit alive"}]
    assert first_cut._opening_hook(rows) is None
    assert first_cut._opening_hook(
        [{"text": "Python is easy, once you know these three tricks today"}]) \
        == "*Python* is easy"


# ---- user 2665: music on request --------------------------------------------

CAT = [{"slug": f"{m}-{i}", "title": f"{m.title()} Song {i}", "mood": m}
       for m in ("chill", "ambient", "upbeat", "cinematic") for i in (1, 2)]


def test_a_music_ask_is_recognised_and_matched_to_moods():
    assert music_offer.wants_music(ASK_2665)
    assert not music_offer.wants_music("remove the music and add captions")
    assert not music_offer.wants_music("make the captions bigger")
    picks = music_offer.offer_tracks(ASK_2665, CAT)
    assert 2 <= len(picks) <= 4
    assert {t["mood"] for t in picks} <= {"chill", "ambient", "upbeat"}


def test_the_turn_ends_on_a_choice_not_on_silence(monkeypatch):
    import music_gate
    import music_library
    monkeypatch.setattr(music_library, "catalog", lambda: CAT)
    monkeypatch.setattr(music_library, "used_by_user", lambda ctx: set())
    opened = {}
    monkeypatch.setattr(music_gate, "open_offer",
                        lambda ctx, q: opened.setdefault("q", q) or {"slugs": ["x"]})

    class C:
        def latest_edl(self):
            return {"json": {"music": [{"storage_key": "k", "mute": True}]}}

    text = music_offer.turn_end_offer(C(), ASK_2665)
    assert text.startswith("For the music, pick one") and opened["q"] == text
    assert "Chill Song 1" in text

    class WithMusic:
        def latest_edl(self):
            return {"json": {"music": [{"storage_key": "k"}]}}

    assert music_offer.turn_end_offer(WithMusic(), ASK_2665) == ""


# ---- #56 stacked looks on dark footage --------------------------------------

def _dark_hud_frame(tmp_path):
    """A dark game frame (luma ~0.12) with a bright HUD strip."""
    out = str(tmp_path / "dark.png")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=0x1c2028:s=320x180,drawbox=x=10:y=150:w=120:h=20:color=0xd8d0c0:t=fill,"
         "drawbox=x=200:y=40:w=60:h=60:color=0x8a3020:t=fill",
         "-frames:v", "1", out], check=True)
    return out


def test_the_2672_stack_is_measured_too_dark_and_tempered_until_it_reads(tmp_path):
    import renderer
    raw = _dark_hud_frame(tmp_path)
    edl = {"keep": [[0, 10]], "effects": {
        "grade": "cinematic",
        "grade_custom": {"contrast": 1.12, "shadows": -0.1},
        "stylize": [{"kind": "vignette", "intensity": 0.3},
                    {"kind": "grain", "intensity": 0.15}]}}
    src = [look_guard.frame_stats(raw)]

    def measure(e):
        return look_guard.compare(src, [look_guard.frame_stats(
            raw, ",".join(renderer.cutaway_look_chain(e, 1.0, 0.04)))])

    natural = look_guard.wants_natural(ASK_2672)
    assert natural
    r = measure(edl)
    assert look_guard.verdict(r, natural)          # the shipped look fails
    cur = edl
    for step in (0, 1, 3, 2, 2, 2, 2, 3, 2):
        nxt, _note = look_guard.temper_step(cur, step, r, natural)
        if nxt is None:
            continue
        cur, r = nxt, measure(nxt)
        if not look_guard.verdict(r, natural):
            break
    assert not look_guard.verdict(r, natural)
    assert cur["effects"]["grade"] == "cinematic"   # the look is kept, eased
    assert cur["effects"]["stylize"][0]["intensity"] <= 0.12


def test_a_light_grade_passes_untouched(tmp_path):
    import renderer
    raw = _dark_hud_frame(tmp_path)
    edl = {"keep": [[0, 10]], "effects": {"grade_custom": {"saturation": 1.1}}}
    r = look_guard.compare([look_guard.frame_stats(raw)], [look_guard.frame_stats(
        raw, ",".join(renderer.cutaway_look_chain(edl, 1.0, 0.04)))])
    assert not look_guard.verdict(r, True)


def test_the_offer_is_the_replys_one_closing_question():
    out = music_offer.append_offer(
        "The grade is on and the audio muted. Want a title over the opening next?",
        "For the music, pick one ...")
    assert out == ("The grade is on and the audio muted.\n\n"
                   "For the music, pick one ...")
    assert not music_offer.wants_music("cut every shot on the beat")


def test_an_asked_for_dark_look_is_judged_by_its_own_limits():
    assert look_guard.wants_dark("make it dark and moody")
    assert not look_guard.wants_dark(ASK_2672)        # 'natural and clear' wins
    r = {"luma": 0.65, "bright": 0.8, "crush": 0.12, "sat": 1.0}
    assert look_guard.verdict(r, False)               # unasked: too dark
    assert not look_guard.verdict(r, False, dark=True)


def test_a_replay_follows_the_programs_follow_path_and_never_repeats_speech(monkeypatch):
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    frame = {"mode": "crop", "ratio": "9:16", "focus_x": 0.5,
             "follow": [{"t0": 100.0, "t1": 104.0,
                         "k": [[100.0, 0.2, 0.5], [104.0, 0.6, 0.5]]}]}
    ctx = _Ctx(frame)
    ctx.index = {"words": [{"w": "first", "t0": 101.0, "t1": 101.3},
                           {"w": "touch", "t0": 101.4, "t1": 101.7}]}
    res = agent_tools.keep_segments(ctx, [STAND, STAND, TOUCH])
    rep = [i for i in ctx.latest_edl()["json"]["inserts"] if i["id"].startswith("rep")][0]
    x0, _y0, x1, _y1 = rep["crop"]
    centre = (x0 + x1) / 2          # the path at 101.61 s: 0.2 + 0.4 * 1.61 / 4
    assert abs(centre - 0.361) < 0.01
    assert rep["mute"] is True and "play SILENT" in res


class _NoChangeCtx(_Ctx):
    def write_edl(self, edl, desc):
        if edl == self.versions[-1]["json"]:
            return f"NO CHANGE — the EDL is identical to v{self.versions[-1]['version']}"
        return super().write_edl(edl, desc)


def test_repeats_land_when_the_keep_list_itself_is_unchanged(monkeypatch):
    # replay seen live (2670b, after): the same keep with a repeat added came
    # back "NO CHANGE" and the repeat was lost
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    ctx = _NoChangeCtx()
    agent_tools.keep_segments(ctx, [STAND, TOUCH])
    res = agent_tools.keep_segments(ctx, [STAND, STAND, TOUCH])
    assert res.startswith("EDL v") and "REPEATS KEPT" in res
    assert len(ctx.latest_edl()["json"]["inserts"]) == 1
    again = agent_tools.keep_segments(ctx, [STAND, STAND, TOUCH])
    assert again.startswith("NO CHANGE") and "WARNING" not in again


def test_the_offer_replaces_a_no_music_line():
    reply = ("I muted the original audio, so the clip now plays silently. A "
             "cinematic grade gives it a cooler look. I didn't add music this "
             "time; upload a track you like and I'll put it in. I didn't change "
             "the angle this time. It's playing in the player.")
    out = music_offer.append_offer(reply, "For the music, pick one ...")
    assert "didn't add music" not in out and "didn't change the angle" in out
    assert "look. I didn't change the angle this time. It's" in out
    tight = music_offer.append_offer(
        "The picture stays readable. I didn't add music this time; upload a "
        "track you like and I'll put it in. I didn't change the angle.", "Pick:")
    assert "readable. I didn't change the angle." in tight
    assert out.endswith("For the music, pick one ...")


def test_a_loop_never_crosses_a_camera_cut(monkeypatch):
    # 2670 replay: [151.5, 152.6] x8 spanned a chapter card (cut at 151.93)
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    ctx = _Ctx()
    ctx.index = {"words": [], "video": {"fps": 30},
                 "shots": [{"start": 0.0}, {"start": 151.93}, {"start": 160.0}]}
    res = agent_tools.keep_segments(ctx, [[151.5, 152.6]] * 3 + [[153.33, 156.67]])
    edl = ctx.latest_edl()["json"]
    assert edl["keep"][0] == [151.96, 152.6]
    reps = [i for i in edl["inserts"] if i["id"].startswith("rep")]
    assert len(reps) == 2 and all(i["source_start_s"] == 151.96 for i in reps)
    assert "LOOP ON ONE SHOT" in res


def test_new_footage_gets_the_crop_aimed_at_it(monkeypatch):
    # 2670 replay (r3): the loop stood outside the First cut's 9:16 window
    import first_cut_visual
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    seen = {}

    def spans(index, path, keep, ww):
        seen["keep"] = keep
        return [{"t0": s, "t1": e, "k": [[round((s + e) / 2, 3), 0.22, 0.5]]}
                for s, e in keep]
    monkeypatch.setattr(first_cut_visual, "action_spans", spans)
    frame = {"mode": "crop", "ratio": "9:16", "focus_x": 0.54,
             "follow": [{"t0": 0.0, "t1": 30.0, "k": [[10.0, 0.5, 0.5]]}]}
    ctx = _Ctx(frame)
    ctx.index = {"words": [], "video": {"width": 1920, "height": 1080, "fps": 30}}
    ctx.proxy_path = lambda: "/nonexistent/proxy.mp4"
    res = agent_tools.keep_segments(ctx, [[112.8, 115.62]] * 3 + [[157.83, 161.0]])
    assert "CROP RE-AIMED" in res
    assert seen["keep"] == [(112.8, 115.62), (157.83, 161.0)]
    edl = ctx.latest_edl()["json"]
    assert [round(sp["t0"], 2) for sp in edl["frame"]["follow"]] == [112.8, 157.83]
    rep = [i for i in edl["inserts"] if i["id"].startswith("rep")][0]
    x0, _y0, x1, _y1 = rep["crop"]
    assert abs((x0 + x1) / 2 - 0.22) < 0.01      # the replay sits on the aim too


def test_a_repeat_is_told_as_a_repeat_never_as_b_roll():
    import followup_recipes
    import trial_reply
    before = {"keep": [[0.0, 30.0]], "inserts": []}
    after = {"keep": [list(STAND), list(TOUCH)], "inserts": [
        {"id": f"rep{n}", "asset_key": "originals/1/a.mp4", "kind": "video",
         "at_output_s": 1.84, "duration_s": 1.84, "source_start_s": 100.69}
        for n in (1, 2)]}
    assert "broll" not in followup_recipes.changed_lanes(before, after)
    assert "cuts" in followup_recipes.changed_lanes(before, after)
    items = trial_reply.friendly_changes(before, after)
    assert "added B-roll" not in items
    assert "repeated a part to fill its length" in items
