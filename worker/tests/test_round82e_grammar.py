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
from schemas import default_edl, validate_edl                  # noqa: E402


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
                 "kinetic-typography-talking-head",
                 "podcast-conversation"):
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


def test_long_conversation_does_not_get_creator_promo_skin():
    idx = _talking_index(dur=3600.0, n_shots=6, speech_frac=0.65)
    idx["speakers"] = 2
    slug, reason = grammar.classify(idx)
    assert slug == "podcast-conversation"
    assert "conversation" in reason


def _podcast_index(dur=3600.0):
    idx = _talking_index(dur=dur, n_shots=6, speech_frac=0.65)
    idx["video"].update(width=1920, height=1080)
    idx["speakers"] = 2
    return idx


def test_shorts_child_is_classified_from_its_kept_program_not_the_parent():
    """Oct 2026: a Shorts child shares the parent's full index. Classifying
    that index called a 45s reel a long-form conversation and handed it the
    'do not create artificial motion' house style."""
    idx = _podcast_index()
    child_edl = {"keep": [[612.3, 657.8]]}
    assert grammar.classify(idx)[0] == "podcast-conversation"
    slug, reason = grammar.classify(idx, child_edl, shorts_child=True)
    assert slug == "podcast-reel", reason
    assert "short-form podcast reel" in reason
    shape = grammar.program_shape(idx, child_edl, shorts_child=True)
    assert shape["from_program"] and shape["reel"]
    assert 45 <= shape["duration"] <= 46


def test_vertical_short_program_of_a_conversation_is_a_podcast_reel():
    idx = _podcast_index()
    reel = {"keep": [[100.0, 160.0]], "frame": {"ratio": "9:16"}}
    assert grammar.classify(idx, reel)[0] == "podcast-reel"
    # The same cut delivered landscape stays a conversation cut.
    landscape = {"keep": [[100.0, 160.0]], "frame": {"ratio": "16:9"}}
    assert grammar.classify(idx, landscape)[0] == "podcast-conversation"
    # A long vertical program is not a reel.
    long_cut = {"keep": [[100.0, 400.0]], "frame": {"ratio": "9:16"}}
    assert grammar.classify(idx, long_cut)[0] == "podcast-conversation"


def test_landscape_edit_keeps_its_source_family_while_it_is_trimmed():
    """Only REELS are measured from the kept program. An undiarized 30-min
    podcast trimmed to 5 minutes in 16:9 must keep the podcast-conversation
    house style (it used to fall to 'mixed signals' and lose it)."""
    idx = _talking_index(dur=1800.0, n_shots=6, speech_frac=0.62)
    idx["video"].update(width=1920, height=1080)
    assert "speakers" not in idx
    assert grammar.classify(idx)[0] == "podcast-conversation"
    trimmed = {"keep": [[0.0, 300.0]], "frame": {"ratio": "16:9"}}
    assert grammar.classify(idx, trimmed)[0] == "podcast-conversation"
    assert "HOUSE STYLE" in grammar.plan_block(idx, trimmed)
    # Every non-reel EDL classifies exactly as the source does.
    for keep in ([[0.0, 30.0]], [[10.0, 200.0], [400.0, 900.0]]):
        edl = {"keep": keep, "frame": {"ratio": "16:9"}}
        assert grammar.classify(idx, edl) == grammar.classify(idx)


def test_two_speaker_vertical_reel_is_a_podcast_reel():
    # Sparse-speech source (a stream), chatty two-person reel cut from it.
    idx = _talking_index(dur=1800.0, n_shots=6, speech_frac=0.1)
    idx["speakers"] = 2
    reel = {"keep": [[1.0, 60.0]], "frame": {"ratio": "9:16"}}
    slug, reason = grammar.classify(idx, reel)
    assert slug == "podcast-reel", reason


