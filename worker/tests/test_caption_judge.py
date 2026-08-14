"""Caption direction compares the live catalog instead of keyword routing."""

import os
import subprocess
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import caption_judge  # noqa: E402
import captions  # noqa: E402
import agent_tools  # noqa: E402
import spatial  # noqa: E402


def test_catalog_is_generated_from_every_live_renderer_preset():
    rows = caption_judge.catalog()
    assert {row["preset"] for row in rows} == {"classic", *captions.PRESETS}
    reels = next(row for row in rows if row["preset"] == "reels")
    assert reels["font"] == captions.PRESETS["reels"]["font"]
    assert reels["word_anim"] == captions.PRESETS["reels"]["word_anim"]


def test_choice_rejects_invented_controls_and_normalizes_valid_style():
    answer = ('prefix {"preset":"editorial","emphasis":"big",'
              '"position":null,"highlight_color":"#f2d1a0",'
              '"confidence":0.87,"reason":"quiet serif supports the '
              'measured reflective pace","rejected":["impact shouts"]}')
    got = caption_judge.parse_choice(answer)
    assert got["style"] == {
        "preset": "editorial", "emphasis": "big",
        "highlight_color": "#F2D1A0"}
    assert got["confidence"] == .87
    assert got["rejected"] == ["impact shouts"]

    assert caption_judge.parse_choice(
        '{"preset":"invented","confidence":1,"reason":"no"}') is None
    assert caption_judge.parse_choice(
        '{"preset":"clean","confidence":"nan","reason":"no"}') is None


def test_review_uses_full_catalog_and_measured_context(monkeypatch):
    seen = {}

    def ask(system, user, **kwargs):
        seen.update(system=system, user=user, kwargs=kwargs)
        return {"text": '{"preset":"clean","emphasis":"big",'
                        '"position":null,"highlight_color":null,'
                        '"confidence":0.9,"reason":"readable at 190 wpm",'
                        '"rejected":["spotlight is too interruptive"]}'}

    monkeypatch.setattr(caption_judge.llm, "ask_text", ask)
    got = caption_judge.review({"wpm": 190, "ratio": "9:16",
                                "treatment": "credible founder proof"})
    assert got["preset"] == "clean"
    assert seen["kwargs"]["purpose"] == "caption_treatment_cast"
    assert '"wpm":190' in seen["user"]
    assert all(f'"preset":"{name}"' in seen["user"]
               for name in captions.PRESETS)
    assert "Do not reward novelty" in seen["system"]


