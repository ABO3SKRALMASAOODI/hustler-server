from pathlib import Path

import agent_prompt
import skill_validator


def test_all_model_visible_skills_are_structured_and_current():
    result = skill_validator.validate_all()
    assert result["ok"], result["failures"]
    assert result["skills"] == len(agent_prompt.skill_names())
    assert result["tools"] > 100


def test_every_skill_supports_focused_section_retrieval():
    for name in agent_prompt.skill_names():
        full = agent_prompt.read_skill_text(name)
        assert full and len(full) > 100
        for section in agent_prompt.SKILL_SECTIONS:
            focused = agent_prompt.read_skill_text(name, section)
            assert focused and f"## {section}" in focused.lower()
            assert focused.startswith(f"# {name} —")


def test_validator_catches_removed_tool_and_operational_history(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    body = ["# sample — sample"]
    for section in skill_validator.REQUIRED_SECTIONS:
        body.extend(["", f"## {section}", "", "Use missing_tool() well."])
    body.append("Project 123, 2026-01-02")
    (skills / "sample.md").write_text("\n".join(body), encoding="utf-8")
    result = skill_validator.validate_all(skills, skill_validator.TOOLS_SOURCE)
    errors = " ".join(result["failures"]["skills/sample.md"])
    assert "missing_tool" in errors
    assert "dated incident" in errors
    assert "project incident" in errors
