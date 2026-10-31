"""Q8 (30 Oct): the trial reply says only what the turn's facts back, short
and warm. The cases are real trial replies from the harness replays (148
Haiku trial turns, .valmera/week-1029/harness/q8/replay_cases.json; names
and sites kept, they are public fixtures), each with the facts its turn
recorded: the EDL before and after, the preview, the transcript.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import trial_reply as tr                                       # noqa: E402

WORDS = ("we did that on purpose so we could see how the engine behaves "
         "deep learning was working and the world didn't accept this at all "
         "where i'd start with python the tutorial is the place to begin "
         "since we're a trillionth of the sun's energy it will solve "
         "everything let me let let me find the uh over here").split()
INDEX = {"words": [{"w": w, "t0": i * 0.3, "t1": i * 0.3 + 0.25}
                   for i, w in enumerate(WORDS)]}
FIRST_CUT = {"keep": [[0.0, 27.2]], "captions": {"mode": "from_transcript",
                                                  "style": {"preset": "reels"}},
             "motion": [{"id": "mg1", "template": "hook_title",
                         "params": {"text": "Where to *start* Python"}}],
             "effects": {"zooms": [{"id": "z1"}]}}


def facts(**kw):
    base = dict(before=FIRST_CUT, after=FIRST_CUT, wrote=False,
                previewed=True, duration_s=27.2, frame="vertical",
                index=INDEX, user_text="make it a vertical short with captions")
    base.update(kw)
    return tr.Facts(**base)


def dropped(reply, f):
    _text, report = tr.finish(reply, f)
    return [d["why"] for d in report["dropped"]]


# ── unbacked claims from the replays ─────────────────────────────────────

def test_vertical_claimed_for_a_landscape_screen_recording():
    # q3cH-vfr-r1: an 18 s, 1280x800 cut called "vertical"
    f = facts(frame="landscape", duration_s=18.2,
              user_text="clean up my footage")
    text, report = tr.finish(
        "Your footage is cleaned up into an 18-second vertical cut: pauses "
        "and the \"um\" are removed, the hook title and captions are on.", f)
    assert [d["why"] for d in report["dropped"]] == ["shape"]
    assert "vertical" not in text.lower()
    # the same cut described as what it is stands (QI2: its pixel size is
    # scrubbed, never the sentence: a reply states no pixel sizes)
    text, report = tr.finish("Your footage is now an 18-second landscape cut "
                             "at the original 1280x800 shape.", f)
    assert [d["why"] for d in report["dropped"]] == ["pixels"]
    assert "18-second landscape cut at the original shape" in text


def test_a_denial_on_a_turn_that_wrote():
    # c2mY-vlog-r1: "Nothing was changed this turn." after 16 s were cut
    after = dict(FIRST_CUT, keep=[[0.0, 11.2]])
    f = facts(after=after, wrote=True, duration_s=11.2,
              user_text="clean up my footage")
    text, report = tr.finish("Nothing was changed this turn.", f)
    assert [d["why"] for d in report["dropped"]] == ["deny"]
    assert text.startswith("Done: I tightened it by 16 seconds")
    assert "It's playing in the player." in text
    assert dropped("Upgrade to start editing.", f) == ["deny"]


def test_the_trial_note_told_as_the_users_own_instruction():
    # q3dS-talk10best-r1 / q8b vlog: "as you asked", "as the trial note asked"
    f = facts(user_text="find the best moment and make a short")
    for line in ("I left the framing, title and big type as they were, as you asked.",
                 "I left it there on purpose, because this turn's instructions kept the title in place.",
                 "I left the framing alone as instructed.",
                 "The graphics were locked as part of the first cut, so I left them unchanged."):
        assert dropped(line, f) == ["attributed"], line
    assert dropped("I left the framing alone, as the trial note asked, so that "
                   "finding remains open.", f) == ["internal"]
    # the user really did ask to keep it
    keep = facts(user_text="keep the framing, just add captions")
    assert dropped("I kept the framing as you asked.", keep) == []


def test_a_render_denied_that_happened_and_one_claimed_that_did_not():
    # c2bS-game-r1: "I didn't render a preview" with the v4 draft on screen
    f = facts(wrote=True, after=dict(FIRST_CUT, keep=[[0.0, 23.0]]),
              duration_s=23.0, user_text="make a highlight")
    assert dropped("I didn't render a preview or check the frames, so I "
                   "haven't seen the result.", f) == ["render_deny"]
    nothing = facts(previewed=False)
    assert dropped("The new preview is ready on the right.", nothing) == ["render"]
    text, _ = tr.finish("Your cut stays the same.", nothing)
    assert "playing" not in text


def test_an_invented_name_and_a_quote_the_speaker_never_said():
    # c2mY-talk10best-r1: "a Sam Altman clip" (the name is not in the talk)
    f = facts()
    assert dropped("The 48-second short is finished: a Sam Altman clip "
                   "about deep learning working.", facts(duration_s=47.7)) == ["name"]
    assert dropped("It opens with him saying \"the robots will take every "
                   "job tomorrow\".", f) == ["quote"]
    # real speech, a stutter quoted with commas, the edit's own title text
    assert dropped("It ends on \"the world didn't accept this at all.\"", f) == []
    assert dropped("I removed the stutter (\"let me, let me\") he says.", f) == []
    assert dropped("It opens on \"Where to start Python\" with captions.", f) == []
    # R2's pattern paired the closing mark of "I was" with the next one
    assert tr.quotes('starting the hook on "I was" and ending on "...this '
                     'at all."') == ["I was", "...this at all."]


def test_a_capability_the_editor_has_and_an_export_it_did_not_make():
    f = facts()
    assert dropped("The canvas is fixed at 9:16, so I couldn't export a "
                   "16:9 version.", f) == ["capability"]
    assert dropped("Your video is ready to download.", f) == ["export"]
    assert dropped("I've exported the final MP4.", f) == ["export"]


def test_a_length_that_is_not_the_videos():
    f = facts(duration_s=27.2, wrote=True,
              after=dict(FIRST_CUT, keep=[[0.0, 27.2], [28.0, 28.1]]))
    assert dropped("The short is now a 15-second cut.", f) == ["length"]
    assert dropped("The vertical short is ready at 27 seconds.", f) == []
    # a moment inside the video is not its length
    assert dropped("I fixed the doubled \"we we\" at 21.3 s.", f) == []


def test_an_effect_asked_for_that_did_not_change():
    # "make the captions bigger" answered "I made the captions bigger" while
    # the captions are exactly the First cut's
    f = facts(wrote=True, after=dict(FIRST_CUT, keep=[[0.0, 26.0]]),
              duration_s=26.0, user_text="make the captions bigger")
    assert dropped("I made the captions bigger and bolder.", f) == \
        ["unchanged:captions"]
    # describing what the video has, unasked, is not a claim of this turn
    g = facts(user_text="make a highlight", wrote=True,
              after=dict(FIRST_CUT, keep=[[0.0, 26.0]]), duration_s=26.0)
    assert dropped("I've made it a vertical 9:16 short with captions and a "
                   "hook title.", g) == []
    # an effect the video does not have at all
    assert dropped("I added background music under the voice.", g) == \
        ["absent:music"]
    # a first-person change on a turn that changed nothing
    assert dropped("I've made it a vertical 9:16 short.",
                   facts(user_text="make a highlight")) == ["change"]


# ── short, warm, specific ────────────────────────────────────────────────

REVIEW_LOG = (
    "I made it a reel: a 27-second vertical cut with a bold hook and "
    "captions on each phrase. In the latest preview, the speaker stays in "
    "frame and the hook title no longer covers his mouth. I could not clear "
    "two checks, so the last preview still has a one-frame jump at 22.2s and "
    "two frames of stutter at 24.8s. Not improved yet (review note, not a "
    "blocker): picture QC: ORPHAN FRAMES at 24.80s. Press Improve it to keep "
    "refining, or export this version as it is. Sorry about that.")


def test_a_review_log_becomes_what_changed_where_to_look_and_one_next_step():
    f = facts(user_text="make this a reel with a strong hook and captions",
              wrote=True, after=dict(FIRST_CUT, keep=[[0.0, 27.0]]),
              duration_s=27.0)
    text, report = tr.finish(REVIEW_LOG, f)
    assert text.startswith("I made it a reel: a 27-second vertical cut")
    for gone in ("QC", "review note", "Improve it", "Sorry", "22.2s", "checks"):
        assert gone not in text, gone
    assert "It's playing in the player." in text
    assert text.rstrip().endswith("?")
    assert len(tr.sentences(text)) <= 4 and len(text.split()) <= tr.MAX_WORDS


def test_one_limit_at_most_and_the_drafts_own_offer():
    f = facts()
    text, _ = tr.finish(
        "The short is ready at 27 seconds. I couldn't find B-roll of the "
        "factory. I couldn't find B-roll of the rocket either. Want me to "
        "add a title card instead?", f)
    assert text.count("couldn't") == 1
    assert text.endswith("Want me to add a title card instead?")


def test_another_language_gets_no_english_and_policy_offers_are_dropped():
    f = facts(user_text="Acesse o site e produza um video")
    text, report = tr.finish(
        "Editei o vídeo de 38 s em 9:16 com a identidade azul do site. "
        "Se você me disser qual das três trilhas CC0 quer, eu a coloco.", f)
    assert "playing" not in text and "Want" not in text
    assert text.startswith("Editei o vídeo de 38 s")
    assert "music_pick" in [d["why"] for d in report["dropped"]]
    assert tr.language("The short is ready and it is playing.") == "en"


def test_a_suggestion_fits_what_the_video_has():
    silent = facts(index={"words": []}, user_text="make a highlight")
    assert "caption" not in tr.suggestion(silent).lower()
    no_caps = facts(after={"keep": [[0, 10]]})
    assert tr.suggestion(no_caps) == "Want captions on it next?"


def test_an_article_before_eighteen():
    f = facts(wrote=True, after=dict(FIRST_CUT, keep=[[0.0, 18.2]]),
              duration_s=18.2, frame="landscape", user_text="clean up")
    text, _ = tr.finish("", f)
    assert "an 18-second" in text and "a 18" not in text


# ── only the trial lane (D1) ─────────────────────────────────────────────

def test_only_the_trial_lane_reaches_the_guard():
    src = open(os.path.join(os.path.dirname(HERE), "agent_loop.py")).read()
    tree = ast.parse(src)
    sites = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and n.attr == "finish_for_ctx"]
    assert len(sites) == 1, "the guard's call site moved"
    line = sites[0].lineno
    guards = [n for n in ast.walk(tree) if isinstance(n, ast.If)
              and n.lineno <= line <= n.end_lineno]
    inner = min(guards, key=lambda n: n.end_lineno - n.lineno)
    test = ast.get_source_segment(src, inner.test)
    assert "_trial_lane(ctx)" in test and "CLAUDE_TRIAL_REPLY_GUARD" in test


def test_the_guard_never_loses_a_reply(monkeypatch):
    class Ctx:
        def latest_edl(self):
            raise RuntimeError("db down")
    monkeypatch.setattr(tr, "finish", lambda d, f: (_ for _ in ()).throw(ValueError("x")))
    assert tr.finish_for_ctx(Ctx(), "draft reply") == ("draft reply", None)


def test_a_trial_turn_always_ends_on_an_exportable_version():
    """repair_required showed a trial user "This edit needs another pass" and
    no Download (harness, 30 Oct): the trial lane's notes advise instead.
    Only the trial lane sets it before the final hand-off."""
    src = open(os.path.join(os.path.dirname(HERE), "agent_loop.py")).read()
    tree = ast.parse(src)
    sets = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and ast.get_source_segment(src, n) == "ctx.critics_advisory = True"]
    guarded = []
    for node in sets:
        ifs = [i for i in ast.walk(tree) if isinstance(i, ast.If)
               and i.lineno <= node.lineno <= i.end_lineno]
        inner = min(ifs, key=lambda i: i.end_lineno - i.lineno) if ifs else None
        if inner is not None and ast.get_source_segment(src, inner.test) == "_trial_lane(ctx)":
            guarded.append(node)
    assert len(guarded) == 1


def test_the_advisory_hand_off_opens_the_export(monkeypatch):
    import types
    import agent_loop
    monkeypatch.setattr(agent_loop, "_strict_quality_handoff", lambda c: {
        "quality_status": "repair_required", "export_ready": False,
        "quality_findings": ["picture QC: FACE AT THE LEFT EDGE 11.8-13.2s"]})
    monkeypatch.setattr(agent_loop, "_open_request_findings", lambda c, row=None: [])
    monkeypatch.setattr(agent_loop, "_safety_hold", lambda c: False)
    out = agent_loop._quality_handoff(types.SimpleNamespace(critics_advisory=True))
    assert out["quality_status"] == "advisory" and out["export_ready"] is True


def test_where_to_look_is_said_once():
    # q8d phonehook (30 Oct): "Look at the player to see it." then the
    # added "It's playing in the player."
    text, _ = tr.finish("Your reel is now a 27-second vertical 9:16 edit with "
                        "captions. Look at the player to see it.", facts())
    assert text.count("player") == 1


def test_a_cutaways_own_title_is_not_an_invented_name_and_a_pick_is_not_the_users():
    # q8b phone r4: "cutaways of ... the Kansas City skyline": the stock
    # photo's own title; q8b talk10best r1: "the moment you chose" for
    # "find the best moment"
    after = dict(FIRST_CUT, fx=[{"id": "br1", "type": "broll",
                                 "credit": {"title": "Kansas City skyline at dusk"}}])
    f = facts(after=after, wrote=True, duration_s=27.2)
    assert dropped("It has cutaways of the sun and the Kansas City skyline.", f) == []
    g = facts(user_text="find the best moment and make a short")
    assert dropped("Your short is about the moment you chose: the scaling argument.", g) \
        == ["attributed"]


def test_a_year_the_cut_never_says():
    # q8d talk10best (the judge caught it): "the 2015 bet" on a cut whose
    # kept speech never says 2015 (it was in the part left out)
    words = ("in 2015 when you co founded it most people said no " * 3).split() + \
            ("deep learning was working and in 2005 a professor said it would never work " * 3).split()
    idx = {"words": [{"w": w, "t0": i * 0.3} for i, w in enumerate(words)]}
    kept = dict(FIRST_CUT, keep=[[10.0, 60.0]])
    f = facts(index=idx, after=kept, wrote=True, duration_s=50.0)
    assert dropped("It is built on the 2015 bet on deep learning.", f) == ["year"]
    assert dropped("It ends on the 2005 professor's prediction.", f) == []


def test_a_mute_is_never_called_levelling_the_sound():
    """30 Oct, the first real trial request ("cut out the audio"): the turn
    set the volume to -60 dB and the reply said "levelled the sound"."""
    base = {"segments": [{"start": 0, "end": 10.5}]}
    muted = dict(base, volume=[{"start": 0, "end": 10.54, "gain_db": -60.0}],
                 master={"loudness": "natural"})
    assert tr.friendly_changes(base, muted) == ["muted the original sound"]
    part = dict(base, volume=[{"start": 0, "end": 4, "gain_db": -60.0},
                              {"start": 4, "end": 10.5, "gain_db": 0.0}])
    assert tr.friendly_changes(base, part) == ["muted part of the sound"]
    quieter = dict(base, volume=[{"start": 0, "end": 10.5, "gain_db": -6.0}])
    assert tr.friendly_changes(base, quieter) == ["lowered the sound"]
    assert tr.friendly_changes(muted, base) == ["brought the original sound back"]
    mastered = dict(base, master={"loudness": "social"})
    assert tr.friendly_changes(base, mastered) == ["levelled the sound"]


def test_a_dropped_line_never_hides_an_unmet_ask():
    """Ticket #49 (30 Oct, user 2665): asked for new music and an angle fix;
    the filter dropped "I did not correct the angle ..." (internal) and
    "Valmera doesn't pick music ..." (music_pick) and the reply only said
    "Done" about the grade."""
    base = {"keep": [[0.0, 10.54]]}
    after = dict(base, volume=[{"start": 0, "end": 10.54, "gain_db": -60.0}],
                 master={"loudness": "natural"},
                 effects={"grade": "cinematic"})
    f = facts(before=base, after=after, wrote=True, duration_s=10.6,
              index={"words": []},
              user_text="Cut out the audio and play something else relating to nature "
                        "and outdoors. Give colour gradings cinematic vibes. Correct the "
                        "angle (if possible)")
    text, report = tr.finish(
        "I gave it a cinematic grade and muted the original audio. "
        "I did not correct the angle: the horizon looked level to me and no tool "
        "rotates the picture cleanly. "
        "Valmera doesn't pick music, so tell me which of three CC0 tracks you want.", f)
    # RS_HONESTY_BROLL: the agent's own line is salvaged without its
    # internal clause; music gets the plain line and the choice
    assert "did not correct the angle" in text
    assert "didn't add music" in text
    assert "no tool" not in text and "CC0" not in text
    assert len(report["answered"]) == 2


def test_a_dropped_line_never_hides_a_done_ask():
    """Ticket #49 (30 Oct, user 2666): the only line saying b-roll was added
    was dropped as a review note; the reply only said what was NOT added."""
    base = {"keep": [[0.0, 15.4]], "captions": {"mode": "from_transcript"}}
    after = dict(base, overlays=[{"id": "o1", "start": 1, "end": 4, "fit": "cover"},
                                 {"id": "o2", "start": 4, "end": 7, "fit": "cover"}])
    f = facts(before=base, after=after, wrote=True, duration_s=15.4,
              user_text="automatically add relevant REAL B-roll footage throughout the video")
    text, _report = tr.finish(
        "The 15-second vertical cut is ready with your voice running throughout. "
        "Two Istanbul clips from Pexels cover the spoken \"Istanbul\" at about 1-4 s and 4-7 s. "
        "The speech never mentions Forbes, so I added no footage for it.", f)
    # RS_HONESTY_BROLL: 6 s of a 15.4 s "throughout" is told as part of it
    assert "B-roll over part of the video" in text


def test_asks_already_answered_get_no_extra_line():
    f = facts(user_text="add some music", wrote=False)
    text, report = tr.finish("Your cut is ready. I didn't add music yet. Want a title?", f)
    assert "answered" not in report and text.count("music") == 1
