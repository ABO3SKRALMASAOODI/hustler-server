"""RS_HONESTY_BROLL (31 Oct 2026): the exact production replies of the trial
users 2665, 2666, 2668 and 2675 (30-31 Oct), replayed through the reply
filter with the EDLs their turns wrote.

Ticket #49: the filter dropped the agent's "not done" lines, so 2665 read
"Done: ..." with two of three asks missing, and 2666 never read that b-roll
was added; both jobs said ``fulfilled``. Ticket #51: b-roll asked for
"throughout" covered 1-7 s of a 15.4 s talk and nothing checked it before
the trial's one preview. The drafts are the kept lines of the production
reply plus the sentences the job's honesty.trial_reply_dropped recorded.
"""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import agent_loop  # noqa: E402
import ask_ledger  # noqa: E402
import trial_reply as tr  # noqa: E402

U2665 = ("Cut out the audio and play something else relating to nature and outdoors "
         "and active but chilly vibes. Give colour gradings cinematic vibes . Correct "
         "the angle (if possible) make it looj mire dramatic but not to lose the actual "
         "original outlines .")
U2666 = ("Edit my uploaded video NOW.\n\nThis is a Persian documentary-style talking-head "
         "video.\n\nListen to my original speech and automatically add relevant REAL B-roll "
         "footage throughout the video.\n\nWhen I mention:\n\n* Hamdi Ulukaya: find and show "
         "real footage or authentic photos of him.\n* Chobani: show real footage of the "
         "company, factories or products.\n* Murat Ülker: show authentic footage or "
         "photos of him.\n* Turkish billionaires: show real Istanbul and business footage.\n"
         "* Forbes: show authentic Forbes information.\n\nAutomatically research, select, "
         "download and INSERT these visuals into my existing video at the correct spoken "
         "moments.\n\nKeep my original Persian voice continuously playing. Switch naturally "
         "between my face and the real footage.\n\nDo not use fictional AI-generated people.\n"
         "Do not add subtitles.\nKeep the video vertical, 9:16.\n\nIMPORTANT: Do not give me "
         "instructions or a storyboard. Execute the actual video edits, render the edited "
         "preview and make the finished video ready for MP4 export.")
U2675 = "Make the video clearer and give a cinematic colour grading"

# 2665, job 126663: EDL v1 (First cut) -> v5
B2665 = {"keep": [[0.0, 10.54]]}
A2665 = dict(B2665, volume=[{"start": 0, "end": 10.54, "gain_db": -60.0}],
             effects={"grade": "cinematic", "vignette": 0.3})
D2665 = ("The audio is muted and the picture now has a cinematic grade with a light "
         "vignette, so the outlines stay intact. "
         "I did not correct the angle: the horizon looked level to me and no tool rotates "
         "the picture cleanly. "
         "Valmera doesn't pick music, so the video is silent. "
         "You can add your own nature track and I'll place it.")

# 2666, job 126670: EDL v9 -> v13 (the production overlays: scale 0.4, fit cover)
B2666 = {"keep": [[0.54, 15.98]], "captions": {"mode": "from_transcript"}}


def _ov(i, start, dur=3.0):
    return {"id": f"ov{i}", "kind": "video", "fit": "cover", "scale": 0.4,
            "start": start, "duration_s": dur, "asset_key": f"fetched/5272/stock/{i}.mp4"}


A2666 = dict(B2666, captions=[], overlays=[_ov(1, 1.0), _ov(2, 4.0)])
D2666 = ("The 15-second vertical cut is ready with the original Persian voice running "
         "throughout and captions off. "
         "Two Istanbul clips from Pexels cover the spoken \"Istanbul\" at about 1–4 s "
         "and 4–7 s. "
         "The speech never mentions Hamdi Ulukaya, Chobani, Murat Ülker or Forbes, so I "
         "added no footage for them.")