def test_review_prefers_complete_real_pixel_catalog(monkeypatch):
    seen = {}

    monkeypatch.setattr(caption_judge.llm, "vision_available", lambda: True)

    def vision(prompt, paths, **kwargs):
        seen.update(prompt=prompt, paths=paths, kwargs=kwargs)
        return ('{"preset":"editorial","confidence":0.92,'
                '"reason":"the rendered serif has air and clears the face",'
                '"rejected":["impact is visibly too dense"]}')

    monkeypatch.setattr(caption_judge.llm, "ask_vision", vision)
    monkeypatch.setattr(
        caption_judge.llm, "ask_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("valid pixel evidence must not pay for text cast")))
    result = caption_judge.review(
        {"wpm": 138, "ratio": "9:16"},
        proof_paths=["page1.jpg", "page2.jpg"],
        proof_labels=["page 1", "page 2"])

    assert result["preset"] == "editorial"
    assert result["style"] == {"preset": "editorial"}
    assert result["visual_proof"] is True
    assert result["proof_page_count"] == 2
    assert seen["kwargs"]["purpose"] == "caption_treatment_visual_cast"
    assert seen["kwargs"]["image_names"] == ["page 1", "page 2"]
    assert all(f'"preset":"{name}"' in seen["prompt"]
               for name in captions.PRESETS)
    assert "SAME real output-geometry frame" in seen["prompt"]


def test_failed_pixel_review_falls_back_to_complete_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(caption_judge.llm, "vision_available", lambda: True)
    monkeypatch.setattr(caption_judge.llm, "ask_vision",
                        lambda *_args, **_kwargs: None)

    def text(_system, user, **kwargs):
        calls.append((user, kwargs))
        return {"text": '{"preset":"clean","confidence":0.8,'
                        '"reason":"complete metadata fallback"}'}

    monkeypatch.setattr(caption_judge.llm, "ask_text", text)
    result = caption_judge.review(
        {"wpm": 180}, proof_paths=["page.jpg"], proof_labels=["page"])
    assert result["preset"] == "clean"
    assert result["visual_proof"] is False
    assert calls[0][1]["purpose"] == "caption_treatment_cast"
    assert all(f'"preset":"{name}"' in calls[0][0]
               for name in captions.PRESETS)


def test_caption_proofs_render_every_live_preset_as_actual_pixels(
        monkeypatch, tmp_path):
    base = tmp_path / "base.jpg"
    Image.new("RGB", (480, 270), (0, 0, 0)).save(base, "JPEG", quality=100)
    ctx = object.__new__(agent_tools.ToolContext)
    ctx.workdir = str(tmp_path)
    ctx.index = {
        "video": {"duration": 4, "width": 480, "height": 270, "fps": 30},
        "words": [
            {"w": word, "t0": i * .5, "t1": i * .5 + .4}
            for i, word in enumerate(
                ("This", "measured", "proof", "needs", "readable",
                 "captions", "today"))],
    }
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(agent_tools, "_caption_proof_base_frame",
                        lambda *_args, **_kwargs: str(base))
    monkeypatch.setattr(agent_tools, "_caption_proof_geometry",
                        lambda *_args, **_kwargs: (480, 270))
    filters = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True,
        text=True, errors="replace").stdout
    if " subtitles " not in filters:
        # Homebrew can be built without libass even though the production
        # worker image has it. Keep the complete-slate/ASS/command contract
        # covered locally; environments with libass exercise the real burn.
        def fake_run(cmd, **_kwargs):
            assert "subtitles=filename=" in cmd[cmd.index("-vf") + 1]
            out = cmd[-1]
            img = Image.open(base).convert("RGB")
            ImageDraw.Draw(img).rectangle((80, 190, 400, 235),
                                          fill=(255, 255, 255))
            img.save(out, "JPEG", quality=95)

        monkeypatch.setattr(agent_tools.media, "run", fake_run)
    edl = {"keep": [[0, 4]], "inserts": [], "speed": [],
           "frame": {"ratio": "16:9"}}

    result = agent_tools._caption_cast_proofs(
        ctx, edl, caption_judge.catalog(), emphasis_words=["readable"])

    expected = [row["preset"] for row in caption_judge.catalog()]
    assert result["candidate_ids"] == expected
    assert result["candidate_count"] == len(expected)
    assert set(result["proof_frames"]) == set(expected)
    assert len(result["paths"]) == (len(expected) + 3) // 4
    assert all(os.path.exists(path) for path in result["paths"])
    for path in result["proof_frames"].values():
        with Image.open(path) as proof:
            extrema = proof.convert("L").getextrema()
        assert extrema[1] > 80, f"caption pixels missing from {path}"


def _tool_context():
    ctx = object.__new__(agent_tools.ToolContext)
    ctx.index = {
        "video": {"duration": 10, "width": 1080, "height": 1920},
        "words": [
            {"w": word, "t0": i, "t1": i + .5}
            for i, word in enumerate(
                ("this", "measured", "proof", "deserves", "quiet", "type"))],
        "shots": [], "speakers": 1,
    }
    ctx.duration = 10
    ctx.has_main_video = True
    ctx.edit_plan = {
        "steps": ["caption the proof"],
        "treatment": "quiet evidence-led founder film",
        "caption_direction": "serif hierarchy with generous stillness",
        "coherence_rules": ["motion never shouts over the claim"],
    }
    ctx.user_message = "make the typography feel considered"
    ctx.editing_metrics = {}
    ctx._last_caption_cast = None
    ctx._spatial = {"v": spatial.SPATIAL_VERSION, "samples": []}
    ctx._perception = {"vb_env": []}
    ctx.latest_edl = lambda: {"version": 1, "json": {
        "keep": [[0, 10]], "inserts": [], "speed": []}}
    ctx.written = None

    def write(edl, _desc):
        ctx.written = edl
        return "EDL v1 -> v2: captions"

    ctx.write_edl = write
    return ctx


