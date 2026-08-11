"""Independent render review: fresh evidence, strict structured findings."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import preview_critic


def test_parse_report_accepts_fenced_json_and_normalizes_findings():
    answer = '''Here is the review:
```json
{"verdict":"pass","findings":[
 {"severity":"major","category":"zoom","time_s":4.2,
  "evidence":"Image 2 tile 1 magnifies only the blank wall",
  "repair":"remove the zoom or aim it at the face","confidence":0.91},
 {"severity":"minor","category":"made_up","time_s":null,
  "evidence":"title is slightly tight","repair":"add margin",
  "confidence":0.6}]}
```'''
    report = preview_critic.parse_report(answer)
    assert report["verdict"] == "repair"  # evidence wins over a stray pass
    assert report["findings"][0]["category"] == "zoom"
    assert report["findings"][1]["category"] == "other"
    lines = preview_critic.repair_lines(report)
    assert len(lines) == 1 and "blank wall" in lines[0]


def test_malformed_or_low_confidence_review_cannot_block_delivery():
    assert preview_critic.parse_report("not json") is None
    report = preview_critic.parse_report(
        '{"verdict":"repair","findings":[{"severity":"major",'
        '"category":"crop","evidence":"possibly clipped",'
        '"repair":"inspect exact frame","confidence":0.4}]}')
    assert report is not None
    assert preview_critic.repair_lines(report) == []


def test_independent_review_sees_edited_output_and_raw_source(monkeypatch,
                                                             tmp_path):
    seen = {}

    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)

    def download(key, path):
        with open(path, "wb") as fh:
            fh.write(key.encode())

    monkeypatch.setattr(agent_tools.storage, "download_to", download)

    def review(paths, labels, context):
        seen.update(paths=paths, labels=labels, context=context)
        return {"verdict": "pass", "findings": []}

    monkeypatch.setattr(agent_tools.preview_critic, "review", review)

    class Ctx:
        workdir = str(tmp_path)
        index = {"tile_keys": ["raw/first.jpg", "raw/last.jpg"]}
        user_message = "make a clean vertical reel"
        duration = 30.0
        edit_plan = {"brief": "talking-head reel", "steps": ["reframe"],
                     "format": "social interview", "must_keep": ["face"],
                     "must_avoid": ["burned text collision"]}

        def latest_edl(self):
            return {"json": {"keep": [[0.0, 30.0]],
                             "frame": {"ratio": "9:16", "mode": "crop"},
                             "effects": {"zooms": []}}}

    report = agent_tools._independent_preview_review(
        Ctx(),
        {"sheet_key": "render/overview.jpg",
         "verify_sheet_key": "render/changed.jpg", "duration_s": 25.0},
        [(4.0, "vertical crop keeps the speaker visible")])
    assert report["verdict"] == "pass"
    assert any("EDITED RENDER overview" in x for x in seen["labels"])
    assert any("EDITED RENDER changed" in x for x in seen["labels"])
    assert sum("RAW SOURCE" in x for x in seen["labels"]) == 2
    assert "talking-head reel" in seen["context"]
    assert "format=social interview" in seen["context"]
    assert "must avoid=burned text collision" in seen["context"]
    assert all(os.path.exists(path) for path in seen["paths"])


def test_critic_compares_framing_treatment_across_shots(monkeypatch):
    seen = {}
    monkeypatch.setattr(preview_critic.llm, "vision_available", lambda: True)

    def ask(prompt, _paths, **_kwargs):
        seen["prompt"] = prompt
        return '{"verdict":"pass","findings":[]}'

    monkeypatch.setattr(preview_critic.llm, "ask_vision", ask)
    report = preview_critic.review(
        ["render.jpg", "raw.jpg"], ["edited", "raw"],
        "brief asks for shot-specific framing")
    assert report["verdict"] == "pass"
    assert "compare treatment ACROSS SHOTS" in seen["prompt"]
    assert "close shot should normally fill" in seen["prompt"]
