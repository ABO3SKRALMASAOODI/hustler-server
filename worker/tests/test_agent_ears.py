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
