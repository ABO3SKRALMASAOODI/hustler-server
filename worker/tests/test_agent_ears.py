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
            '{"verdict":"fix","category":"speech_masking",'
            '"program_time_s":3.0,"target_id":"master",'
            '"sequence_beat":1,"confidence":0.94,'
            '"evidence":"music masks the sentence",'
            '"action":"lower the music 4 dB"}'))
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
    assert "Return JSON only" in heard["prompt"]


def test_render_review_reuses_audio_evidence_when_only_picture_changes(
        monkeypatch, tmp_path):
    clip = tmp_path / "mix.mp3"
    calls = []
    monkeypatch.setattr(llm, "audio_review_available", lambda: True)
    monkeypatch.setattr(
        agent_tools.storage, "download_to",
        lambda _key, path: open(path, "wb").write(b"mp3"))
    monkeypatch.setattr(
        llm, "ask_audio",
        lambda *_args, **_kwargs: (
            calls.append(1) or
            '{"verdict":"pass","category":null,'
            '"program_time_s":null,"confidence":0.93,'
            '"evidence":"speech remains clear over the bed",'
            '"action":null}'))
    ctx = SimpleNamespace(
        workdir=str(tmp_path), edit_plan={"music_direction": "quiet bed"},
        index={"video": {"sha256": "same-source"}}, editing_metrics={},
        last_audio_review=None, audio_reviewed_versions=set(),
        _audio_review_cache={})
    base = {"keep": [[0, 10]], "music": [{"id": "m1", "start": 0,
             "end": 10, "gain_db": -22, "storage_key": "music/a"}]}
    result = {"listen_keys": [{"key": "mix.mp3", "t0": 0, "t1": 6}]}

    first = agent_tools._review_render_audio(
        ctx, {"version": 2, "json": {**base, "effects": {}}}, result)
    second = agent_tools._review_render_audio(
        ctx, {"version": 3, "json": {
            **base, "effects": {"grade": "warm"}}}, result)

    assert first["verdict"] == "pass"
    assert second["verdict"] == "pass" and second["reused"] is True
    assert second["edl_version"] == 3
    assert calls == [1]
    assert ctx.editing_metrics["audio_mix_reviews"] == 1
    assert ctx.editing_metrics["audio_mix_reviews_reused"] == 1


def test_only_structured_confident_audio_defect_can_reopen_mix():
    assert agent_tools._parse_render_audio_review(
        "FIX — the ending pause feels slow; trim the story.", 4, 3) is None
    assert agent_tools._parse_render_audio_review(
        '{"verdict":"fix","category":"story_pacing",'
        '"program_time_s":8,"confidence":0.99,'
        '"evidence":"pause feels slow","action":"cut the story"}',
        4, 3) is None
    assert agent_tools._parse_render_audio_review(
        '{"verdict":"fix","category":"speech_masking",'
        '"program_time_s":5,"confidence":0.5,'
        '"evidence":"voice is covered","action":"lower music"}',
        4, 3) is None
    valid = agent_tools._parse_render_audio_review(
        '{"verdict":"fix","category":"speech_masking",'
        '"program_time_s":5,"target_id":"m1","confidence":0.91,'
        '"evidence":"two words are masked by the chorus",'
        '"action":"lower music 3 dB from 4-6s"}', 4, 3)
    assert valid["verdict"] == "fix"
    assert valid["category"] == "speech_masking"
    assert valid["target_id"] == "m1"


def test_audio_repair_must_name_a_heard_time_and_real_target():
    base = ('{"verdict":"fix","category":"sfx_choice",'
            '"program_time_s":3,"target_id":"sx1","sequence_beat":2,'
            '"confidence":0.93,"evidence":"a comic pop weakens the reveal",'
            '"action":"replace sx1 with a restrained impact"}')
    valid = agent_tools._parse_render_audio_review(
        base, 7, 2, heard_windows=[(1, 5)],
        valid_targets={"sx1", "master", "source"})
    assert valid["target_id"] == "sx1"
    assert valid["sequence_beat"] == 2

    assert agent_tools._parse_render_audio_review(
        base.replace('"program_time_s":3', '"program_time_s":9'),
        7, 2, heard_windows=[(1, 5)],
        valid_targets={"sx1", "master", "source"}) is None
    assert agent_tools._parse_render_audio_review(
        base.replace('"target_id":"sx1"', '"target_id":"ghost"'),
        7, 2, heard_windows=[(1, 5)],
        valid_targets={"sx1", "master", "source"}) is None
    assert agent_tools._parse_render_audio_review(
        base, 7, 2, heard_windows=[],
        valid_targets={"sx1", "master", "source"}) is None


