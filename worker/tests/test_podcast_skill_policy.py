"""Owner policy for the shared podcast-shorts skill (16 Oct 2026): a run
never pauses to wait for a Valmera fix or asks the user to continue. It
works around defects, finishes on its own and reports the real defects at
the end. The owner's runs kept stopping and needed "continue" each time."""
import pathlib

SKILL = (pathlib.Path(__file__).resolve().parents[2]
         / "plugins" / "valmera" / "skills" / "valmera-podcast-shorts")


def _active_text():
    files = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
    return "\n".join(p.read_text() for p in files)


def test_runs_never_stop_the_line():
    text = _active_text()
    assert "stop the line" not in text.lower()
    assert "Defects: keep going, report at the end" in text
    assert "never asks the user" in text


def test_short_count_follows_the_podcast_never_a_number():
    """Owner policy (16 Oct 2026): no target, minimum, maximum or rate for
    the number of shorts; every story that clears the bar is made."""
    import re
    text = _active_text()
    assert "count follows the podcast" in text
    assert not re.search(r"\btop \d+-\d+\b", text)
    assert "hero cap" not in text.lower()
    run_py = (SKILL / "scripts" / "run.py").read_text()
    assert "HERO_CAP" not in run_py
