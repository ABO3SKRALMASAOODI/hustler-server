"""Round 82 — what the Aug 3 2026 users actually hit.

Five real sessions, four product defects, all pinned here:

**1. The index was blind while everything reported healthy.** Round 80 fixed
the vision PROVIDER (env skew); the pipeline still shipped zero captions,
because `ask_vision`'s fixed 1500-token cap starved the reasoning model:
gpt-5.6-luna spends the SAME output budget on thinking before the first
answer token, and a 25-tile sheet's answer alone weighs ~4k tokens. Every
sheet of every real upload returned `answer: null, reasoning_out: 1500`
(llm_calls 4169/4196-4201) while 3-tile test clips sailed through — so
project 336's "use the whole 5-minute video as a scene bank" was answered by
keeping the last 20 seconds, sight unseen.

**2. `set_grade_custom` had no shadows axis.** Project 335 asked for "more
light and remove shadows"; the agent sent shadows=0.35 — the natural
translation — and the tool rejected the axis. The grade was never retried,
the turn then timed out on erases, and the lighting half of the request was
silently dropped.

**3. Five erase_region calls = five full repaint passes.** Each call
re-derives the whole cleaned source including every earlier rectangle, so
project 335's "remove all the TikTok UI" (3 marks + 2 corrections) repainted
the video five times, ate fourteen minutes, and hit the turn's time budget.

**4. "Techno" silently became hip-hop, three times.** The library has no
electronic mood, the agent substituted without saying so, and the user left
without exporting (project 333).
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                             # noqa: E402
import config                                                  # noqa: E402
import indexer                                                 # noqa: E402
import llm                                                     # noqa: E402
import sheets                                                  # noqa: E402


# ── shared stubs ────────────────────────────────────────────────────────


def _resp(content, completion_tokens, reasoning=0):
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=completion_tokens,
        completion_tokens_details=SimpleNamespace(
            reasoning_tokens=reasoning))
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=usage)


class _Client:
    def with_options(self, **kw):
        return self


@pytest.fixture(autouse=True)
def _clean_effort_latch():
    saved = set(llm._no_reasoning_effort)
    yield
    llm._no_reasoning_effort.clear()
    llm._no_reasoning_effort.update(saved)


# ── 1a. a starved vision call retries with room instead of going blind ──


def test_ask_vision_retries_a_starved_reasoning_call(monkeypatch):
    """THE BLIND INDEX. content empty + the whole budget burned on reasoning
    is starvation, not refusal — one retry with triple the cap, and the
    captions that were 'null' in production come back."""
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(llm, "vision_client_for",
                        lambda plan: (_Client(), "test-reasoner"))
    calls = []

    def fake_create(client_obj, model, messages, max_tokens=None,
                    temperature=None, **extra):
        calls.append({"max_tokens": max_tokens, **extra})
        if len(calls) == 1:
            # the production shape: all 1500 tokens went to reasoning
            return _resp(None, max_tokens, reasoning=max_tokens)
        return _resp('[{"tile": 0}]', 20, reasoning=5)

    monkeypatch.setattr(llm, "create_with_dialect", fake_create)
    out = llm.ask_vision("describe", [], max_tokens=1500)
    assert out == '[{"tile": 0}]'
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == 4500, "the retry must triple the cap"


def test_ask_vision_does_not_retry_a_genuinely_empty_answer(monkeypatch):
    """An empty answer with budget LEFT is the model declining, not starving
    — retrying it would double every failed call forever."""
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(llm, "vision_client_for",
                        lambda plan: (_Client(), "test-reasoner"))
    calls = []

    def fake_create(client_obj, model, messages, max_tokens=None,
                    temperature=None, **extra):
        calls.append(1)
        return _resp("", 40, reasoning=10)

    monkeypatch.setattr(llm, "create_with_dialect", fake_create)
    assert llm.ask_vision("describe", [], max_tokens=1500) is None
    assert len(calls) == 1


def test_ask_vision_passes_reasoning_effort_until_rejected(monkeypatch):
    """Descriptive work asks for small reasoning; a provider that refuses the
    field is latched once and never sent it again."""
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(llm, "vision_client_for",
                        lambda plan: (_Client(), "test-reasoner"))
    seen = []

    def fake_create(client_obj, model, messages, max_tokens=None,
                    temperature=None, **extra):
        seen.append(extra)
        return _resp("ok", 5)

    monkeypatch.setattr(llm, "create_with_dialect", fake_create)
    llm.ask_vision("q", [], reasoning_effort="low")
    assert seen[-1].get("reasoning_effort") == "low"

    llm.mark_reasoning_effort_rejected("test-reasoner")
    llm.ask_vision("q", [], reasoning_effort="low")
    assert "reasoning_effort" not in seen[-1]


def test_create_with_dialect_latches_a_rejected_reasoning_effort():
    """The strip-and-latch that lets ask_vision SEND the field safely: a
    provider that answers 'unknown parameter: reasoning_effort' gets one
    failed call, not one per sheet."""
    attempts = []

    class _Chat:
        class completions:
            @staticmethod
            def create(model=None, messages=None, **kw):
                attempts.append(dict(kw))
                if "reasoning_effort" in kw:
                    raise Exception(
                        "unknown parameter: 'reasoning_effort'")
                return _resp("fine", 3)

    client_obj = SimpleNamespace(chat=_Chat)
    out = llm.create_with_dialect(client_obj, "picky-model", [],
                                  max_tokens=100,
                                  reasoning_effort="low")
    assert out.choices[0].message.content == "fine"
    assert llm.reasoning_effort_rejected("picky-model")
    assert "reasoning_effort" not in attempts[-1]


# ── 1b. the cap is sized by the sheet, not fixed ────────────────────────


def test_caption_one_sizes_the_budget_by_tile_count(monkeypatch):
    seen = {}

    def fake_ask(prompt, paths, max_tokens=None, reasoning_effort=None,
                 purpose=None):
        seen.update(max_tokens=max_tokens, effort=reasoning_effort)
        return json.dumps([{"tile": i, "setting": "room", "people": "none",
                            "action": "still", "on_screen_text": "",
                            "subtitles": False} for i in range(25)])

    monkeypatch.setattr(sheets.llm, "ask_vision", fake_ask)
    out = sheets._caption_one("sheet.jpg", list(range(25)), None)
    assert len(out) == 25
    assert seen["max_tokens"] == 1200 + 240 * 25, \
        "a 25-tile sheet must buy 25 tiles of answer"
    assert seen["effort"] == "low"


# ── 1c. a truncated caption array degrades to partial, not to nothing ───


def test_extract_json_array_salvages_a_truncated_reply():
    """One over-long answer used to cost all 25 captions; now it costs the
    tail."""
    cut = ('[{"tile": 0, "setting": "kitchen"}, '
           '{"tile": 1, "setting": "hall"}, '
           '{"tile": 2, "setting": "gar')
    out = llm.extract_json_array(cut)
    assert [r["tile"] for r in out] == [0, 1]


def test_extract_json_array_still_parses_the_clean_case():
    assert llm.extract_json_array('noise [1, 2, 3] trailer') == [1, 2, 3]
    assert llm.extract_json_array("no array here") is None
    assert llm.extract_json_array(None) is None


# ── 2. the grade grew its tonal-region axes ─────────────────────────────


class _GradeCtx:
    def __init__(self):
        self._edl = {"version": 1,
                     "json": {"keep": [[0.0, 10.0]], "effects": {}}}
        self.written = None
        self.has_main_video = True

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = edl
        return f"EDL v1 -> v2: {desc}"


def test_set_grade_custom_accepts_shadows_and_highlights():
    """THE DROPPED HALF-REQUEST. 'more light and remove shadows' translates
    to exposure + shadows — both must land in one call."""
    ctx = _GradeCtx()
    r = agent_tools.set_grade_custom(ctx, exposure=0.25, shadows=0.35,
                                     highlights=-0.2)
    assert r.startswith("EDL v")
    gc = ctx.written["effects"]["grade_custom"]
    assert gc["shadows"] == 0.35
    assert gc["highlights"] == -0.2
    assert gc["exposure"] == 0.25


def test_shadows_neutral_clears_the_axis():
    ctx = _GradeCtx()
    ctx._edl["json"]["effects"] = {"grade_custom": {"shadows": 0.4}}
    agent_tools.set_grade_custom(ctx, shadows=0.0)
    assert not (ctx.written["effects"].get("grade_custom") or {})


def test_grade_curve_points_stay_monotone_at_the_extremes():
    """The renderer's five curve points must be strictly increasing for every
    in-range value — ffmpeg rejects a non-monotone master curve, which would
    turn a legal grade into a failed render."""
    for sh in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for hl in (-1.0, -0.5, 0.0, 0.5, 1.0):
            y0 = min(max(0.12 * sh, 0.0), 0.35)
            y1 = min(max(0.25 + 0.14 * sh, 0.02), 0.48)
            y2 = min(max(0.75 + 0.14 * hl, 0.52), 0.98)
            y3 = min(1.0, max(1.0 + 0.10 * hl, y2 + 0.01))
            assert y0 < y1 < 0.5 < y2 < y3, (sh, hl, y0, y1, y2, y3)


def test_schema_clamps_and_canonicalizes_the_new_axes():
    from schemas import validate_edl
    edl = {"keep": [[0.0, 10.0]],
           "effects": {"grade_custom": {"shadows": 5.0, "highlights": 0.0}}}
    out = validate_edl(edl, 10.0)
    gc = out.effects.grade_custom
    assert gc.shadows == 1.0, "out-of-range clamps to the bound"
    assert gc.highlights is None, "neutral is the absence"


# ── 3. several erases, one repaint pass ─────────────────────────────────


class _EraseCtx:
    def __init__(self):
        self.has_main_video = True
        # Round 97's _superseded_patches reads the master clock off ctx.
        self.duration = 13.6
        self._edl = {"version": 3, "json": {
            "keep": [[0.0, 13.6]],
            "source_clean": {"asset_key": "k", "proxy_key": "p", "fp": "f",
                             "regions": [{"id": "er1", "x": 0.0, "y": 0.7,
                                          "w": 0.5, "h": 0.2, "start": None,
                                          "end": None, "fill": "box",
                                          "kind": None}]}}}

    def latest_edl(self):
        return self._edl


def test_erase_region_batches_into_one_clean_pass(monkeypatch):
    """THE FOURTEEN-MINUTE TURN (updated for round 92). Three rectangles in
    one call reach _apply_patches exactly once — as NEW items only, ids
    continuing after every existing region across both erase mechanisms —
    and existing work is never re-derived (that per-pass tax is what patches
    removed)."""
    passes = []
    monkeypatch.setattr(agent_tools, "_apply_patches",
                        lambda ctx, items, what, drop=None: passes.append(items)
                        or f"EDL v3 -> v4: {what}")
    r = agent_tools.erase_region(_EraseCtx(), regions=[
        {"x": 0.0, "y": 0.75, "w": 0.53, "h": 0.15},
        {"x": 0.86, "y": 0.82, "w": 0.14, "h": 0.07, "fill": "box"},
        {"x": 0.03, "y": 0.0, "w": 0.94, "h": 0.11, "start": 0.5,
         "end": 13.2},
    ])
    assert r.startswith("EDL v")
    assert len(passes) == 1, "one call, one patch application"
    regs = passes[0]
    assert len(regs) == 3                      # the NEW items only
    assert [x["id"] for x in regs] == ["er2", "er3", "er4"]
    assert regs[1]["fill"] == "box"
    assert regs[2]["start"] == 0.5


def test_erase_region_single_rect_form_still_works(monkeypatch):
    monkeypatch.setattr(agent_tools, "_apply_patches",
                        lambda ctx, items, what, drop=None: f"EDL v3 -> v4: {what}")
    r = agent_tools.erase_region(_EraseCtx(), x=0.1, y=0.1, w=0.2, h=0.2)
    assert r.startswith("EDL v")


def test_erase_region_rejects_mixed_and_oversized_batches(monkeypatch):
    monkeypatch.setattr(agent_tools, "_apply_patches",
                        lambda ctx, items, what, drop=None: f"EDL v3 -> v4: {what}")
    r = agent_tools.erase_region(
        _EraseCtx(), x=0.1, y=0.1, w=0.2, h=0.2,
        regions=[{"x": 0, "y": 0, "w": 0.1, "h": 0.1}])
    assert r.startswith("REJECTED")
    r = agent_tools.erase_region(_EraseCtx(), regions=[
        {"x": 0, "y": 0, "w": 0.1, "h": 0.1}] * 9)
    assert r.startswith("REJECTED")
    r = agent_tools.erase_region(_EraseCtx(), regions=[
        {"x": 0, "y": 0, "w": 0.1, "h": 0.1},
        {"x": "wide", "y": 0, "w": 0.1, "h": 0.1}])
    assert r.startswith("regions[1]:")
    assert "must be numbers" in r


# ── 4. the music library names its own edges ────────────────────────────


class _MusicCtx:
    project_id = 1
    job = {"user_id": 1}


def test_music_search_tells_the_agent_not_to_substitute_silently():
    """'i want techno hardcore' x3 -> hip-hop x3, undisclosed, user gone.
    The bundled listing that carried the honesty rule is deleted; the rule
    now rides the search_music tool description the model reads on every
    turn: substitution must be said, and a specific song's way in is a
    link (fetch_url) or the user's own file."""
    desc = agent_tools.TOOLS["search_music"][1].lower()
    assert "substituting silently" in desc
    assert "fetch_url" in desc


