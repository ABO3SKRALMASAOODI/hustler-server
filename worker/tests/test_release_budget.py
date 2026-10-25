"""Executor release budget (R3, 25 Oct 2026): at most two releases a day.

deploy-cloudflare-executor.yml asks worker/cloudflare/release_budget.py
before it publishes. A run with a newer main already waiting behind it skips
its publish (the waiting run releases both); a third release in 24 hours is a
warning, never a block; any API failure releases (fail open).
"""

import importlib.util
import os

_PATH = os.path.join(os.path.dirname(__file__), "..", "cloudflare",
                     "release_budget.py")
_spec = importlib.util.spec_from_file_location("release_budget", _PATH)
budget = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(budget)

WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "..", ".github",
                        "workflows", "deploy-cloudflare-executor.yml")


def _run(rid, number, status="in_progress", branch="main", sha="a" * 40):
    return {"id": rid, "run_number": number, "status": status,
            "head_branch": branch, "head_sha": sha,
            "created_at": "2026-10-25T10:00:00Z"}


def test_a_newer_waiting_run_takes_over_the_release():
    runs = [_run(2, 152, "pending", sha="b" * 40), _run(1, 151)]
    release, note = budget.decide(runs, 1, 0, "push")
    assert release is False
    assert "#152" in note and "bbbbbbbb" in note


def test_an_older_or_finished_run_never_skips_this_one():
    runs = [_run(3, 150, "completed"), _run(1, 151),
            _run(4, 149, "pending"), _run(5, 153, "queued", branch="dev")]
    release, _note = budget.decide(runs, 1, 0, "push")
    assert release is True


def test_a_third_release_in_a_day_is_announced_not_blocked():
    release, note = budget.decide([_run(1, 151)], 1, 2, "push")
    assert release is True
    assert "release 3 in the trailing 24 h" in note
    assert "at most 2 a day" in note


def test_an_operator_dispatch_always_releases():
    runs = [_run(2, 152, "pending"), _run(1, 151)]
    assert budget.decide(runs, 1, 5, "workflow_dispatch")[0] is True


def test_only_a_successful_publish_step_counts():
    def jobs(conclusion, name=budget.PUBLISH_STEP):
        return {"jobs": [{"steps": [{"name": "install and test",
                                     "conclusion": "success"},
                                    {"name": name,
                                     "conclusion": conclusion}]}]}
    assert budget.published(jobs("success")) is True
    assert budget.published(jobs("skipped")) is False
    assert budget.published(jobs("failure")) is False
    assert budget.published(jobs("success", name="other")) is False


def test_an_unreachable_api_fails_open(monkeypatch, tmp_path):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def boom(_path):
        raise OSError("no network")

    monkeypatch.setattr(budget, "_get", boom)
    assert budget.main() == 0
    assert out.read_text() == "release=true\n"


def test_the_workflow_gates_every_publishing_step_on_the_budget():
    text = open(WORKFLOW).read()
    head, _, tail = text.partition("- name: executor release budget")
    assert tail, "the budget step must exist"
    assert "python worker/cloudflare/release_budget.py" in tail
    # the tests run before the decision on every push
    assert "python -m pytest -q worker/tests" in head
    for step in ("publish Worker and Container images",
                 "install runtime secrets",
                 "verify provider identity and code"):
        block = tail.split(f"- name: {step}", 1)[1].split("- name:", 1)[0]
        assert "if: steps.budget.outputs.release == 'true'" in block, step


def test_a_release_waits_for_running_customer_jobs_then_goes():
    now = [0.0]
    answers = [[(5, "final", 2123)], [(5, "final", 2123)], []]

    def probe(_url):
        return answers.pop(0)

    def sleep(s):
        now[0] += s

    waited, rows = budget.drain_customers(
        "postgres://x", 900, poll_s=20, sleep=sleep, clock=lambda: now[0],
        probe=probe)
    assert rows == [] and waited == 40


def test_the_customer_wait_is_bounded_and_fails_open():
    now = [0.0]

    def sleep(s):
        now[0] += s

    waited, rows = budget.drain_customers(
        "postgres://x", 60, poll_s=20, sleep=sleep, clock=lambda: now[0],
        probe=lambda _url: [(5, "index", 2097)])
    assert waited == 60 and rows == [(5, "index", 2097)]

    def broken(_url):
        raise RuntimeError("db down")

    assert budget.drain_customers("postgres://x", 60, probe=broken)[1] is None
    assert budget.running_customer_jobs("") is None


def test_the_owner_factory_is_never_waited_for():
    assert 60 in budget.INTERNAL_USER_IDS
    assert "NOT (user_id = ANY(%s))" in budget.CUSTOMER_JOBS_SQL
    assert "state = 'running'" in budget.CUSTOMER_JOBS_SQL
