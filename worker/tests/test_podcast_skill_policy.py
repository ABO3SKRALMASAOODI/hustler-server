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


def test_render_fix_and_call_counts_are_budgets_never_stops():
    """Owner policy (16 Oct 2026): runs kept ending good shorts on the
    two-render ceiling, the one-fix rule and the call cap, and the owner
    overrode them twice ("ignore and solve it, report it at the end")."""
    import re
    text = _active_text()
    low = text.lower()
    assert "two renders is the ceiling" not in low
    assert "two renders is a ceiling" not in low
    assert not re.search(r"at most one targeted fix|one targeted fix\b", low)
    assert "budgets, never stops" in low
    prompt = (SKILL / "agents" / "openai.yaml").read_text().lower()
    assert "stop the line" not in prompt
    run_py = (SKILL / "scripts" / "run.py").read_text()
    assert "already used" not in run_py


# Quota-style exclusion wording (owner, 16 Oct 2026: a 2 h 41 min Elon Musk
# episode he watched and loved gave 6 finals; its not-selected list parked
# moments "beyond the bar for this batch", as a "third manufacturing story"
# or for overlapping another short's theme). Such wording may appear in the
# skill only inside quotes, as an example of an invalid reason.
QUOTA_WORDING = (
    r"for this batch", r"beyond the bar", r"select fewer", r"pick fewer",
    r"fewer, stronger", r"\balready have \w+ (shorts|stories|on)\b",
    r"\b(second|third|fourth) \w+ story\b", r"\bsame theme\b",
    r"\boverlaps \w+'s\b", r"\b(joe|host)-led\b", r"\bbrand risk\b",
)


def _unquoted(text):
    import re
    flat = " ".join(text.split())
    return re.sub(r'"[^"]{0,160}"', '""', flat)


def test_no_quota_style_exclusion_wording():
    import re
    text = _active_text()
    bare = _unquoted(text).lower()
    for pattern in QUOTA_WORDING:
        assert not re.search(pattern, bare), pattern
    prompt = (SKILL / "agents" / "openai.yaml").read_text().lower()
    assert "rank the hero stories" not in prompt
    selection = " ".join((SKILL / "references" / "selection.md").read_text().split())
    assert "Valid reasons to leave a moment out" in selection
    assert "Never valid:** quota or batch logic" in selection
    for reason in ("no arc", "duplicate", "sponsor read", "private individual",
                   "unfaithful", "footage"):
        assert f"**{reason}**" in selection, reason


def test_selection_is_a_full_scan_graded_independently():
    text = " ".join(_active_text().split())
    assert "Full scan, not a skim" in text
    assert "Grade every moment on its own against a fixed bar" in text
    for grade in ("**hook**", "**payoff**", "**inspiration**"):
        assert grade in text
    assert "(hook + payoff + inspiration + visual) / 18" in text


def test_selection_reads_the_audience_heatmap():
    text = " ".join(_active_text().split())
    assert "## Audience signal" in text
    assert "--skip-download --dump-json" in text
    assert "`heatmap`" in text and "`chapters`" in text
    assert "selection/audience.json" in text
    assert "A high-replay moment you do not select needs a valid reason" in text
    skill = " ".join((SKILL / "SKILL.md").read_text().split())
    assert skill.index("**Audience signal**") < skill.index("4. **Materialize once.**")


def test_challenge_pass_runs_before_make_shorts():
    selection = " ".join((SKILL / "references" / "selection.md").read_text().split())
    challenge = selection.index("### Challenge pass (before make_shorts)")
    assert challenge < selection.index("## Materialize once")
    assert '"would I miss this?"' in selection
    assert "separate critic" in selection and "explicit second pass" in selection
    assert "selection/challenge.md" in selection
    skill = " ".join((SKILL / "SKILL.md").read_text().split())
    assert skill.index("**challenge pass**") < skill.index("4. **Materialize once.**")


def test_a_craft_defect_is_a_fix_never_a_kill():
    review = " ".join((SKILL / "references" / "review.md").read_text().split())
    assert "| attention is no, or several no |" not in review
    assert "are several fixes, never a kill" in review
    selection = (SKILL / "references" / "selection.md").read_text()
    assert "**Kills are expensive.**" in selection