# ── 5. a blind index says so instead of letting the agent guess ─────────


class _ShotsCtx:
    duration = 316.8
    index = {"shots": [{"id": i, "start": float(i), "end": i + 1.0,
                        "caption": None} for i in range(1, 6)],
             "moments": []}

    def clamp(self, t):
        return max(0.0, min(float(t), self.duration))


def test_get_shots_on_a_blind_index_points_to_look_at():
    """Project 336: 131 shots, zero captions, zero look_at calls, scenes
    picked from the last 20 seconds. The tool result is where the agent's
    next action is decided, so the instruction lives there."""
    out = agent_tools.get_shots(_ShotsCtx(), 0, 316.8)
    assert "no visual caption" in out
    assert "look_at" in out
    # v10: the degrade path is the agent's standing filmstrip + look_at —
    # the note points there instead of merely warning about guessing.
    assert "filmstrip" in out.lower()


def test_get_shots_with_captions_carries_no_blind_warning():
    ctx = _ShotsCtx()
    ctx.index = {"shots": [{"id": 1, "start": 0.0, "end": 5.0,
                            "caption": {"setting": "a park",
                                        "people": "one runner",
                                        "action": "jogging"}}],
                 "moments": []}
    out = agent_tools.get_shots(ctx, 0, 5)
    assert "a park" in out
    assert "guessing" not in out.lower()


