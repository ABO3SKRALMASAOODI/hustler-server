"""Actual-audio evidence is available without becoming an edit gate."""

import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402
import agent_loop  # noqa: E402
import config  # noqa: E402
import db as dbx  # noqa: E402
import llm  # noqa: E402


def test_dedicated_reviewer_sends_audio_and_records_modality_tokens(
        monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"bounded-audio")
    sent, recorded = {}, {}

    class Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {
                "choices": [{"message": {
                    "content": "PASS — music is audible and speech is clear."}}],
                "usage": {
                    "prompt_tokens": 900, "completion_tokens": 40,
                    "prompt_tokens_details": {"audio_tokens": 700},
                    "completion_tokens_details": {"audio_tokens": 0}},
            }

    monkeypatch.setattr(config, "AUDIO_REVIEW_API_KEY", "test-key")
    monkeypatch.setattr(config, "AUDIO_REVIEW_MODEL", "gpt-audio-1.5")
    monkeypatch.setattr(llm, "_audio_review_dead", False)
    monkeypatch.setattr(
        llm.requests, "post",
        lambda url, **kwargs: (sent.update(url=url, **kwargs) or Response()))
    monkeypatch.setattr(
        llm, "record",
        lambda purpose, request, response, usage: recorded.update(
            purpose=purpose, request=request, response=response,
            audio=llm.audio_token_counts(usage)))

    answer = llm.ask_audio(
        "Judge this mix.", [str(clip)], ["PROGRAM 0-4s"],
        purpose="audio_render_review")

    assert answer.startswith("PASS")
    parts = sent["json"]["messages"][0]["content"]
    assert any(part.get("type") == "input_audio" for part in parts)
    assert recorded["purpose"] == "audio_render_review"
    assert recorded["audio"] == (700, 0)
    # Payload records labels, never raw base64 audio.
    assert recorded["request"]["clips"] == ["PROGRAM 0-4s"]


def test_audio_reviewer_retries_non_answers_before_returning_evidence(
        monkeypatch, tmp_path):
    clip = tmp_path / "mix.mp3"
    clip.write_bytes(b"bounded-audio")
    answers = ["", "I will listen to the provided clip to assess it.",
               "FIX — music masks the voice; lower it 4 dB."]
    prompts = []

    class Response:
        status_code = 200
        text = "ok"

        def __init__(self, answer):
            self.answer = answer

        def json(self):
            return {"choices": [{"message": {"content": self.answer}}],
                    "usage": {}}

    def post(_url, **kwargs):
        prompts.append(kwargs["json"]["messages"][0]["content"][0]["text"])
        return Response(answers[len(prompts) - 1])

    monkeypatch.setattr(config, "AUDIO_REVIEW_API_KEY", "test-key")
    monkeypatch.setattr(config, "AUDIO_REVIEW_MODEL", "gpt-audio-1.5")
    monkeypatch.setattr(llm, "_audio_review_dead", False)
    monkeypatch.setattr(llm.requests, "post", post)

    answer = llm.ask_audio(
        "Start with PASS or FIX.", [str(clip)],
        purpose="audio_render_review")
    assert answer.startswith("FIX")
    assert len(prompts) == 3
    assert "Answer NOW" in prompts[-1]


def test_review_audio_schema_is_honest_off(monkeypatch):
    monkeypatch.setattr(config, "AUDIO_REVIEW_API_KEY", "")
    monkeypatch.setattr(llm, "_audio_review_dead", False)
    assert "review_audio" not in {
        tool["function"]["name"] for tool in agent_tools.openai_tools()}

    monkeypatch.setattr(config, "AUDIO_REVIEW_API_KEY", "test-key")
    assert "review_audio" in {
        tool["function"]["name"] for tool in agent_tools.openai_tools()}


