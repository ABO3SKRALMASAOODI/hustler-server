"""Current podcast contract: owner decisions of 13-14 Nov 2026.

v10 replaces the v9 editor-agent protocol with a link-only engine run. Pin
that entry point and its safeguards, not archived headings or tool calls.
The native runtime tests below also exercise delivery, cleanup and resume.
"""
import ast
import os
import subprocess
import sys

from podcast_skill_contract import SKILL, active_text, script


def test_runs_never_stop_the_line():
    # A real source wall now holds delivery (134058d5), without losing work.
    text = active_text()
    for phrase in ("The only input is the link", "no brief, no count, no style, no song",
                   "do not end your turn while it runs", "run the same command with the same link",
                   "shorts already made are kept", "nothing is delivered until the set is whole"):
        assert phrase in text, phrase
    prompt = (SKILL / "agents/openai.yaml").read_text()
    assert "link as its only argument" in prompt and "Ask me nothing else" in prompt


def test_short_count_follows_the_podcast_never_a_number():
    text = active_text()
    assert "every moment at 7 or over is made" in text
    assert "There is no count, minimum or maximum" in text
    source = script()
    assert "There is no target number" in source
    assert "no minimum and no maximum" in source
    assert "HERO_CAP" not in source


def test_render_fix_and_call_counts_are_budgets_never_stops():
    # v10 checkpoints failed shorts, retries on resume and reports real failures;
    # the old unlimited v9 render-budget wording is no longer the run protocol.
    text = active_text()
    for phrase in ("A short that failed is tried again on that run",
                   "anything in `not_made` with its reason",
                   "Fix a real defect in the product, never by hand in a film"):
        assert phrase in text, phrase
    assert "two renders is the ceiling" not in text.lower()
    assert "one targeted fix" not in text.lower()


def test_no_quota_style_exclusion_wording():
    # Selection is model judgment, not a vocabulary classifier. It must explain
    # exclusions; no count or theme quota may replace the individual bar.
    source = script()
    for phrase in ("Grade each moment on its own", "There is no target number",
                   "no minimum and no maximum", "the strongest moments you left out",
                   "the reason"):
        assert phrase in source, phrase
    text = active_text()
    assert "No word list or regex decides what a moment means or whether a film is good" in text
    assert "models read, signals measure" in text


def test_selection_is_a_full_scan_graded_independently():
    source = script()
    for phrase in ("Walk the whole transcript in order", "Grade each moment on its own",
                   "hook_score", "payoff_score", "story_score", "strength"):
        assert phrase in source, phrase
    text = active_text()
    assert "One reading of the whole conversation" in text
    assert "Each is graded 1 to 10 on its own" in text
    assert "A builder never rates its own film" in text
    assert "measurements, not scores" in text


def test_selection_reads_the_audience_heatmap():
    # The manual heatmap step was replaced by v10's whole-conversation model
    # selection. Source identity and word times remain mandatory evidence.
    text = active_text()
    assert "Every word with its time" in text
    assert "else the whole source heard locally" in text
    source = script()
    for phrase in ("# The source", "# Transcript", "identity_text(info)",
                   "lines_of(ws)"):
        assert phrase in source, phrase
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr in ("ask", "ask_metered")
             and n.args and any(isinstance(x, ast.Name) and x.id == "SELECT_SYSTEM" for x in ast.walk(n.args[0]))]
    assert calls
    assert any(len(c.args) > 1 and isinstance(c.args[1], ast.Name) and c.args[1].id == "content"
               and any(k.arg == "effort" and isinstance(k.value, ast.Constant) and k.value.value == "high" for k in c.keywords)
               for c in calls)


def test_challenge_pass_runs_before_make_shorts():
    # The v9 separate critic/make_shorts protocol became a single selection
    # stage before engine construction; the fixed bar still precedes the build.
    source = script()
    assert '("fetch", "hear", "moments", "shorts", "deliver")' in source
    assert 'assign_styles(rank(rows)' in source
    assert 'no moment of this source clears the bar' in source
    assert 'shorts(run, results["fetch"], results["moments"]' in source


def test_a_craft_defect_is_a_fix_never_a_kill():
    text = active_text()
    assert "Fix a real defect in the product, never by hand in a film" in text
    assert "A short that failed is tried again on that run" in text
    assert "anything in `not_made` with its reason" in text
    assert "Pictures never move to a different sentence without a fresh placement check" in text
    assert "Rejected placements return to the take; no unexamined picture reaches the film" in text


def test_payoff_never_holds_on_a_freeze_or_slow_tail():
    text = active_text()
    assert "the payoff is always his face" in text
    assert "film ends on his last word so the end card follows directly" in text
    for retired in ("audio_mode='hold'", "0.5x slow tail", "slow last 0.4 s", "add_freeze_frame"):
        assert retired not in text
    film = script("wf1_film.py")
    assert "the payoff is his face" in film
    assert "never more than its last four seconds" in film
    assert "A clip cut close to its last word has no tail" in film


def test_persistent_editors_share_lessons_and_reviews_use_the_critic():
    # Persistent editor agents were retired for the engine shell. The active
    # handoff is one measured, complete local delivery, with immutable branding.
    text = active_text()
    for phrase in ("one run of the workflow shell", "Delivered once, complete",
                   "Nothing is ever added to a delivered folder", "Never published by this skill",
                   "never cropped, covered, moved or replaced", "run fails if anything of it is left",
                   "No model call on Anthropic's API"):
        assert phrase in text, phrase
    assert "PERSISTENT editors" not in text


def test_v10_runtime_delivery_resume_brand_and_cleanup_contracts(tmp_path):
    """Keep the no-network native regression suite on check-editor's test path.

    A separate interpreter isolates the workflow's llm/film module names from
    the worker's modules. Fixtures write only beneath pytest's temporary root.
    """
    env = dict(os.environ, PODCAST_NO_REEXEC="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "tmp_path_retention_policy=failed",
         "--basetemp", str(tmp_path / "native"), str(SKILL / "scripts/test_podcast.py")],
        cwd=SKILL, env=env, text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
