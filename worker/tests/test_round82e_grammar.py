"""Round 82e — the grammar library goes live.

The taste plan's first runtime slice: classify what a user's footage can
become, hand the agent the matching family's rules as HOUSE STYLE inside
the project state, and give it the corpus's #1 missing capability — the
typography-choreography pass (add_kinetic_text) that puts every spoken
phrase on screen at its spoken instant in ONE call instead of forty.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                             # noqa: E402
import grammar                                                 # noqa: E402


def _talking_index(dur=60.0, n_shots=2, speech_frac=0.6):
    words, t = [], 1.0
    while t < dur * speech_frac:
        words.append({"w": "word", "t0": round(t, 2),
                      "t1": round(t + 0.4, 2)})
        t += 0.5
    return {"video": {"duration": dur, "width": 1080, "height": 1920,
                      "fps": 24, "has_audio": True},
            "words": words,
            "shots": [{"id": i + 1, "start": i * dur / n_shots,
                       "end": (i + 1) * dur / n_shots}
                      for i in range(n_shots)]}


# ── the library and the classifier ──────────────────────────────────────


def test_library_loads_every_family():
    lib = grammar.library()
    for slug in ("talking-head-promo", "quote-reel", "voiceover-montage",
                 "narrative-vlog", "card-deck-explainer",
                 "kinetic-typography-talking-head"):
        assert slug in lib, f"{slug} missing from worker/grammars"


def test_classify_talking_head():
    slug, reason = grammar.classify(_talking_index())
    assert slug == "talking-head-promo"
    assert "talking take" in reason


def test_classify_montage():
    idx = _talking_index(dur=120.0, n_shots=40, speech_frac=0.0)
    idx["words"] = []
    slug, _ = grammar.classify(idx)
    assert slug == "voiceover-montage"


def test_classify_declines_when_unsure():
    idx = _talking_index(dur=30.0, n_shots=20, speech_frac=0.25)
    slug, reason = grammar.classify(idx)
    assert slug is None
    assert "no confident family" in reason
    assert grammar.classify({})[0] is None
    assert grammar.classify({"video": {"duration": 3}})[0] is None


def test_plan_block_carries_rules_rubric_and_the_user_wins_rule():
    block = grammar.plan_block(_talking_index())
    assert "HOUSE STYLE" in block
    assert "talking-head-promo" in block
    assert "ALWAYS override" in block, "the user's words must win, in print"
    assert "accent color" in block
    assert "add_kinetic_text" in block
    assert grammar.plan_block({}) == ""


# ── the typography choreography pass ────────────────────────────────────


class _KCtx:
    def __init__(self, words, keep, captions=None):
        self.has_main_video = True
        self.index = {"video": {"duration": 60.0}, "words": words}
        j = {"keep": keep}
        if captions:
            j["captions"] = captions
        self._edl = {"version": 3, "json": j}
        self.written = None

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = edl
        return f"EDL v3 -> v4: {desc}"


def _words(spec):
    """[(token, t0, t1), ...] -> word dicts."""
    return [{"w": w, "t0": a, "t1": b} for w, a, b in spec]


def test_kinetic_text_choreographs_phrases_in_one_pass():
    words = _words([("Stop", 10.2, 10.5), ("thinking", 10.55, 10.9),
                    ("about", 10.95, 11.2), ("how", 11.25, 11.5),
                    # 0.9s gap -> new phrase
                    ("other", 12.4, 12.7), ("people", 12.75, 13.1)])
    ctx = _KCtx(words, keep=[[10.0, 20.0]])
    r = agent_tools.add_kinetic_text(ctx, emphasis_words=["people"])
    assert r.startswith("EDL v")
    texts = ctx.written["texts"]
    assert len(texts) == 2
    a, b = texts
    # source 10.2 with keep starting at 10.0 -> program ~0.2
    assert abs(a["start"] - 0.15) < 0.11
    assert a["text"] == "Stop thinking about how"
    # first phrase holds until the second replaces it
    assert abs(a["end"] - b["start"]) < 0.06
    # alternating placement slots
    assert (a["x"], a["y"]) != (b["x"], b["y"])
    # the emphasized phrase gets the accent + pop + a size bump
    assert b["color"] == "#DC2626"
    assert b["entrance"] == "pop"
    assert b["size_scale"] > a["size_scale"]
    assert a["color"] == "#FFFFFF"


def test_kinetic_text_mutes_captions_only_when_captions_exist():
    words = _words([("hello", 1.0, 1.4), ("world", 1.5, 1.9)])
    ctx = _KCtx(words, keep=[[0.0, 10.0]],
                captions={"mode": "from_transcript"})
    r = agent_tools.add_kinetic_text(ctx)
    assert "muted over this window" in r
    assert ctx.written["caption_mutes"] == [[0.0, 10.0]]

    ctx2 = _KCtx(words, keep=[[0.0, 10.0]])
    agent_tools.add_kinetic_text(ctx2)
    assert "caption_mutes" not in (ctx2.written or {})


def test_kinetic_text_rejects_honestly():
    ctx = _KCtx([], keep=[[0.0, 10.0]])
    assert agent_tools.add_kinetic_text(ctx).startswith("REJECTED")
    # words exist but none survive the cut
    ctx = _KCtx(_words([("cut", 50.0, 50.4)]), keep=[[0.0, 10.0]])
    r = agent_tools.add_kinetic_text(ctx)
    assert r.startswith("REJECTED")
    assert "no kept speech" in r


def test_kinetic_text_caps_and_offers_continuation():
    words = _words([(f"w{i}", i * 2.0, i * 2.0 + 0.4) for i in range(120)])
    ctx = _KCtx(words, keep=[[0.0, 250.0]])
    r = agent_tools.add_kinetic_text(ctx)
    assert r.startswith("EDL v")
    assert len(ctx.written["texts"]) == 48
    assert "continue with start=" in r


def test_kinetic_text_is_registered_as_a_write_tool():
    fn, desc, params = agent_tools.TOOLS["add_kinetic_text"]
    assert fn is agent_tools.add_kinetic_text
    assert "zone" in params and "emphasis_words" in params
    assert agent_tools.REQUIRED_ARGS["add_kinetic_text"] == []
    assert "add_kinetic_text" in agent_tools.WRITE_TOOLS
