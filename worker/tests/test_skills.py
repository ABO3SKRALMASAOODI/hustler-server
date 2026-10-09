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


# Retired from the live catalog (agent_tools pops them from TOOLS at import),
# so the static validator still sees their literal keys. Skills must not
# teach them: every mention sends the editor into a guaranteed rejection.
RETIRED_TOOLS = (
    "set_edit_plan", "complete_edit_plan_steps", "apply_edit_recipe",
    "generate_video", "generate_image", "research_music", "search_music",
    "audition_music_candidates", "fetch_music",
)


def test_skills_name_only_live_tools_and_never_retired_ones():
    import re
    import agent_tools
    live = set(agent_tools.TOOLS)
    for name in agent_prompt.skill_names():
        text = agent_prompt.read_skill_text(name)
        for retired in RETIRED_TOOLS:
            assert not re.search(rf"\b{retired}\b", text), (name, retired)
        calls = set(re.findall(
            r"(?<![\w.])([a-z][a-z0-9_]*_[a-z0-9_]+)\s*\(", text))
        missing = calls - live - skill_validator.NON_TOOL_CALLS
        assert not missing, (name, sorted(missing))


def test_motion_library_points_at_an_installed_skill():
    # list_motion_templates tells the editor to read 'motion-design'.
    assert "motion-design" in agent_prompt.skill_names()
    text = Path(skill_validator.ROOT / "motion_tools.py").read_text(
        encoding="utf-8")
    assert "'motion-design'" in text