def test_render_mix_review_is_advisory_actual_audio(monkeypatch, tmp_path):
    heard = {}
    monkeypatch.setattr(llm, "audio_review_available", lambda: True)

    def download(_key, path):
        with open(path, "wb") as output:
            output.write(b"mp3")

    monkeypatch.setattr(agent_tools.storage, "download_to", download)
    monkeypatch.setattr(
        llm, "ask_audio",
        lambda prompt, paths, labels, **kwargs: (
            heard.update(prompt=prompt, paths=paths, labels=labels,
                         kwargs=kwargs) or
            "FIX — the music masks the sentence at 3s; lower it 4 dB."))
    ctx = SimpleNamespace(
        workdir=str(tmp_path), edit_plan={"format": "podcast reel",
            "music_direction": "subtle bed under speech"},
        editing_metrics={}, last_audio_review=None,
        audio_reviewed_versions=set())
    row = {"version": 4, "json": {"music": [{"start": 0}]}}
    result = {"listen_keys": [
        {"key": "media/x.mp3", "t0": 1.0, "t1": 6.0}]}

    report = agent_tools._review_render_audio(ctx, row, result)

    assert report["verdict"] == "fix"
    assert report["edl_version"] == 4
    assert ctx.audio_reviewed_versions == {4}
    assert ctx.editing_metrics["audio_mix_reviews"] == 1
    assert heard["kwargs"]["purpose"] == "audio_render_review"
    assert "actual rendered program" in heard["prompt"].lower()


def test_later_turn_can_hear_uploaded_audio_by_persistent_storage_key(
        monkeypatch, tmp_path):
    """An audio upload is not limited to the upload/indexing turn."""
    key = "music/77/interview.m4a"
    asset = {
        "id": 91, "kind": "music", "storage_key": key,
        "duration_s": 38.0, "meta": {"filename": "interview.m4a"},
    }
    heard = {}

    class Db:
        @staticmethod
        def run(fn, *args, **_kwargs):
            if fn is dbx.asset_by_key:
                assert args == (77, key)
                return asset
            raise AssertionError(f"unexpected DB call {fn.__name__}")

    def download(storage_key, path):
        assert storage_key == key
        with open(path, "wb") as output:
            output.write(b"persisted-upload")

    def extract(source, start, end, output):
        assert source.endswith("asset_91.m4a")
        assert (start, end) == (7.0, 13.0)
        with open(output, "wb") as target:
            target.write(b"bounded-sample")

    monkeypatch.setattr(llm, "audio_review_available", lambda: True)
    monkeypatch.setattr(agent_tools.storage, "download_to", download)
    monkeypatch.setattr(agent_tools.media, "extract_audio_clip", extract)
    monkeypatch.setattr(
        llm, "ask_audio",
        lambda prompt, paths, labels, **kwargs: (
            heard.update(prompt=prompt, paths=paths, labels=labels,
                         kwargs=kwargs) or
            "The guest says the launch failed; the recording has room echo."))
    ctx = SimpleNamespace(
        db=Db(), project_id=77, workdir=str(tmp_path), _asset_locals={},
        editing_metrics={}, edit_plan={"format": "podcast reel"},
        has_main_video=False)

    result = agent_tools.review_audio(
        ctx, asset_key=key, times=[10.0], span_s=6,
        question="judge whether this contains a strong podcast hook")

    assert result.startswith("BOUNDED ACTUAL-AUDIO REVIEW")
    assert "interview.m4a 7.0-13.0s" in result
    assert heard["kwargs"]["purpose"] == "audio_asset_review"
    assert ctx.editing_metrics == {
        "audio_asset_reviews": 1, "audio_review_clips": 1}


def test_actual_audio_fix_gets_one_targeted_repair_decision():
    ctx = SimpleNamespace(
        last_visual_critic=None,
        last_audio_review={"verdict": "fix",
                           "text": "FIX — music masks speech; lower it 4 dB."},
        last_preview={"edl_version": 6},
        latest_edl=lambda: {"version": 6})
    messages, pushed = [], set()

    assert agent_loop._quality_repair_pushback(
        ctx, messages, time.monotonic(), pushed)
    assert "music masks speech" in messages[-1]["content"]
    assert pushed == {6}
    assert not agent_loop._quality_repair_pushback(
        ctx, messages, time.monotonic(), pushed)
