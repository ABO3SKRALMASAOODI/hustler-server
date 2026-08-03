"""The first two turns a real user ever had, and the four ways they failed.

uveysgaming7766882@gmail.com, project 319 / session 424, Aug 1 2026. A 17.7s
silent welding clip. Everything below is that session's real data.

  1. The ready-notice said "0 transcribed words, 0 pauses in the talking" and
     then invited them to "cut the dead air, caption every word" — a fixed
     example, printed under footage with no speech in it.
  2. They asked for exactly that. Five tool calls later the agent replied
     "I couldn't make either change" and stopped. No alternative, no edit.
  3. They asked for six devices; flash + shake + glow all landed inside the
     SAME 0.52-second window of a 17.7-second video. Every taste rule passed,
     because the density rule divides devices by runtime.
  4. The reply came back in Turkish. Turn one had been English. The trigger
     was two Turkish words inside a line of English editing jargon, and the
     system prompt had no rule about language at all.

They then hand-trimmed the timeline five times, down to a 0.15-second clip,
and never came back.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop                                            # noqa: E402
import agent_prompt                                          # noqa: E402
import config                                                # noqa: E402
import indexer                                               # noqa: E402
import llm                                                   # noqa: E402
import taste                                                 # noqa: E402
from timeline import Timeline                                # noqa: E402


# ── 1. the notice must not suggest an edit this footage cannot take ──────

def test_silent_footage_is_never_offered_a_speech_edit():
    """The real greeting, on the real index: 0 words, 0 pauses."""
    ex = indexer._opening_example(n_words=0, n_sil=0)
    for speech_edit in ("dead air", "caption", "silence", "um", "subtitle",
                        "transcript"):
        assert speech_edit not in ex.lower(), \
            f"silent clip was offered {speech_edit!r}: {ex!r}"
    assert ex.strip(), "an empty example is not an improvement"


def test_speech_with_no_pauses_is_not_told_to_cut_dead_air():
    """There is nothing to tighten when the speaker never stops."""
    assert "dead air" not in indexer._opening_example(n_words=420, n_sil=0)


def test_ordinary_talking_head_keeps_the_original_example():
    ex = indexer._opening_example(n_words=420, n_sil=9)
    assert "dead air" in ex and "caption" in ex


def test_no_speech_is_stated_as_a_fact_not_as_two_zeroes():
    """"0 transcribed words, 0 pauses in the talking" reads as a transcription
    failure. It was neither — nobody was talking."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "indexer.py")).read()
    assert "no speech on the audio" in src
    # and the zero-branch is gated on n_words, not printed unconditionally
    assert 'if n_words else' in src


def test_llm_greet_is_told_the_footage_is_silent():
    """The LLM-authored notice is the normal path when the model resolves.
    It must not invent the same speech-shaped example the template used to."""
    seen = {}

    def fake_ask_text(system, user, **kw):
        seen["user"] = user
        return {"text": "Your video is ready to edit.",
                "prompt_tokens": 1, "completion_tokens": 1}

    key_backup, ask_backup = config.OPENAI_API_KEY, llm.ask_text
    config.OPENAI_API_KEY, llm.ask_text = "test-key", fake_ask_text
    try:
        indexer._greet_via_llm(None, 319, "0.3 min, 4 shots, no speech "
                               "on the audio", None, False, {"words": []})
    finally:
        config.OPENAI_API_KEY, llm.ask_text = key_backup, ask_backup

    prompt = seen.get("user", "").lower()
    assert "no speech" in prompt, "the silent-footage branch never fired"
    assert "do not suggest captions" in prompt