def test_heard_window_label_joins_items_to_measured_story_beat():
    edl = {
        "keep": [[0, 10]],
        "music": [{"id": "mus1", "start": 0, "end": 10,
                   "purpose": "hold tension under the explanation"}],
        "sfx": [{"id": "sx1", "at": 3.2,
                 "purpose": "land the proof reveal"}],
    }
    plan = {"sequence_map": [{
        "role": "proof", "purpose": "make the result credible",
        "sound": "bed narrows, restrained impact on the number",
        "source_start_s": 2, "source_end_s": 4,
    }]}

    label = agent_tools._audio_window_label(edl, plan, 1, 6)

    assert "music id=mus1" in label
    assert "SFX id=sx1 at=3.20s" in label
    assert "beat=1 role=proof" in label
    assert "bed narrows, restrained impact" in label


def test_audio_execution_mapping_exposes_sound_outside_its_promised_beat():
    edl = {
        "keep": [[0, 10]],
        "music": [{"id": "mus1", "start": 0, "end": 10,
                   "purpose": "carry the full arc"}],
        "sfx": [{"id": "sx-late", "at": 7.0,
                 "purpose": "land the proof"}],
    }
    plan = {"sequence_map": [{
        "role": "proof", "sound": "one impact on the proof",
        "source_start_s": 2, "source_end_s": 4,
    }]}

    context = agent_tools._audio_execution_context(edl, plan)

    assert "beat 1 role=proof output=[[2.0, 4.0]]" in context
    assert "music mus1 0-10s touches beats=[1]" in context
    assert "SFX sx-late at=7.0s touches beats=none" in context


def test_audio_context_keeps_sequence_and_item_roles_ahead_of_long_history(
        monkeypatch, tmp_path):
    heard = {}
    monkeypatch.setattr(llm, "audio_review_available", lambda: True)
    monkeypatch.setattr(
        agent_tools.storage, "download_to",
        lambda _key, path: open(path, "wb").write(b"mp3"))
    monkeypatch.setattr(
        agent_tools.director, "decision_block", lambda _plan: "x" * 20000)
    monkeypatch.setattr(
        llm, "ask_audio",
        lambda prompt, _paths, labels, **_kwargs: (
            heard.update(prompt=prompt, labels=labels) or
            '{"verdict":"pass","evidence":"the heard mix is coherent"}'))
    ctx = SimpleNamespace(
        workdir=str(tmp_path), editing_metrics={}, last_audio_review=None,
        audio_reviewed_versions=set(), index={"video": {"sha256": "s"}},
        edit_plan={
            "steps": ["finish the coherent sound treatment"],
            "music_direction": "one restrained bed with a clear payoff",
            "sfx_direction": "one family of physical impacts",
            "sequence_map": [{
                "role": "proof", "purpose": "earn trust",
                "sound": "music thins, then one impact",
                "source_start_s": 2, "source_end_s": 4,
            }],
        })
    row = {"version": 9, "json": {
        "keep": [[0, 10]],
        "music": [{"id": "mus1", "start": 0, "end": 10,
                   "purpose": "sustain tension"}],
        "sfx": [{"id": "sx1", "at": 3.0,
                 "purpose": "land proof"}],
    }}

    report = agent_tools._review_render_audio(
        ctx, row, {"listen_keys": [{"key": "mix.mp3", "t0": 1,
                                      "t1": 6}]})

    assert report["verdict"] == "pass"
    assert "Sound sequence contract" in heard["prompt"]
    assert "music thins, then one impact" in heard["prompt"]
    assert "Authored music roles: mus1" in heard["prompt"]
    assert "Authored SFX events: sx1" in heard["prompt"]
    assert "AUDIO EXECUTION EVIDENCE" in heard["prompt"]
    assert "intentional silence" in heard["prompt"]
    assert "beat=1 role=proof" in heard["labels"][0]


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