def facts(**kw):
    base = dict(before={}, after={}, wrote=True, previewed=True, duration_s=None,
                frame="vertical", index={"words": []}, user_text="")
    base.update(kw)
    return tr.Facts(**base)


def test_2665_asks_and_their_states():
    assert ask_ledger.asks_detail(U2665) == [("music", True), ("angle", True), ("grade", False)]
    assert ask_ledger.ledger(U2665, B2665, A2665) == {
        "music": "not_done", "angle": "not_done", "grade": "done"}


def test_2665_reply_never_says_done_and_tells_both_missing_asks():
    f = facts(before=B2665, after=A2665, duration_s=10.6, user_text=U2665)
    text, report = tr.finish(D2665, f)
    assert not text.lower().startswith("done")
    assert "did not correct the angle" in text
    assert "no tool" not in text                      # the internal clause is cut
    assert "didn't add music" in text
    assert "nature track" in text                     # the offer is never dropped for length
    assert report["unmet"] == ["music", "angle"]
    assert "Want a title" not in text                 # the next step is the missing asks


def test_2665_job_outcome_is_partial():
    f = facts(before=B2665, after=A2665, duration_s=10.6, user_text=U2665)
    _text, report = tr.finish(D2665, f)
    honesty = {}
    assert agent_loop._outcome_after_reply("fulfilled", report, honesty) == "partial"
    assert honesty["unmet_asks"] == ["music", "angle"]
    # a turn that met every ask stays fulfilled; a blocked one stays blocked
    assert agent_loop._outcome_after_reply("fulfilled", {"dropped": []}, {}) == "fulfilled"
    assert agent_loop._outcome_after_reply("blocked", report, {}) == "blocked"


def test_2666_subtitles_are_not_an_ask_and_broll_is_throughout():
    assert "captions" not in ask_ledger.asks(U2666)
    assert ask_ledger.broll_throughout(U2666)


def test_2666_two_cover_clips_over_7_of_15s_are_partly_and_said():
    f = facts(before=B2666, after=A2666, duration_s=15.4, user_text=U2666)
    text, report = tr.finish(D2666, f)
    assert report["asks"] == {"broll": "partly"}
    assert report["unmet"] == ["broll"]
    assert "B-roll over part of the video" in text
    assert "Forbes" in text                           # no invented footage, said honestly


def test_2666_covered_throughout_says_added_and_is_met():
    after = dict(B2666, captions=[], overlays=[_ov(1, 1.0), _ov(2, 4.0), _ov(3, 8.5),
                                               _ov(4, 12.0, 2.5)])
    f = facts(before=B2666, after=after, duration_s=15.4, user_text=U2666)
    text, report = tr.finish(D2666, f)
    assert report["asks"] == {"broll": "done"}
    assert "unmet" not in report
    # the agent's own line comes back without its time stamps
    assert 'Two Istanbul clips from Pexels cover the spoken "Istanbul".' in text
    assert "1\u20134" not in text


def test_2666_coverage_counts_cover_overlays_whatever_their_scale():
    import timeline
    total, covered, gaps = ask_ledger.broll_gaps(A2666)
    assert (total, covered) == (15.44, 6.0) and gaps[0] == (7.0, 15.44)
    assert timeline.broll_coverage(A2666) == (15.44, 6.0, (7.0, 15.44))
    assert ask_ledger.broll_shortfall(A2666) == [(7.0, 15.44)]


class _Ctx(SimpleNamespace):
    def latest_edl(self):
        return {"version": 13, "json": self.edl}


WORDS = [{"w": w, "t0": 0.6 + i * 0.35, "t1": 0.9 + i * 0.35}
         for i, w in enumerate(("living in Istanbul you need a residence permit and a "
                                "passport and the migration office takes weeks to answer "
                                "every application for the permit card").split())]