def test_a_video_with_speech_still_gets_the_transcript_grounded_branch():
    seen = {}

    def fake_ask_text(system, user, **kw):
        seen["user"] = user
        return {"text": "ok", "prompt_tokens": 1, "completion_tokens": 1}

    key_backup, ask_backup = config.OPENAI_API_KEY, llm.ask_text
    config.OPENAI_API_KEY, llm.ask_text = "test-key", fake_ask_text
    try:
        indexer._greet_via_llm(None, 1, "2.0 min, 3 shots", None, False,
                               {"words": [{"w": "hello"}, {"w": "there"}]})
    finally:
        config.OPENAI_API_KEY, llm.ask_text = key_backup, ask_backup

    assert "no speech" not in seen.get("user", "").lower()
    assert "hello there" in seen.get("user", "")


# ── 3. three effects on the same half-second ─────────────────────────────

def _edl_of_the_real_turn(**over):
    """EDL v9 of session 424, verbatim: vibrant grade, two zooms, and flash +
    shake + glow all on 5.30-5.82s after the speed ramp re-anchored them."""
    edl = {
        "keep": [[0.0, 17.68]], "inserts": [], "sfx": [], "music": [],
        "texts": [], "overlays": [], "voiceover": [], "volume": [],
        "captions": None, "frame": None, "master": None,
        "speed": [{"id": "sp1", "start": 5.3, "end": 6.0, "factor": 1.35},
                  {"id": "sp2", "start": 14.8, "end": 15.8, "factor": 0.72}],
        "effects": {
            "grade": "vibrant",
            "zooms": [{"id": "zm1", "start": 8.02, "end": 10.22,
                       "strength": 0.14, "mode": "ease"},
                      {"id": "zm2", "start": 14.02, "end": 16.41,
                       "strength": 0.2, "mode": "punch"}],
            "stylize": [
                {"id": "st1", "kind": "flash", "start": 5.3, "end": 5.82,
                 "intensity": 0.55},
                {"id": "st2", "kind": "shake", "start": 5.3, "end": 5.82,
                 "intensity": 0.35},
                {"id": "st3", "kind": "glow", "start": 5.3, "end": 5.82,
                 "intensity": 0.5}],
        },
    }
    edl["effects"].update(over.pop("effects", {}))
    edl.update(over)
    return edl


def _critique(edl, ask=""):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    return taste.critique(edl, {"words": [], "shots": []}, tl,
                          src_w=1280, src_h=720, user_asked=ask)


def _pile_findings(findings):
    return [f for f in findings if "live at the same time" in f]


def test_the_shipped_edit_is_now_caught():
    """The turn that shipped. Five devices over 17.9s averages to one every
    3.5s and passed every rule; three of them were simultaneous."""
    found = _pile_findings(_critique(_edl_of_the_real_turn()))
    assert found, "the flash+shake+glow pile-up still passes the audit"
    msg = found[0]
    assert "flash" in msg and "shake" in msg and "glow" in msg
    assert "5.3" in msg


def test_asking_for_the_devices_does_not_excuse_stacking_them():
    """`user_asked` only ever suppresses findings. It must not suppress this
    one: the user asked for six devices, never for three at once."""
    ask = "zoom + beat flash + shake + kıvılcım glow + speed ramp + renk"
    assert _pile_findings(_critique(_edl_of_the_real_turn(), ask=ask))


def test_two_devices_on_one_beat_is_craft_not_a_pile():
    """A zoom and a flash on the same hit is how emphasis is built. The rule
    must not fire on it, or the agent learns to ignore the whole audit."""
    edl = _edl_of_the_real_turn(effects={"stylize": [
        {"id": "st1", "kind": "flash", "start": 8.1, "end": 8.5,
         "intensity": 0.5}]})
    assert not _pile_findings(_critique(edl))


def test_a_look_worn_by_the_whole_video_is_not_a_pile():
    """Global grain + vignette are live at every instant by definition.
    Counting them would report every graded edit as a pile-up."""
    edl = _edl_of_the_real_turn(effects={"stylize": [
        {"id": "st1", "kind": "grain", "start": None, "end": None,
         "intensity": 0.3},
        {"id": "st2", "kind": "vignette", "start": None, "end": None,
         "intensity": 0.3}]})
    assert not _pile_findings(_critique(edl))