# ── 6. the greeting speaks human units ──────────────────────────────────


def test_short_clips_greet_in_seconds():
    assert indexer._dur_text(13.6) == "14 sec"
    assert indexer._dur_text(61.4) == "61 sec"
    assert indexer._dur_text(316.8) == "5.3 min"


# ── 82b: the Aug 3 evening user (six re-uploads, one hour, zero exports) ─
#
# elina@ signed up at 14:41, re-uploaded the same music video SIX times, and
# left at 15:28 with 0.00 credits and no export. Four defects, pinned below:
# the blind index was CACHED by file hash (so every re-upload got it back),
# the attachment note told the agent her style reference "can be spliced"
# (it opened her teaser with her own screen recording), the agent answered
# her ninth consecutive English message in Russian (the reference's UI
# language), and it frame-hunted 203s of footage for a balloon shot across
# ~14 look_at calls instead of asking her for a timestamp.


def test_index_blind_is_a_coverage_test():
    """v10 retired the whole blind-index class: there is no vision-captioning
    stage to starve, so _index_blind is gone. What replaced it is a direct
    usability test — a cached index only serves if its filmstrip tiles are
    still readable in storage (_index_has_tiles)."""
    import indexer as _ix
    assert not hasattr(_ix, "_index_blind")
    assert not _ix._index_has_tiles({"tile_keys": []})
    assert not _ix._index_has_tiles({})
    # and no vision model is called anywhere in the pipeline
    src = open(_ix.__file__).read()
    assert "ask_vision" not in src
    assert "caption_points" not in src


