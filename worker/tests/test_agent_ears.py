"""Round 98.1 — the deaf-latch must recognize xAI's accent.

The round-98 ears shipped with _DEAF_MARKERS that all assume the provider
NAMES audio when refusing it ("input_audio", "does not support audio"...).
xAI refuses differently — it lists what it accepts:

    Error code: 400 - {'error': {'message': 'Invalid chat format. Content
    blocks are expected to be either text or image_url type.', ...}}

No marker matched, looks_like_deaf_model() said "not a deaf rejection", the
latch never set, and the 400 propagated as a failed turn. In production
(jobs 3059 and 3068, Aug 8) that killed EVERY turn that rendered a preview
— round 98 attaches the changed seconds' sound right after — so the user
read "Something went wrong on my end while editing" at the exact moment the
edit had actually landed. These tests pin the xAI phrasing and the gate
that keeps the wider marker honest.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_loop                                              # noqa: E402
import config                                                  # noqa: E402
import llm                                                     # noqa: E402

# The production error, verbatim from video_jobs 3068's error column.
XAI_400 = ("Error code: 400 - {'error': {'message': 'Invalid chat format. "
           "Content blocks are expected to be either text or image_url "
           "type.', 'type': 'invalid_request_error', 'param': None, "
           "'code': None}}")


def test_xai_content_block_rejection_reads_as_deaf():
    assert llm.looks_like_deaf_model(Exception(XAI_400))


def test_openai_style_rejection_still_reads_as_deaf():
    assert llm.looks_like_deaf_model(Exception(
        "This model does not support the 'input_audio' content type."))


def test_unrelated_400_does_not_read_as_deaf():
    # A garden-variety bad request must not switch the ears off.
    assert not llm.looks_like_deaf_model(Exception(
        "Error code: 400 - invalid value for temperature"))


def test_strip_audio_parts_is_the_gate():
    """The xAI phrasing also fits an image-only rejection from some future
    text-only model. The loop is safe anyway because the latch requires
    _strip_audio_parts to have REMOVED something: no audio in the request,
    no deaf latch — exactly the agent_loop.py order this mirrors."""
    audio = {"type": "input_audio",
             "input_audio": {"data": "AAAA", "format": "wav"}}
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                         audio]}]
    assert agent_loop._strip_audio_parts(msgs) is True
    # The parts are gone and the model is told why it cannot hear.
    types = [p["type"] for p in msgs[0]["content"]]
    assert "input_audio" not in types
    assert any("does not take audio" in p.get("text", "")
               for p in msgs[0]["content"] if p["type"] == "text")

    # Image-only request: nothing to strip, so the gate stays closed and an
    # ambiguous 400 falls through to the blind check instead.
    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}
    ]}]
    assert agent_loop._strip_audio_parts(msgs) is False


def test_reply_cannot_deny_a_rendered_audio_review():
    class Ctx:
        audio_reviewed_versions = {2}

        def latest_edl(self):
            return {"version": 2, "json": {}}

    violation = agent_loop._audio_denial_violation(
        Ctx(), "Audio playback was unavailable to me, so I could not hear it.")
    assert violation and "DID hear" in violation
    assert agent_loop._audio_denial_violation(
        Ctx(), "The rendered mix was reviewed and the music is audible.") is None


def test_dedicated_reviewer_uses_audio_chat_and_records_modality_tokens(
        monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"bounded-audio")
    sent = {}
    recorded = {}

    class Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {
                "choices": [{"message": {
                    "content": "PASS — music is audible and speech is clear."
                }}],
                "usage": {
                    "prompt_tokens": 900,
                    "completion_tokens": 40,
                    "prompt_tokens_details": {"audio_tokens": 700},
                    "completion_tokens_details": {"audio_tokens": 0},
                },
            }

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return Response()

    def fake_record(purpose, request, response, usage):
        recorded.update(purpose=purpose, request=request, response=response,
                        audio=llm.audio_token_counts(usage))

    monkeypatch.setattr(config, "AUDIO_REVIEW_API_KEY", "test-key")
    monkeypatch.setattr(config, "AUDIO_REVIEW_MODEL", "gpt-audio-1.5")
    monkeypatch.setattr(config, "AUDIO_REVIEW_BASE_URL",
                        "https://api.openai.com/v1")
    monkeypatch.setattr(llm, "_audio_review_dead", False)
    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm, "record", fake_record)

    answer = llm.ask_audio(
        "Judge this mix.", [str(clip)], ["PROGRAM 0-4s"],
        purpose="audio_render_review")
    assert answer.startswith("PASS")
    body = sent["json"]
    assert body["model"] == "gpt-audio-1.5"
    assert body["modalities"] == ["text"]
    parts = body["messages"][0]["content"]
    assert any(part.get("type") == "input_audio" for part in parts)
    assert recorded["purpose"] == "audio_render_review"
    assert recorded["audio"] == (700, 0)


def test_dedicated_reviewer_retries_transient_model_error(
        monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"bounded-audio")
    calls = []
    recorded = {}

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.text = ("model produced invalid content" if status_code == 500
                         else "ok")

        def json(self):
            return {
                "choices": [{"message": {
                    "content": "PASS — requested music is audible."
                }}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return Response(500 if len(calls) < 3 else 200)

    def fake_record(purpose, request, response, usage):
        recorded.update(purpose=purpose, response=response)

    monkeypatch.setattr(config, "AUDIO_REVIEW_API_KEY", "test-key")
    monkeypatch.setattr(config, "AUDIO_REVIEW_MODEL", "gpt-audio-1.5")
    monkeypatch.setattr(config, "AUDIO_REVIEW_BASE_URL",
                        "https://api.openai.com/v1")
    monkeypatch.setattr(llm, "_audio_review_dead", False)
    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm, "record", fake_record)

    answer = llm.ask_audio("Judge this mix.", [str(clip)])

    assert answer.startswith("PASS")
    assert len(calls) == 3
    assert recorded["response"]["attempts"] == 3