def test_2666_the_hold_fires_once_before_the_trial_preview_with_the_spoken_words():
    ctx = _Ctx(claude_trial={"model": "claude-haiku-5-5"}, claude_paid=None,
               edl=A2666, index={"words": WORDS})
    hold = agent_loop._broll_throughout_hold(ctx, "render_preview", U2666)
    assert hold and hold.startswith("NOT RENDERED YET")
    # no b-roll placed at all (every source failed): no hold, the render goes ahead
    bare = _Ctx(claude_trial={"model": "claude-haiku-5-5"}, claude_paid=None,
                edl=B2666, index={"words": WORDS})
    assert agent_loop._broll_throughout_hold(bare, "render_preview", U2666) is None
    assert "7-15.44s" in hold and "residence" in hold or "permit" in hold
    assert "Never show a person, brand" in hold
    assert agent_loop._broll_throughout_hold(ctx, "render_preview", U2666) is None


def test_the_hold_stays_out_of_the_way():
    trial = {"model": "claude-haiku-5-5"}
    full = dict(A2666, overlays=[_ov(1, 1.0), _ov(2, 4.0), _ov(3, 8.5), _ov(4, 12.0, 2.5)])
    for ctx, name, text in (
            (_Ctx(claude_trial=trial, claude_paid=None, edl=full), "render_preview", U2666),
            (_Ctx(claude_trial=trial, claude_paid=None, edl=A2666), "add_overlay", U2666),
            (_Ctx(claude_trial=trial, claude_paid=None, edl=A2666), "render_preview",
             "add some b-roll of the city at the start"),
            # a grandfathered lane is untouched
            (_Ctx(claude_trial=None, claude_paid=None, edl=A2666), "render_preview", U2666)):
        assert agent_loop._broll_throughout_hold(ctx, name, text) is None


def test_2668_both_after_dropped_lines_goes_with_them():
    """Job 126681: three internal lines were dropped and the reply read "I
    left both alone because you asked only for the look." """
    draft = ("The cinematic edit is saved as v5 at 60.7 seconds, with the grade, fine grain "
             "and light vignette across the whole piece. "
             "The mastered mix now measures -14.8 LUFS, close to the -14 social target. "
             "The v5 render check raised only minor notes: the static framing and two small "
             "background figures at about 0:53. "
             "I left both alone because you asked only for the look.")
    base = {"keep": [[0.0, 60.7]]}
    after = dict(base, effects={"grade": "cinematic", "grain": 0.2, "vignette": 0.3},
                 master={"loudness": "social"})
    f = facts(before=base, after=after, duration_s=60.7, user_text="I want cinmatic")
    text, report = tr.finish(draft, f)
    assert "both" not in text
    assert "unmet" not in report


def test_2675_both_asks_met_and_the_unasked_tail_line_may_go():
    base = {"keep": [[0.0, 37.13]]}
    after = dict(base, effects={"stylize": [{"id": "st2", "kind": "sharpen", "intensity": 0.5}],
                                "grade": "cinematic",
                                "grade_custom": {"contrast": 1.06, "saturation": 0.95}})
    draft = ("I made the picture sharper and denoised, and applied a cinematic grade with "
             "slightly lower saturation and higher contrast. "
             "The 37-second edit is ready, but sharpening can't add resolution, so a "
             "low-resolution source stays soft. "
             "I kept the repeated \"Happy Birthday\" lines because they are the sung lyric, "
             "and I left the roughly 10-second tail after the last word untrimmed.")
    f = facts(before=base, after=after, duration_s=37.2, user_text=U2675)
    text, report = tr.finish(draft, f)
    assert report["asks"] == {"clarity": "done", "grade": "done"}
    assert "unmet" not in report
    assert text.startswith("I made the picture sharper")
    assert agent_loop._outcome_after_reply("fulfilled", report, {}) == "fulfilled"


def test_negated_and_praised_topics_are_not_asks():
    assert ask_ledger.asks("Do not add subtitles. Keep it vertical.") == []
    assert ask_ledger.asks("no music please, just make it cinematic") == []
    assert ask_ledger.asks("the captions look great, now add some b-roll") == ["broll"]
    assert ask_ledger.asks_detail("add music") == [("music", False)]