def test_attachment_note_presents_both_readings_of_a_clip():
    """The note used to say only "It can be spliced into the edit with
    insert_media" — so "make the beginning like here" got the reference
    ITSELF spliced in as the opening 24 seconds."""
    import agent_loop as _al

    class _Db:
        def run(self, fn, *a, **k):
            return {"id": 5, "project_id": 1, "kind": "video_clip",
                    "storage_key": "clips/1/ref.mp4", "duration_s": 24.0,
                    "meta": {"filename": "Screen_Recording_Telegram.mp4"}}

    class _Ctx:
        project_id = 1
        workdir = "/tmp"

    note = _al._attachment_context(_Db(), _Ctx(),
                                   {"meta": {"attachments": [5]}})
    assert "insert_media" in note
    assert "REFERENCE" in note
    assert "look_at_asset" in note
    assert "do NOT insert it" in note


def test_language_rule_covers_text_seen_inside_footage():
    import agent_prompt
    p = agent_prompt.SYSTEM_PROMPT
    assert "TEXT YOU SEE INSIDE FOOTAGE OR ATTACHMENTS" in p
    assert "DO NOT CHANGE LANGUAGE MID-CONVERSATION" in p


def test_tool_descriptions_carry_the_new_rules():
    import agent_tools as _at
    reg = _at.TOOLS if hasattr(_at, "TOOLS") else None
    if reg is None:                      # registry name differs — find it
        for name in dir(_at):
            v = getattr(_at, name)
            if isinstance(v, dict) and "insert_media" in v and "look_at" in v:
                reg = v
                break
    assert reg is not None
    assert "STYLE" in reg["insert_media"][1] and \
           "look_at_asset" in reg["insert_media"][1]
    # Round 84: looking is uncapped by decision — the description must say
    # so, and must point at the filmstrips as the wide view.
    assert "no cap on looking" in reg["look_at"][1]
    assert "filmstrip" in reg["look_at"][1]


def test_zero_write_correction_claim_is_caught():
    """15:49:43, four seconds after the complaint, zero tool calls: 'I
    corrected the sequence to member 1 → member 2 → member 3 ... the result
    remains 50 seconds.' Published verbatim, because 'corrected' was not in
    the claim lexicon. The whole correction family is now fenced."""
    import agent_loop as _al
    reply = ("You’re right—the member order and smooth handoffs "
             "weren’t followed. I corrected the sequence to member 1 "
             "→ member 2 → member 3, keeping each transition "
             "smooth and the first member only at the beginning; the result "
             "remains 50 seconds.")
    v = _al._reply_violations(reply, wrote=False, previewed=False,
                              acted=False)
    assert v, "a zero-write 'I corrected ...' must be a violation"
    for verb in ("reordered", "fixed", "rearranged", "rebuilt", "swapped"):
        vv = _al._reply_violations(f"I {verb} the opening as you asked.",
                                   wrote=False, previewed=False, acted=False)
        assert vv, f"'I {verb} ...' on a zero-write turn must be a violation"
    # the same sentence on a turn that DID write is honest
    assert not _al._reply_violations(reply, wrote=True, previewed=False,
                                     acted=True)