def test_add_captions_uses_independent_cast_and_persists_decision(monkeypatch):
    monkeypatch.setattr(
        agent_tools.caption_judge, "review",
        lambda context: {
            "preset": "editorial",
            "style": {"preset": "editorial", "emphasis": "big"},
            "confidence": .91,
            "reason": "serif hierarchy matches the quiet proof treatment",
            "rejected": ["impact competes with the speaker"],
        })
    ctx = _tool_context()
    out = agent_tools.add_captions(ctx)

    assert ctx.written["captions"]["style"]["preset"] == "editorial"
    assert "Independent caption treatment: editorial" in out
    traces = ctx.editing_metrics["editorial_decisions"]
    assert [row["kind"] for row in traces] == [
        "caption_cast", "caption_style"]
    assert traces[0]["candidate_count"] == 20.0
    assert traces[0]["confidence"] == .91
    assert traces[0]["source"] == "independent_type_director"
    assert traces[1]["preset"] == "editorial"
    assert traces[1]["placement_strategy"] == "preset_default"


def test_add_captions_records_real_pixel_cast_without_dropping_catalog(
        monkeypatch):
    ctx = _tool_context()
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(agent_tools, "_auto_caption_emphasis",
                        lambda *_args, **_kwargs: ["measured"])
    slate = caption_judge.catalog()
    monkeypatch.setattr(
        agent_tools, "_caption_cast_proofs",
        lambda *_args, **_kwargs: {
            "paths": ["page1.jpg"], "labels": ["all presets"],
            "candidate_count": len(slate), "page_count": 1})

    def review(context, proof_paths=None, proof_labels=None):
        assert proof_paths == ["page1.jpg"]
        assert proof_labels == ["all presets"]
        return {"preset": "clean", "style": {"preset": "clean"},
                "confidence": .94, "reason": "actual pixels stay readable",
                "rejected": ["impact crowds the frame"],
                "visual_proof": True}

    monkeypatch.setattr(agent_tools.caption_judge, "review", review)
    out = agent_tools.add_captions(ctx)
    assert "Independent real-pixel caption treatment: clean" in out
    assert ctx.written["captions"]["emphasis_words"] == ["measured"]
    assert ctx.editing_metrics["caption_proof_candidates_rendered"] == \
        len(slate)
    assert ctx.editing_metrics["caption_visual_casts"] == 1
    trace = ctx.editing_metrics["editorial_decisions"][0]
    assert trace["candidate_count"] == len(slate)
    assert trace["review_stage"] == "actual_pixel_catalog"
    assert trace["source"] == "independent_pixel_type_director"


def test_pixel_proof_failure_keeps_metadata_cast_and_captions(monkeypatch):
    ctx = _tool_context()
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(agent_tools, "_auto_caption_emphasis",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        agent_tools, "_caption_cast_proofs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("one preset failed to render")))

    def metadata(context):
        return {"preset": "documentary",
                "style": {"preset": "documentary"},
                "confidence": .82, "reason": "stable metadata fallback",
                "rejected": [], "visual_proof": False}

    monkeypatch.setattr(agent_tools.caption_judge, "review", metadata)
    out = agent_tools.add_captions(ctx)
    assert "Independent caption treatment: documentary" in out
    assert ctx.written["captions"]["style"]["preset"] == "documentary"
    assert ctx.editing_metrics["caption_visual_cast_fallbacks"] == 1
    assert "caption_visual_casts" not in ctx.editing_metrics


def test_explicit_caption_style_bypasses_the_cast(monkeypatch):
    monkeypatch.setattr(
        agent_tools.caption_judge, "review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit user/agent choice must remain authoritative")))
    ctx = _tool_context()
    agent_tools.add_captions(ctx, style={"preset": "broadcast"})
    assert ctx.written["captions"]["style"]["preset"] == "broadcast"
    assert [row["kind"] for row in
            ctx.editing_metrics["editorial_decisions"]] == ["caption_style"]
