"""REAL_USER_BRUTE (31 Oct): replies real trial users read in the Studio
(local journeys over the real UI, and production project 5270). Each one
carried the editor's own business into the user's reply."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import trial_reply as tr                                       # noqa: E402

CAP = {"mode": "from_transcript", "style": {"preset": "reels", "size": "m"}}
BEFORE = {"keep": [[0.0, 19.0]], "captions": CAP}
AFTER = {"keep": [[0.0, 19.0]], "captions": {"mode": "from_transcript",
                                              "style": {"preset": "reels", "size": "xl"}}}


def facts(user, **kw):
    d = dict(before=BEFORE, after=AFTER, wrote=True, previewed=True, duration_s=19.0,
             frame="vertical", index={"words": []}, user_text=user)
    d.update(kw)
    return tr.Facts(**d)


def test_a_line_pointing_back_at_a_dropped_finding_goes_with_it():
    draft = ("The captions are now extra-large, and the 19-second vertical cut is ready. "
             "Two framing findings remain open: the speaker's face comes close to the left edge "
             "around 5.8-7.2 s and the right edge around 11.8-12.2 s. "
             "I did not repair those, so this version is not ready to hand off as final.")
    out, rep = tr.finish(draft, facts("make the captions bigger"))
    assert "those" not in out and "hand off" not in out, out
    assert out.startswith("The captions are now extra-large"), out
    assert {d["why"] for d in rep["dropped"]} >= {"internal"}


def test_the_hand_off_contract_never_reaches_the_user():
    draft = ("I've cut the boring parts: the edit is now 19 seconds, with the pauses and the "
             "stutter at the start removed. It is not ready to hand off yet.")
    out, _ = tr.finish(draft, facts("cut the boring parts", before={"keep": [[0.0, 3.0], [3.4, 19.5]]},
                                    after={"keep": [[0.2, 3.0], [3.4, 19.5]]}))
    assert "hand off" not in out and out.startswith("I've cut the boring parts"), out


def test_an_aside_about_what_the_user_never_asked_for_is_dropped():
    draft = ("Done: I restyled the captions, and it's now a 19-second vertical cut. "
             "I also kept the B-roll still's gentle Ken Burns move, which is clean for a cutaway.")
    out, rep = tr.finish(draft, facts("make the captions bigger"))
    assert "Ken Burns" not in out, out
    assert any(d["why"] == "status" for d in rep["dropped"])
    draft = ("Done: I restyled the captions, and it's now a 19-second cut. The captions remain "
             "at 2.3x, and the opening zoom and stacked blur effects from the first cut are unchanged.")
    out, _ = tr.finish(draft, facts("make the captions bigger"))
    assert "2.3x" not in out and "unchanged" not in out, out


def test_an_honest_limitation_about_the_ask_itself_stays():
    draft = ("The opening title is unchanged, because my size change never reached the edit.")
    out, _ = tr.finish(draft, facts("make the opening title a little bigger", wrote=False,
                                    after=BEFORE))
    assert "opening title is unchanged" in out, out


def test_centisecond_timestamps_are_the_editor_talking_to_itself():
    draft = ("I'd end that graphic at 21.07s so the words get their own caption, and enlarge "
             "the opening title once its text is available to edit. Want it tighter next?")
    out, _ = tr.finish(draft, facts("make the opening title a little bigger", wrote=False,
                                    after=BEFORE))
    assert "21.07" not in out, out


def test_named_things_after_a_drop_are_not_orphans():
    draft = ("Two findings remain open on the framing. The captions are now extra-large, and "
             "those words read easily on a phone.")
    out, _ = tr.finish(draft, facts("make the captions bigger"))
    assert "those words read easily" in out, out


def test_a_cut_that_got_longer_is_never_told_as_trimmed():
    draft = ("The cut is now 20.35 seconds, with the dead pauses trimmed and the hook title "
             "and captions kept.")
    f = facts("cut the boring parts", before={"keep": [[0.0, 10.0], [12.0, 20.9]]},
              after={"keep": [[0.0, 10.0], [11.0, 21.35]]}, duration_s=20.35)
    assert tr.unbacked(tr.sentences(draft)[0], f) == "longer"
    out, _ = tr.finish(draft, f)
    assert "trimmed" not in out, out


def test_a_new_subscribers_reply_is_finished_but_may_talk_about_its_export():
    draft = ("The captions are now twice their previous size. The 19-second cut passes the "
             "current preview. I left the opening 'But' fragment and the sagging middle section "
             "unchanged. Your full-quality MP4 is ready to download.")
    f = facts("make the captions bigger", subscriber=True)
    out, rep = tr.finish(draft, f)
    assert "passes the current preview" not in out and "sagging" not in out, out
    assert "ready to download" in out, out
    trial_out, _ = tr.finish(draft, facts("make the captions bigger"))
    assert "ready to download" not in trial_out


def test_the_agent_loop_finishes_new_subscriber_replies_too():
    src = open(os.path.join(os.path.dirname(HERE), "agent_loop.py"), encoding="utf-8").read()
    assert "subscriber=_new_sub" in src
    assert 'getattr(ctx, "claude_paid", None) is not None' in src


def test_a_quoted_title_with_its_own_question_mark_stays_one_sentence():
    draft = ("Your cut stays as it is, a 17-second edit: it already does what you asked. "
             "The video already opens with a title, \"NEW TO PYTHON? START HERE\", over the first "
             "two seconds, so I didn't add a second one.")
    parts = tr.sentences(draft)
    assert len(parts) == 2 and "\"NEW TO PYTHON? START HERE\"" in parts[1], parts
    out, _ = tr.finish(draft, facts("add a title at the start", wrote=False, after=BEFORE,
                                    duration_s=17.0, previewed=True))
    assert out.index("NEW TO PYTHON? START HERE") > out.index("already does"), out
    assert not out.startswith("START HERE"), out


def test_the_editors_knobs_and_its_fallbacks_stay_out_of_the_reply():
    draft = ("I made the captions larger (caption scale 1.6 to 2.0) with no change to their timing "
             "or wording. The three B-roll stills also play as slow Ken Burns moves because no "
             "depth was measured for them.")
    out, _ = tr.finish(draft, facts("make the captions bigger"))
    assert "scale 1.6" not in out and "Ken Burns" not in out and "depth" not in out, out
    assert out.startswith("I made the captions larger with no change"), out
    draft = ("Done: I reframed it to 9:16, and it's now a 25-second vertical cut. My re-aim of "
             "those spans was rejected, so the current version keeps that framing.")
    out, _ = tr.finish(draft, facts("add a title at the start"))
    assert "rejected" not in out and "re-aim" not in out, out
    draft = ("The final render passed verification, except the still at 7.3-8.1s, which I "
             "justified rather than fixed. The cut is tighter at 12.1 seconds.")
    out, _ = tr.finish(draft, facts("make it punchier", subscriber=True, duration_s=12.1,
                                    before={"keep": [[0.0, 12.7]]}, after={"keep": [[0.0, 12.1]]}))
    assert "justified" not in out and "verification" not in out, out


def test_a_new_subscribers_verifies_line_and_multiplier_are_dropped():
    draft = ("I made the captions bigger (2.0× the previous size) and re-rendered. The 19-second "
             "vertical cut now verifies, with one exception.")
    out, _ = tr.finish(draft, facts("make the captions bigger", subscriber=True))
    assert "×" not in out and "verifies" not in out, out
    assert out.startswith("I made the captions bigger and re-rendered."), out


def test_bigger_is_never_claimed_for_a_title_that_got_smaller():
    mg = lambda size: {"keep": [[0.0, 19.0]], "motion": [{"id": "mg1", "template": "hook_title",
                                                         "params": {"text": "X", "size": size}}]}
    draft = "I made the opening title bigger and matched its accent to the short's yellow."
    f = facts("make the opening title a little bigger", before=mg(1.3), after=mg(1.25))
    assert tr.unbacked(draft, f) == "smaller"
    f = facts("make the opening title a little bigger", before=mg(1.3), after=mg(1.5))
    assert tr.unbacked(draft, f) is None