def test_spread_out_devices_pass():
    """The same five devices, distributed. This is the edit that was wanted."""
    edl = _edl_of_the_real_turn(effects={"stylize": [
        {"id": "st1", "kind": "flash", "start": 2.0, "end": 2.4,
         "intensity": 0.5},
        {"id": "st2", "kind": "shake", "start": 6.0, "end": 6.4,
         "intensity": 0.35},
        {"id": "st3", "kind": "glow", "start": 12.0, "end": 12.6,
         "intensity": 0.5}]})
    assert not _pile_findings(_critique(edl))


def test_densest_moment_is_exact():
    """Max overlap is always reached at some interval's start."""
    assert taste._densest_moment([]) is None
    assert taste._densest_moment([(0.0, 1.0, "a")])[2] == ["a"]
    lo, hi, labels = taste._densest_moment(
        [(0.0, 5.0, "a"), (1.0, 2.0, "b"), (1.5, 4.0, "c"), (9.0, 9.5, "d")])
    assert sorted(labels) == ["a", "b", "c"]
    assert (lo, hi) == (1.5, 2.0)
    # touching, not overlapping
    assert len(taste._densest_moment(
        [(0.0, 1.0, "a"), (1.0, 2.0, "b")])[2]) == 1


# ── 4. the language flip ─────────────────────────────────────────────────

def test_the_prompt_has_a_language_rule_at_all():
    p = agent_prompt.SYSTEM_PROMPT
    assert "WRITE IN THE USER'S LANGUAGE" in p
    assert "DO NOT CHANGE LANGUAGE MID-CONVERSATION" in p


def test_the_ready_notice_is_told_not_to_set_the_language():
    """It is always English, so a Turkish user's session would otherwise be
    'established' as English by a message they did not write."""
    p = agent_prompt.SYSTEM_PROMPT
    i = p.index("WRITE IN THE USER'S LANGUAGE")
    rule = p[i:i + 1600]
    assert "does NOT set the language" in rule or "do NOT set the language" \
        in rule
    assert "ready to edit" in rule and "always English" in rule
    assert "from your very first reply" in rule


def test_the_footage_language_cannot_be_read_as_an_instruction():
    """whisper labelled this silent clip 'en'. The state said
    "LANGUAGE (detected): en." — which reads like a directive and is not one."""
    index = {"video": {"duration": 17.7}, "language": "en",
             "sentences": [], "words": [], "shots": [], "silences": []}
    block = agent_loop._index_summary(index)
    assert "LANGUAGE (detected)" not in block
    assert "LANGUAGE OF THE SPEECH IN THE FOOTAGE: en" in block
    assert "your reply" in block.lower()


# ── 2. the dead end ──────────────────────────────────────────────────────

def test_the_prompt_forbids_ending_on_a_bare_refusal():
    p = agent_prompt.SYSTEM_PROMPT
    assert "NEVER END A TURN ON A BARE" in p
    # it must name the footage case, not just the capability case it already
    # covered — that distinction is the whole bug
    i = p.index("NEVER END A TURN ON A BARE")
    rule = p[i:i + 1600]
    assert "no speech to cut silences out of" in rule
    assert "closest edit it does support" in rule


def test_the_no_offers_rule_does_not_apply_to_an_empty_turn():
    """find_silences and cut_silences BOTH told the agent to offer an
    alternative ("ask which parts to keep, or cut by the picture"). It
    offered nothing, because "no offer of more work" is an unconditional
    reply rule. On a turn that delivered nothing, the two rules collide and
    the reply rule was winning."""
    p = agent_prompt.SYSTEM_PROMPT
    i = p.index("No sign-off question")
    rule = p[i:i + 900]
    assert "ASSUMES YOU DELIVERED SOMETHING" in rule
    assert "changed nothing" in rule
