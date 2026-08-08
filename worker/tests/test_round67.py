"""Round 67 — the move to GPT-5.6 Luna, the agent's own eyes, and the two
customer failures of Jul 31 2026.

Everything here shipped as a real defect that day:

  * abrayen626's Arabic talking-head came back "0 transcribed words" —
    Deepgram nova-3 detected "Portuguese"/"Turkish" (it does not support
    Arabic), returned an empty 2xx, and the whisper fallback never ran
    because nothing had *failed*. The agent then told him his video had no
    speech, twice, and could not caption or cut anything.
  * ayetoluabeebat's 27s clip of a woman speaking transcribed as language
    "Indonesian" with EXACTLY one word: "Valmera." — our own brand keyterm,
    biased into every customer's ASR call, hallucinated itself as her entire
    transcript, and captions are generated from the transcript.
  * The provider move: OpenAI's reasoning family rejects the classic
    `max_tokens` (wants max_completion_tokens) and any non-default
    temperature — sent as-is, every step of every turn would 400.
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_loop                                              # noqa: E402
import config                                                  # noqa: E402
import llm                                                     # noqa: E402
import taste                                                   # noqa: E402
import transcribe                                              # noqa: E402
from timeline import Timeline                                  # noqa: E402


# ── the parameter-dialect adaptation ────────────────────────────────────

class _OpenAIStyle400(Exception):
    pass


def _mt_error():
    return _OpenAIStyle400(
        "Error code: 400 - {'error': {'message': \"Unsupported parameter: "
        "'max_tokens' is not supported with this model. Use "
        "'max_completion_tokens' instead.\", 'type': "
        "'invalid_request_error'}}")


def _temp_error():
    return _OpenAIStyle400(
        "Error code: 400 - {'error': {'message': \"Unsupported value: "
        "'temperature' does not support 0.2 with this model. Only the "
        "default (1) value is supported.\", 'type': "
        "'invalid_request_error'}}")


def test_classic_dialect_is_the_default():
    kw = llm.completion_kwargs("fresh-model", 500, 0.2)
    assert kw == {"max_tokens": 500, "temperature": 0.2}


def test_max_tokens_rejection_adapts_and_latches():
    llm._use_max_completion_tokens.discard("m1")
    kw = {"max_tokens": 500, "temperature": 0.2}
    out = llm.adapt_completion_kwargs(_mt_error(), "m1", kw)
    assert out == {"max_completion_tokens": 500, "temperature": 0.2}
    # latched: the next fresh call speaks the learned dialect immediately
    assert "max_completion_tokens" in llm.completion_kwargs("m1", 100, None)
    llm._use_max_completion_tokens.discard("m1")


def test_temperature_rejection_adapts_and_latches():
    llm._no_temperature.discard("m2")
    kw = {"max_tokens": 500, "temperature": 0.2}
    out = llm.adapt_completion_kwargs(_temp_error(), "m2", kw)
    assert out == {"max_tokens": 500}
    assert "temperature" not in llm.completion_kwargs("m2", 100, 0.2)
    llm._no_temperature.discard("m2")


def test_an_unrelated_failure_is_not_adapted():
    assert llm.adapt_completion_kwargs(
        RuntimeError("connection reset"), "m3",
        {"max_tokens": 500, "temperature": 0.2}) is None


def test_create_with_dialect_survives_both_rejections():
    """A model that rejects BOTH classics still answers on the third try —
    and the latches make every later call first-try."""
    calls = []

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(dict(kw))
                    if "max_tokens" in kw:
                        raise _mt_error()
                    if "temperature" in kw:
                        raise _temp_error()
                    return "ok"

    llm._use_max_completion_tokens.discard("m4")
    llm._no_temperature.discard("m4")
    try:
        assert llm.create_with_dialect(_Client(), "m4", [],
                                       max_tokens=100, temperature=0.2) == "ok"
        assert len(calls) == 3
        calls.clear()
        assert llm.create_with_dialect(_Client(), "m4", [],
                                       max_tokens=100, temperature=0.2) == "ok"
        assert len(calls) == 1, "the dialect must be latched, not re-learned"
    finally:
        llm._use_max_completion_tokens.discard("m4")
        llm._no_temperature.discard("m4")


# ── direct sight: frames go into the AGENT's own context ────────────────

def test_agent_sees_by_default_and_latches_blind():
    assert llm.agent_sees("some-model") is True
    llm.mark_agent_blind("blind-one")
    try:
        assert llm.agent_sees("blind-one") is False
    finally:
        llm._agent_blind.discard("blind-one")


def test_strip_image_parts_removes_only_images_and_reports():
    msgs = [
        {"role": "user", "content": "plain text"},
        {"role": "user", "content": [
            {"type": "text", "text": "[frames]"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]},
    ]
    assert agent_loop._strip_image_parts(msgs) is True
    assert all(p.get("type") != "image_url" for p in msgs[1]["content"])
    # ...and the model is told WHY the picture vanished
    assert any("could not be shown" in p.get("text", "")
               for p in msgs[1]["content"])
    # nothing to strip -> False, so an unrelated 400 is never mis-latched
    assert agent_loop._strip_image_parts(msgs) is False


def test_recorded_messages_never_carry_image_bytes():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "t"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,"
                                            + "A" * 100000}},
    ]}]
    rec = agent_loop._messages_for_record(msgs)
    assert "base64" not in str(rec)
    assert rec[0]["content"][1] == {"type": "text", "text": "[image attached]"}
    # the original is untouched — it still has to reach the provider
    assert msgs[0]["content"][1]["type"] == "image_url"


# ── the sparse-transcript fallback ──────────────────────────────────────

def test_zero_words_on_real_audio_is_sparse():
    assert transcribe._too_sparse([], 130.0) is True
    assert transcribe._too_sparse([], 2.0) is False      # a 2s clip may be mute


def test_one_hallucinated_word_for_27s_of_speech_is_sparse():
    # ayetoluabeebat's index 201: 27s of continuous speech, one word.
    assert transcribe._too_sparse([types.SimpleNamespace(w="Valmera.")],
                                  27.0) is True


def test_a_normal_transcript_is_not_sparse():
    words = [types.SimpleNamespace(w=f"w{i}") for i in range(40)]
    assert transcribe._too_sparse(words, 27.0) is False


def test_short_thin_clips_are_left_alone():
    # 10s with 2 words: plausibly real ("yeah. okay.") — no second engine.
    assert transcribe._too_sparse([types.SimpleNamespace(w="ok"),
                                   types.SimpleNamespace(w="yes")],
                                  10.0) is False


def test_brand_keyterms_are_not_biased_by_default():
    assert config.WHISPER_HOTWORDS == ""
    assert transcribe._hotword_terms() == []


# ── taste: gentle zooms and caption placement ───────────────────────────

def _tl(dur=60.0):
    return Timeline([[0.0, dur]], [], [])


def _index(n_words=60):
    return {"words": [{"w": f"w{i}", "t0": i, "t1": i + 0.4}
                      for i in range(n_words)],
            "video": {"duration": 60.0, "width": 1080, "height": 1920,
                      "fps": 30, "has_audio": True},
            "shots": []}


def test_repeated_hard_punches_are_flagged():
    edl = {"keep": [[0.0, 60.0]],
           "effects": {"zooms": [
               {"start": 5, "end": 7, "strength": 0.4, "mode": "punch"},
               {"start": 20, "end": 22, "strength": 0.45, "mode": "punch"},
           ]}}
    found = taste.critique(edl, _index(), _tl(), 1080, 1920, user_asked="")
    assert any("abrupt snaps" in f for f in found)


def test_hard_punches_are_allowed_when_asked():
    edl = {"keep": [[0.0, 60.0]],
           "effects": {"zooms": [
               {"start": 5, "end": 7, "strength": 0.4, "mode": "punch"},
               {"start": 20, "end": 22, "strength": 0.45, "mode": "punch"},
           ]}}
    found = taste.critique(edl, _index(), _tl(), 1080, 1920,
                           user_asked="give it hard punch zooms")
    assert not any("abrupt snaps" in f for f in found)


def test_one_hard_punch_is_a_device_not_a_defect():
    edl = {"keep": [[0.0, 60.0]],
           "effects": {"zooms": [
               {"start": 20, "end": 22, "strength": 0.45, "mode": "punch"},
           ]}}
    found = taste.critique(edl, _index(), _tl(), 1080, 1920, user_asked="")
    assert not any("abrupt snaps" in f for f in found)


def test_multiword_captions_mid_frame_are_flagged():
    edl = {"keep": [[0.0, 60.0]],
           "captions": {"mode": "from_transcript",
                        "style": {"preset": "podcast",
                                  "position": "middle"}},
           "effects": {}}
    found = taste.critique(edl, _index(), _tl(), 1080, 1920, user_asked="")
    assert any("across the speaker's face" in f for f in found)


def test_spotlight_mid_frame_is_fine():
    edl = {"keep": [[0.0, 60.0]],
           "captions": {"mode": "from_transcript",
                        "style": {"preset": "spotlight",
                                  "position": "middle"}},
           "effects": {}}
    found = taste.critique(edl, _index(), _tl(), 1080, 1920, user_asked="")
    assert not any("across the speaker's face" in f for f in found)


# ── the spotlight preset renders one glowing word at a time ─────────────

def test_spotlight_is_one_word_per_event_with_glow(tmp_path):
    import re

    import captions
    index = {"words": [{"w": "Discipline", "t0": 0.2, "t1": 0.8},
                       {"w": "beats", "t0": 0.85, "t1": 1.2},
                       {"w": "motivation", "t0": 1.25, "t1": 1.9}]}
    tl = Timeline([[0.0, 3.0]], [], [])
    edl = {"keep": [[0.0, 3.0]],
           "captions": {"mode": "from_transcript",
                        "style": {"preset": "spotlight"}}}
    out = str(tmp_path / "spot.ass")
    captions.build_ass(edl, index, tl, out, play_res=(1080, 1920))
    txt = open(out).read()
    assert txt.count("Dialogue:") == 6          # 3 words x (glow + main)
    assert len(re.findall(r"\\blur\d", txt)) == 3
    assert "DISCIPLINE" in txt                  # uppercase is the look
    # dead centre of the 1080x1920 frame — the one preset allowed mid-frame
    assert r"\pos(540,960)" in txt


def test_multiword_presets_default_to_the_bottom():
    import captions
    # Two sanctioned centre-holders: 'spotlight' (one word at a time) and
    # 'lyric' (round 99b — the mixed-face lyric edit, where owning the middle
    # of the frame IS the look). taste.py names the same two.
    for name, p in captions.PRESETS.items():
        if name in ("spotlight", "lyric"):
            assert p["position"] == "middle"
        else:
            assert p["position"] == "bottom", (
                f"preset '{name}' anchors multi-word text mid-frame — "
                "across the speaker's face")


# ── round 67d: Luna takes tools only with reasoning_effort='none' ──────────

_LUNA_CONFLICT = ("Error code: 400 - {'error': {'message': \"Function tools "
                  "with reasoning_effort are not supported for gpt-5.6-luna "
                  "in /v1/chat/completions. To use function tools, use "
                  "/v1/responses or set reasoning_effort to 'none'.\", "
                  "'type': 'invalid_request_error', "
                  "'param': 'reasoning_effort', 'code': None}}")


def test_tools_reasoning_conflict_is_detected_and_not_overmatched():
    """The EXACT error the first Luna agent turn died on in production (job
    1626, 2026-07-31). It fired with reasoning_effort ABSENT from the request
    — the model's default reasoning is what conflicts — so the strip-the-
    field latch could never fix it, and the singular-only 'is not supported'
    marker meant no adaptation ran at all: the turn failed in 4 seconds."""
    e = RuntimeError(_LUNA_CONFLICT)
    assert llm.looks_like_tools_reasoning_conflict(e)
    assert llm.looks_like_bad_parameter(e, "reasoning_effort")
    # an outage or unrelated 400 must not latch anything
    for msg in ("Rate limit exceeded", "invalid JSON in messages",
                "Unsupported parameter: 'max_tokens' is not supported "
                "with this model."):
        assert not llm.looks_like_tools_reasoning_conflict(RuntimeError(msg))


def test_create_with_dialect_latches_effort_none_for_tools(monkeypatch):
    monkeypatch.setattr(llm, "_tools_effort_none", set())
    calls = []

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(kw)
                    if kw.get("tools") and \
                            kw.get("reasoning_effort") != "none":
                        raise RuntimeError(_LUNA_CONFLICT)
                    return types.SimpleNamespace(choices=[])

    tools = [{"type": "function", "function": {"name": "x"}}]
    llm.create_with_dialect(_Client, "gpt-5.6-luna", [], max_tokens=10,
                            tools=tools)
    assert len(calls) == 2, "one conflict, one corrected retry"
    assert calls[1]["reasoning_effort"] == "none"
    assert llm.tools_need_effort_none("gpt-5.6-luna")
    # latched: the next call is right the FIRST time
    calls.clear()
    llm.create_with_dialect(_Client, "gpt-5.6-luna", [], max_tokens=10,
                            tools=tools)
    assert len(calls) == 1 and calls[0]["reasoning_effort"] == "none"
    # and a tool-less call never carries the field
    calls.clear()
    llm.create_with_dialect(_Client, "gpt-5.6-luna", [], max_tokens=10)
    assert "reasoning_effort" not in calls[0]
