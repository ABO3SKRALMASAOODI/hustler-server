"""Ticket #77 (1 Nov 2026): a trial user's analysis request ("Analyse this
ad ... Rate each item 1-10 ... 5. Captions: sync with the voice ...") was
read as an edit ask for captions, marked partial, and the trial reply cut the
ten-item analysis down to two sentences. The request is paraphrased here
(no customer copy); its structure is the production one."""
import ask_ledger
import trial_reply

ANALYSIS = """Paste this into the analysis box.

Analyse this 42s 9:16 paid social ad for a face serum. The audience is women aged 30-55.
Rate each item 1-10, quote the exact timestamp, and give one specific fix:
1. Hook (0-3s): would it stop the scroll? Is the problem shown before it's said?
2. Retention: list every second where attention is likely to drop, and why.
3. Clarity: does a muted viewer understand the full argument from captions alone?
5. Captions: sync with the voice, readability, line length, anything covering faces.
6. Audio: clipped words, volume jumps, words that fade at transitions.
7. Transitions: any that feel jarring, slow or grainy.
Then give:
- The 3 highest-impact fixes ranked by expected lift in hook rate and hold rate.
- 3 alternative opening lines (under 8 words each) to A/B test.
- One-line verdict: ship as is, ship after fixes, or rework."""

EDIT = """Edit this 9:16 ad. Keep every shot and the audio exactly as they are. Only do these things:
1. Trim the first 0.46s of silence so the voice starts on frame 1.
2. Add burned-in captions auto-timed to the voiceover, bold white, yellow key word.
Do not add music, stickers or zooms. Export 1080x1920 MP4."""

DRAFT = """1. Hook, 0-3s: 5/10. The stern mascot stops the scroll, but the problem isn't shown until the wince at about 9.6s. Fix: open on the wince.
2. Retention: 4/10. Likely drop points: 0-4.8s (one static shot), 9.8-14.3s (the same caption for about 4.5s).
3. Clarity: 6/10. A muted viewer gets the claim but not the mechanism.
5. Captions: 7/10. In sync and readable; the caption held across the 14.3-14.4s overlap looks glitchy.
6. Audio: I could not listen to the audio, so item 6 is unrated.
7. Transitions: 6/10. The cut at 14.3s is jarring.

Top 3 fixes, ranked by expected hook and hold lift:
- Show the peeling skin in the first second.
- Break the 4.5s caption hold into two chunks.
- Brighten the dark floor shot at 19.2-23.8s.

Alternative opening lines (A/B test):
- "Your retinol is quitting on you."
- "Stop burning your face."
- "Three serums, one problem."

Verdict: ship after fixes. Your edit is ready to download."""


def _facts(text):
    # production: the captions are burned into the upload, no EDL layer
    return trial_reply.Facts(before={}, after={},
                             wrote=False, previewed=False, duration_s=42.0,
                             frame="vertical", user_text=text)


def test_assessment_request_has_no_edit_asks():
    assert ask_ledger.assessment_only(ANALYSIS)
    assert ask_ledger.asks(ANALYSIS) == []
    assert ask_ledger.ledger(ANALYSIS, {}, {}, False) == {}


def test_short_assessments_are_recognised():
    for text in ("Can you review my reel and rate the hook out of 10?",
                 "Give me your honest feedback on this edit.",
                 "Rate this video 1-10 and tell me what to improve."):
        assert ask_ledger.assessment_only(text), text


def test_edit_requests_keep_their_asks():
    assert not ask_ledger.assessment_only(EDIT)
    assert "captions" in ask_ledger.asks(EDIT)
    for text in ("Review my video and add captions",
                 "Can you rate it and then fix the colour grade?",
                 "Give me feedback, then cut the boring parts",
                 "Create a cinematic 3D comedy video. Rate the jokes in it 1-10.",
                 "review it and try making the whole video smooth, rate it after",
                 "These clips are my B-roll. Analyze them, give feedback and place "
                 "them over the talk.",
                 "[Hook]\nAI could change your job. Honest feedback wins."):
        assert not ask_ledger.assessment_only(text), text


def test_analysis_reply_is_delivered_whole_and_not_partial():
    reply, report = trial_reply.finish(DRAFT, _facts(ANALYSIS))
    # every rated item, the ranked fixes, the openers and the verdict reach the user
    for must in ("Hook, 0-3s: 5/10", "Retention: 4/10", "9.8-14.3s", "Captions: 7/10",
                 "I could not listen to the audio", "Top 3 fixes", "Break the 4.5s caption",
                 "Stop burning your face.", "Verdict: ship after fixes."):
        assert must in reply, must
    # the list layout survives
    assert "\n2. Retention" in reply and "\n- Show the peeling" in reply
    # an unbacked trial export claim is still dropped
    assert "ready to download" not in reply
    assert any(d["why"] == "export" for d in report["dropped"])
    assert not report.get("unmet")
    assert len(reply.split()) > 120


def test_outcome_stays_fulfilled_for_an_answered_analysis():
    import agent_loop
    _reply, report = trial_reply.finish(DRAFT, _facts(ANALYSIS))
    honesty = {}
    assert agent_loop._outcome_after_reply("fulfilled", report, honesty) == "fulfilled"
    assert "unmet_asks" not in honesty