def test_program_shape_scales_to_a_long_silence_cut_podcast():
    """60-min podcast, 1,200 shots, 799 keep spans after cut_silences: the
    shape runs on every state block and review, so it must stay cheap and
    count kept words/shots exactly like a linear scan."""
    import time
    dur = 3600.0
    words = [{"w": "w", "t0": i * 0.33, "t1": i * 0.33 + 0.25,
              "speaker": "S%d" % ((i // 40) % 2)} for i in range(10900)]
    shots = [{"start": i * 3.0, "end": (i + 1) * 3.0} for i in range(1200)]
    idx = {"video": {"duration": dur, "width": 1920, "height": 1080},
           "words": words, "shots": shots, "speakers": 2}
    step = dur / 799
    keep = [[round(i * step, 3), round(i * step + step * 0.8, 3)]
            for i in range(799)]
    edl = {"keep": keep, "frame": {"ratio": "16:9"}}
    grammar.program_shape(idx, edl)              # warm the Timeline import
    t0 = time.perf_counter()
    shape = grammar.program_shape(idx, edl)
    elapsed = time.perf_counter() - t0
    in_keep = lambda a, b: any(min(b, e) > max(a, s) + .001 for s, e in keep)
    linear_shots = sum(1 for sh in shots if in_keep(sh["start"], sh["end"]))
    assert shape["shots"] == linear_shots
    linear_speech = sum(
        w["t1"] - w["t0"] for w in words
        if any(s - 1e-6 <= (w["t0"] + w["t1"]) / 2 <= e + 1e-6
               for s, e in keep))
    assert abs(shape["speech_s"] - linear_speech) < 1e-6
    assert elapsed < 0.15, elapsed


def test_single_speaker_vertical_take_stays_a_talking_head():
    idx = _talking_index()
    assert grammar.classify(idx, {"keep": [[0.0, 60.0]],
                                  "frame": {"ratio": "9:16"}})[0] == \
        "talking-head-promo"


def test_podcast_reel_house_style_expects_motion_type_and_sound():
    idx = _podcast_index()
    block = grammar.plan_block(idx, {"keep": [[612.3, 657.8]]},
                               shorts_child=True)
    assert "podcast-reel" in block
    assert "ALWAYS override" in block
    low = block.lower()
    for craft in ("micro-story", "camera", "typography", "sound",
                  "add_motion_graphic", "sound:"):
        assert craft in low, craft
    assert "do not create artificial motion" not in low
    assert "podcast-reel" in grammar.library()


def test_plan_block_carries_rules_rubric_and_the_user_wins_rule():
    block = grammar.plan_block(_talking_index())
    assert "HOUSE STYLE" in block
    assert "talking-head-promo" in block
    assert "ALWAYS override" in block, "the user's words must win, in print"
    assert "FORMAT decisions, not global laws" in block
    assert "add_kinetic_text" in block
    assert grammar.plan_block({}) == ""


def test_runtime_house_styles_use_relationships_not_editing_quotas():
    talking = grammar.plan_block(_talking_index())
    montage_idx = _talking_index(dur=120.0, n_shots=40, speech_frac=0.0)
    montage_idx["words"] = []
    montage = grammar.plan_block(montage_idx)
    combined = (talking + "\n" + montage).lower()

    for templated_rule in (
            "every 8-12s", "5-7 cuts per 10s", "numbered lists are the "
            "default", "section_marker_max_gap_s", "display_moments_min",
            "cut_rate_talking_spans_max_per_10s"):
        assert templated_rule not in combined
    assert "not at a timer" in talking
    assert "holds long enough to read" in montage


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
    # The emphasized phrase gets the accent + size bump, while one coherent
    # custom motion language replaces the old roulette of named entrances.
    assert b["color"] == "#DC2626"
    assert a["entrance"] == b["entrance"] == "none"
    assert a["exit"] == b["exit"] == "none"
    assert a["motion"]["scale"][-1]["v"] == 1.0
    assert max(k["v"] for k in b["motion"]["scale"]) > 1.0
    assert a["motion"]["opacity"][0]["v"] == 0.0
    assert b["size_scale"] > a["size_scale"]
    assert a["color"] == "#FFFFFF"


def test_kinetic_text_can_choose_legacy_or_still_motion_language():
    words = _words([("hello", 1.0, 1.3), ("world", 1.35, 1.7)])
    preset = _KCtx(words, keep=[[0.0, 10.0]])
    agent_tools.add_kinetic_text(preset, motion_style="preset")
    assert preset.written["texts"][0]["motion"] is None
    assert preset.written["texts"][0]["entrance"] != "none"

    still = _KCtx(words, keep=[[0.0, 10.0]])
    agent_tools.add_kinetic_text(still, motion_style="still")
    assert still.written["texts"][0]["motion"] is None
    assert still.written["texts"][0]["entrance"] == "none"


def test_kinetic_text_owns_caption_suppression_independent_of_tool_order():
    words = _words([("hello", 1.0, 1.4), ("world", 1.5, 1.9)])
    ctx = _KCtx(words, keep=[[0.0, 10.0]],
                captions={"mode": "from_transcript"})
    r = agent_tools.add_kinetic_text(ctx)
    assert "owns its caption suppression" in r
    text = ctx.written["texts"][0]
    assert text["mute_captions"] is True
    assert "caption_mutes" not in ctx.written

    ctx2 = _KCtx(words, keep=[[0.0, 10.0]])
    agent_tools.add_kinetic_text(ctx2)
    # Captions may be enabled in a later operation; ownership already exists,
    # so order cannot make a duplicate word layer appear.
    assert ctx2.written["texts"][0]["mute_captions"] is True
    assert "caption_mutes" not in (ctx2.written or {})


def test_kinetic_text_rejects_honestly():
    ctx = _KCtx([], keep=[[0.0, 10.0]])
    assert agent_tools.add_kinetic_text(ctx).startswith("REJECTED")
    # words exist but none survive the cut
    ctx = _KCtx(_words([("cut", 50.0, 50.4)]), keep=[[0.0, 10.0]])
    r = agent_tools.add_kinetic_text(ctx)
    assert r.startswith("REJECTED")
    assert "no kept speech" in r


def test_kinetic_text_writes_every_requested_moment_without_a_cap():
    words = _words([(f"w{i}", i * 2.0, i * 2.0 + 0.4) for i in range(120)])
    ctx = _KCtx(words, keep=[[0.0, 250.0]])
    r = agent_tools.add_kinetic_text(ctx)
    assert r.startswith("EDL v")
    assert len(ctx.written["texts"]) == 120
    assert "continue with start=" not in r


def test_kinetic_text_is_registered_as_a_write_tool():
    fn, desc, params = agent_tools.TOOLS["add_kinetic_text"]
    assert fn is agent_tools.add_kinetic_text
    assert "zone" in params and "emphasis_words" in params
    assert agent_tools.REQUIRED_ARGS["add_kinetic_text"] == []
    assert "add_kinetic_text" in agent_tools.WRITE_TOOLS


class _TextCtx:
    def __init__(self):
        self._edl = default_edl(6.0)
        self.version = 1

    def latest_edl(self):
        return {"version": self.version, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(edl, 6.0).model_dump()
        old = self.version
        self.version += 1
        return f"EDL v{old} -> v{self.version}: {desc}"


def test_add_and_revise_general_text_motion():
    ctx = _TextCtx()
    motion = {
        "x": [{"t": 0.0, "v": -0.1},
              {"t": 0.4, "v": 0.5, "ease": "out"}],
        "scale": [{"t": 0.0, "v": 0.8}, {"t": 0.4, "v": 1.0}],
        "opacity": [{"t": 0.0, "v": 0.0}, {"t": 0.15, "v": 1.0}],
    }
    result = agent_tools.add_text(ctx, "ARRIVE", 1.0, 3.0, motion=motion)
    assert result.startswith("EDL v1 -> v2")
    tx = ctx._edl["texts"][0]
    assert tx["entrance"] == tx["exit"] == "none"
    assert tx["motion"]["x"][0]["v"] == -0.1
    assert tx["mute_captions"] is True

    result = agent_tools.set_text_motion(
        ctx, tx["id"], {"rotation": -12, "opacity": 0.85})
    assert result.startswith("EDL v2 -> v3")
    tx = ctx._edl["texts"][0]
    assert tx["motion"]["rotation"] == -12.0
    assert tx["motion"]["opacity"] == 0.85

    result = agent_tools.set_text_motion(ctx, tx["id"], {})
    assert result.startswith("EDL v3 -> v4")
    assert ctx._edl["texts"][0]["motion"] is None


def test_text_motion_tools_reject_ambiguous_animation_and_are_registered():
    ctx = _TextCtx()
    result = agent_tools.add_text(
        ctx, "FIGHT", 1.0, 3.0, entrance="pop",
        motion={"scale": [{"t": 0, "v": 0.8}, {"t": 0.3, "v": 1.0}]})
    assert result.startswith("REJECTED")
    assert "owns the animation curve" in result

    add_props = agent_tools.TOOLS["add_text"][2]
    fn, desc, props = agent_tools.TOOLS["set_text_motion"]
    assert "motion" in add_props and add_props["motion"]["type"] == "object"
    assert fn is agent_tools.set_text_motion and "LOCAL seconds" in desc
    assert "motion" in props and agent_tools.REQUIRED_ARGS[
        "set_text_motion"] == ["id"]
    assert "set_text_motion" in agent_tools.WRITE_TOOLS
    assert "set_text_motion" in agent_tools.RECIPE_TOOLS


def test_state_block_measures_the_program_once_from_the_edl_it_holds():
    """The house style and the editorial-family cast share ONE measured
    program shape from the EDL state_block already read (it used to re-read
    the latest EDL twice and measure the program three times)."""
    import agent_loop
    reads = []
    idx = _podcast_index()

    class Ctx:
        has_main_video = True
        project_id = 9
        duration = 3600.0
        project = {"id": 9, "kind": "video", "parent_project_id": 4}
        edit_plan = None
        user_message = "make the captions bigger"
        index = idx

        @staticmethod
        def latest_edl():
            reads.append(1)
            return {"version": 3, "json": {"keep": [[612.3, 657.8]],
                                           "frame": {"ratio": "9:16"}}}

    class Db:
        @staticmethod
        def run(*_args, **_kwargs):
            return []

    shapes = []
    real_shape = grammar.program_shape

    def counting_shape(*args, **kwargs):
        shapes.append(kwargs.get("shorts_child"))
        return real_shape(*args, **kwargs)

    grammar.program_shape = counting_shape
    try:
        block = agent_loop.state_block(Ctx(), Db())
    finally:
        grammar.program_shape = real_shape
    assert len(reads) == 1
    assert len(shapes) == 1
    assert "This footage classifies as: podcast-reel" in block
    assert "podcast_reel" in block