def test_an_existing_layer_meets_an_add_ask_but_not_a_replace_ask():
    edl = {"keep": [[0, 10]], "music": [{"id": "m1", "asset_key": "lib/a.mp3"}]}
    assert ask_ledger.ledger("add music", edl, edl) == {"music": "done"}
    assert ask_ledger.ledger("play something else, a different song", edl, edl) == {
        "music": "not_done"}


def test_2666_unmet_ask_with_no_write_never_says_it_does_what_you_asked():
    """Candidate replay of 2666 (31 Oct): every b-roll source failed and the
    reply read "Your cut stays as it is ...: it already does what you asked.
    ... I couldn't add the B-roll"."""
    edl = dict(B2666, captions=[])
    draft = ("The Persian transcript has no matches for Hamdi Ulukaya, Chobani, Murat \u00dclker "
             "or Forbes, so I didn't place cutaways for them. "
             "I couldn't add the B-roll, so the cut is still the face-only version.")
    f = facts(before=edl, after=edl, wrote=False, duration_s=12.8, user_text=U2666)
    text, report = tr.finish(draft, f)
    assert "already does what you asked" not in text
    assert "couldn't add the B-roll" in text
    assert report["unmet"] == ["broll"]


def test_2668_a_tenth_of_a_second_off_is_not_tightened_cuts():
    """Candidate replay of 2668 (31 Oct): keep 0-60.7 -> 0-60.57 was told as
    "Done: I tightened the cuts"; the Opus judge called it a false claim."""
    before = {"keep": [[0.0, 60.7]]}
    after = {"keep": [[0.0, 60.57]]}
    changes = tr.friendly_changes(before, after)
    assert "tightened the cuts" not in changes
    assert "trimmed a fraction of a second" in changes


def test_source_broll_fx_cutaways_count_as_broll():
    """Replay check (31 Oct, harness phone fixture on main): source_broll put
    its cutaways in EDL.fx (type broll, program seconds); they are b-roll for
    the ask, the coverage and the hold."""
    import timeline
    base = {"keep": [[0.0, 29.04]]}
    after = dict(base, fx=[{"id": "br1", "type": "broll", "fit": "cover", "stage": "screen",
                            "start": 5.04, "end": 7.14},
                           {"id": "br2", "type": "broll", "fit": "cover", "stage": "screen",
                            "start": 12.0, "end": 15.5}])
    assert ask_ledger.ledger("add some b-roll", base, after) == {"broll": "done"}
    total, covered, _gaps = ask_ledger.broll_gaps(after)
    assert (total, covered) == (29.04, 5.6)
    assert timeline.broll_coverage(after)[1] == 5.6
    assert ask_ledger.ledger("add b-roll throughout the video", base, after) == {"broll": "partly"}


def test_2666_what_was_not_done_stays_beside_what_was():
    """Candidate replay of 2666 (31 Oct, hold fired, two Istanbul cutaways):
    the line answering the user's own list was dropped for length."""
    after = dict(B2666, captions=[], overlays=[_ov(1, 1.0), _ov(2, 4.0)], keep=[[0.54, 12.26]])
    draft = ("Your 11.7-second vertical cut is rendered with your Persian voice running "
             "throughout and no subtitles. "
             "It has two real Istanbul cutaways from Pexels (Bosphorus and Galata skyline) on "
             "the Istanbul mentions. "
             "Hamdi Ulukaya, Chobani, Murat \u00dclker and Forbes are never named in this speech, "
             "so I added no footage of them. "
             "The clip also ends mid-phrase because the source cuts there.")
    f = facts(before=B2666, after=after, duration_s=11.7, user_text=U2666)
    text, report = tr.finish(draft, f)
    assert report["asks"] == {"broll": "done"}
    assert "never named in this speech" in text
    assert "two real Istanbul cutaways" in text
