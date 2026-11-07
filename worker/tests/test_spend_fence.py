"""Model spend has a fence (census class 3).

4 Nov 2026: a company batch spent $60.90 in a day, its Claude calls landed on
the customers' credit line and the automatic switch turned the free trial off.
Every model dollar now lands on one ledger by lane; company spend stops at its
own share before the call and can never reach what a customer gets.
"""
import re
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import agent_tools
import claude_credit
import claude_lane
import claude_trial
import config
import indexer
import llm
import spend_ledger
import videogen

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "worker"


class Ledger:
    """spend_ledger and the customers' line, by SQL shape."""

    def __init__(self):
        self.rows = {}          # (provider, lane, purpose) -> usd
        self.paid = 0.0
        self.kv = {}

    def run(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self._row = None
        if q.startswith("SELECT to_regclass('public.spend_ledger')"):
            self._row = {"t": "spend_ledger"}
        elif q.startswith("INSERT INTO spend_ledger"):
            key = params[:3]
            self.rows[key] = self.rows.get(key, 0.0) + params[3]
        elif q.startswith("SELECT value FROM app_kv"):
            value = self.kv.get(params[0])
            self._row = {"value": value} if value is not None else None
        elif q.startswith("SELECT COALESCE(SUM(usd), 0) AS usd FROM spend_ledger"):
            self._row = {"usd": sum(usd for (provider, lane, _), usd in self.rows.items()
                                    if lane == params[0] and provider == params[1])}
        else:
            raise AssertionError(q)

    def fetchone(self):
        return self._row


@pytest.fixture(autouse=True)
def asks_again():
    spend_ledger._migrated = False
    yield
    spend_ledger._migrated = False


@pytest.fixture
def ledger(monkeypatch):
    world = Ledger()
    monkeypatch.setattr(claude_trial, "_metering_db", lambda: world)
    monkeypatch.delenv("SPEND_LANE", raising=False)
    yield world
    llm.set_recorder(None)


def recorder(lane):
    def fn(*args):
        pass
    fn.spend_lane = lane
    return fn


def usage(prompt=1000, out=100):
    return NS(prompt_tokens=prompt, completion_tokens=out,
              prompt_tokens_details={})


def priced(usd):
    return NS(prompt_tokens=1000, completion_tokens=100,
              prompt_tokens_details={}, provider_cost_usd=usd)


def test_every_provider_lands_on_the_ledger_with_no_recorder(ledger):
    assert llm.get_recorder() is None
    llm.record("vision", {"model": "gpt-6-luna"}, {"answer": "x"}, usage())
    llm.record("agent", {"model": "claude-haiku-5-5"}, {"content": "x"},
               priced(0.25))
    assert ledger.rows[("openai", "customer", "vision")] > 0
    assert ledger.rows[("anthropic", "customer", "agent")] == 0.25


def test_a_call_with_no_token_usage_lands_at_its_recorded_cost(ledger):
    llm.record("transcript_verification", {"model": "whisper-1"},
               {"provider_cost_usd": 0.5})
    assert ledger.rows[("openai", "customer", "transcript_verification")] == 0.5


def test_an_unpriced_model_is_an_estimate_never_zero(ledger):
    llm.record("agent", {"model": "gpt-7-unknown"}, {}, usage(1_000_000, 0))
    assert ledger.rows[("openai", "customer", "agent")] > 0


def test_a_failed_call_costs_nothing(ledger):
    llm.record("agent", {"model": "gpt-6-luna"}, {"error": "503"}, None)
    assert ledger.rows == {}


def test_a_process_asks_for_the_table_once_because_a_customer_waits(ledger):
    asked = []
    execute = ledger.execute

    def counted(sql, params=()):
        if "to_regclass" in sql:
            asked.append(sql)
        execute(sql, params)
    ledger.execute = counted
    for _ in range(5):
        llm.record("vision", {"model": "gpt-6-luna"}, {"answer": "x"}, usage())
    assert len(asked) == 1


def test_an_image_lands_at_its_price_and_its_row_stays_as_it_was(ledger, monkeypatch):
    rows = []
    llm.set_recorder(lambda *args: rows.append(args))
    monkeypatch.setattr(config, "IMAGE_PRICE_USD", 0.055)
    monkeypatch.setattr(llm, "image_client", lambda: NS(images=NS(generate=lambda **k: NS(
        data=[NS(b64_json="aGk=", url=None)]))))
    ok, _err = llm._openai_image_gen("grok-imagine-image-quality", "a cat", "/dev/null")
    assert ok
    assert ledger.rows[("xai", "customer", "image_gen")] == 0.055
    assert "provider_cost_usd" not in rows[0][2]     # the credit charge reads this row


def test_a_refused_image_costs_nothing(ledger):
    llm.record("image_gen", {"model": "grok-imagine-image-quality"},
               {"error": "image API error: no"}, None)
    assert ledger.rows == {}


def test_a_clip_lands_at_its_price(ledger):
    llm.charge_generation("video_gen", config.VIDEO_GEN_MODEL, 0.35)
    assert ledger.rows[("fal", "customer", "video_gen")] == 0.35
    assert 'llm.charge_generation("video_gen"' in _source(WORKER / "agent_tools.py")


def test_a_broken_ledger_never_reaches_the_customers_turn(monkeypatch):
    monkeypatch.setattr(claude_trial, "_metering_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    llm.record("agent", {"model": "gpt-6-luna"}, {}, usage())


def test_a_customers_call_is_never_refused_and_reads_nothing(monkeypatch):
    def refuse(*args):
        raise AssertionError("the fence read the ledger for a customer")
    monkeypatch.setattr(llm, "_on_ledger", refuse)
    monkeypatch.delenv("SPEND_LANE", raising=False)
    llm.set_recorder(recorder("customer"))
    try:
        llm.fence("claude-opus-5-5")
        llm.set_recorder(None)
        llm.fence("gpt-6-luna")
    finally:
        llm.set_recorder(None)


def test_company_spend_stops_at_its_day_share(ledger):
    llm.set_recorder(recorder("internal"))
    llm.record("agent", {"model": "claude-opus-5-5"}, {},
               priced(4.99))
    llm.fence("claude-opus-5-5")
    llm.record("agent", {"model": "claude-opus-5-5"}, {},
               priced(0.02))
    with pytest.raises(llm.SpendFenceReached, match="internal lane has spent") as stop:
        llm.fence("claude-opus-5-5")
    # never read as an empty wallet: no owner alarm, no lane switch
    assert not llm.is_quota_error(stop.value)


def test_company_spend_stops_at_its_cycle_share_too(ledger):
    ledger.kv["spend_fence"] = '{"internal": {"*": {"day_usd": 100, "cycle_usd": 1}}}'
    llm.set_recorder(recorder("internal"))
    llm.record("agent", {"model": "gpt-6-luna"}, {}, NS(
        prompt_tokens=1, completion_tokens=1, prompt_tokens_details={},
        service_tier="default"))
    ledger.rows[("openai", "internal", "agent")] = 1.0
    with pytest.raises(llm.SpendFenceReached):
        llm.fence("gpt-6-luna")


def test_a_lane_is_fenced_per_provider(ledger):
    llm.set_recorder(recorder("replay"))
    llm.record("agent", {"model": "claude-haiku-5-5"}, {},
               priced(15.0))
    with pytest.raises(llm.SpendFenceReached):
        llm.fence("claude-haiku-5-5")
    llm.fence("gpt-6-luna")


def test_a_share_can_be_set_without_a_release(ledger):
    ledger.kv[spend_ledger.CONFIG_KEY] = '{"internal": {"*": {"day_usd": 0.01, "cycle_usd": 1}}}'
    llm.set_recorder(recorder("internal"))
    llm.record("agent", {"model": "claude-haiku-5-5"}, {},
               priced(0.02))
    with pytest.raises(llm.SpendFenceReached):
        llm.fence("claude-haiku-5-5")


def test_an_unreadable_ledger_stops_company_spend(monkeypatch):
    monkeypatch.setattr(claude_trial, "_metering_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    llm.set_recorder(recorder("internal"))
    try:
        with pytest.raises(llm.SpendFenceReached, match="could not be read"):
            llm.fence("claude-opus-5-5")
    finally:
        llm.set_recorder(None)


def test_before_the_migration_the_fence_is_open_and_says_so(monkeypatch, capsys):
    class Empty(Ledger):
        def execute(self, sql, params=()):
            self._row = {"t": None}
    world = Empty()
    monkeypatch.setattr(claude_trial, "_metering_db", lambda: world)
    monkeypatch.setattr(llm, "_unmigrated_said", False)
    llm.set_recorder(recorder("internal"))
    try:
        llm.fence("claude-opus-5-5")
        assert "migration 043 is missing" in capsys.readouterr().out
    finally:
        llm.set_recorder(None)


def full(ledger, lane="internal"):
    llm.set_recorder(recorder(lane))
    ledger.rows[("openai", lane, "agent")] = 1000.0
    ledger.rows[("anthropic", lane, "agent")] = 1000.0


def test_the_fence_stands_before_the_wire_on_chat(ledger):
    full(ledger)
    sent = []
    client = NS(chat=NS(completions=NS(create=lambda **k: sent.append(k))))
    with pytest.raises(llm.SpendFenceReached):
        llm.create_with_dialect(client, "gpt-6-luna", [{"role": "user", "content": "x"}])
    assert sent == []


def test_the_fence_stands_before_the_wire_on_responses(ledger, monkeypatch):
    full(ledger)
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **k: pytest.fail("the call left"))
    with pytest.raises(llm.SpendFenceReached):
        llm.responses_create("https://api.openai.com/v1", "k", "gpt-6.1-sol",
                             [{"role": "user", "content": "x"}], [])


def test_the_fence_stands_before_the_wire_on_claude(ledger, monkeypatch):
    full(ledger)
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_lane, "_sdk",
                        lambda: pytest.fail("the call left"))
    with pytest.raises(llm.SpendFenceReached):
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])


def _at_share(ledger):
    ledger.kv["spend_fence"] = '{"internal": {"*": {"day_usd": 0, "cycle_usd": 0}}}'
    llm.set_recorder(recorder("internal"))


def test_the_fence_stands_before_the_wire_on_an_image(ledger, monkeypatch):
    _at_share(ledger)
    monkeypatch.setattr(llm, "image_client", lambda: pytest.fail("the call left"))
    ok, err = llm._openai_image_gen("grok-imagine-image-quality", "a cat", "/dev/null")
    assert ok is False and "share" in err
    assert ledger.rows == {}


def test_the_fence_stands_before_the_wire_on_a_clip(ledger, monkeypatch):
    _at_share(ledger)
    monkeypatch.setattr(config, "FAL_KEY", "key")
    monkeypatch.setattr(videogen, "needs_image", lambda model: False)
    monkeypatch.setattr(videogen.requests, "post",
                        lambda *a, **k: pytest.fail("the call left"))
    ok, err, seconds = videogen.generate_video("a cat", "/dev/null")
    assert ok is False and "share" in err and seconds == 0.0


def test_a_lane_switch_inside_the_chat_loop_is_fenced():
    text = _source(WORKER / "agent_loop.py")
    loop = text[:text.index("resp = step_client.chat.completions.create(")]
    loop = loop[loop.rindex("while resp is None:"):]
    assert "llm.fence(call_model)" in loop


def test_the_trial_meter_keeps_a_company_call_off_the_line(monkeypatch):
    seen = []

    class Db:
        def run(self, fn, *args):
            seen.append(args)
            return 0.0, 0.0
    monkeypatch.setattr(claude_trial, "_metering_db", lambda: Db())
    monkeypatch.setattr(claude_trial, "_pool_alerts", lambda *a: None)
    monkeypatch.delenv("SPEND_LANE", raising=False)
    scope = claude_trial.Scope(user_id=46, cap_usd=1.0, spent_usd=0.0,
                               model="claude-haiku-5-5", granted=True)
    claude_trial.activate(scope)
    try:
        llm.set_recorder(recorder("internal"))
        claude_trial.meter("claude-haiku-5-5", 0.1)
        llm.set_recorder(recorder("customer"))
        claude_trial.meter("claude-haiku-5-5", 0.1)
    finally:
        claude_trial.deactivate()
        llm.set_recorder(None)
    assert [args[-1] for args in seen] == [False, True]


def test_our_own_upload_is_company_spend_on_the_sound_lanes_thread(ledger, monkeypatch):
    monkeypatch.delenv("SPEND_LANE", raising=False)
    job = {"id": 1, "project_id": 2, "user_id": 60}
    recorder_, sound = indexer._company_recorders(NS(), job)
    assert isinstance(recorder_, llm.JobRecorder) and llm.get_recorder() is recorder_
    llm.set_recorder(None)
    seen = []

    def sound_lane():
        llm.set_recorder(sound)
        seen.append(llm.spend_lane())
        llm.record("transcript_verification", {"model": "gpt-6-luna"}, {}, usage())
        llm.set_recorder(None)
    thread = threading.Thread(target=sound_lane)
    thread.start()
    thread.join()
    assert seen == ["internal"]
    assert ledger.rows[("openai", "internal", "transcript_verification")] > 0
    assert "sound_recorder" in _source(WORKER / "indexer.py").split("def _sound_lane")[1][:200]


def test_a_customers_upload_stays_as_it_was(monkeypatch):
    monkeypatch.delenv("SPEND_LANE", raising=False)
    job = {"id": 1, "project_id": 2, "user_id": 2744}
    assert indexer._company_recorders(None, job) == (None, None)
    assert llm.get_recorder() is None


def test_the_customers_line_takes_customer_calls_only(ledger, monkeypatch):
    paid = []
    monkeypatch.setattr(claude_trial, "_paid_tx",
                        lambda conn, usd: paid.append(usd))
    llm.set_recorder(recorder("internal"))
    claude_trial.meter_paid("claude-opus-5-5", 9.0)
    llm.set_recorder(recorder("replay"))
    claude_trial.meter_paid("claude-opus-5-5", 9.0)
    assert paid == []
    llm.set_recorder(recorder("customer"))
    claude_trial.meter_paid("claude-haiku-5-5", 0.01)
    assert paid == [0.01]


def test_a_test_account_on_the_trial_lane_stays_off_the_pool_and_the_line(
        monkeypatch):
    days, lines = [], []

    class Conn:
        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            if "trial_budget_days" in sql:
                days.append(sql)

        def fetchone(self):
            return {"spent_usd": 0.5}
    monkeypatch.setattr(claude_credit, "ledger_ready", lambda conn: True)
    monkeypatch.setattr(claude_credit, "check_line",
                        lambda conn: lines.append(1))
    monkeypatch.setattr(claude_trial, "pool_totals", lambda conn: {"pool": 0.0})
    claude_trial._meter_tx(Conn(), 46, 0.5, True, False, customer=False)
    assert days == [] and lines == []
    claude_trial._meter_tx(Conn(), 2000, 0.5, True, False, customer=True)
    assert len(days) == 1 and lines == [1]


def test_the_lane_comes_from_the_account_and_a_replay_stack_overrides_it(
        monkeypatch):
    monkeypatch.delenv("SPEND_LANE", raising=False)
    assert spend_ledger.lane_of(2744) == "customer"
    assert spend_ledger.lane_of(60) == "internal"
    assert spend_ledger.lane_of(None) == "customer"
    assert spend_ledger.lane_of("not a number") == "customer"
    monkeypatch.setenv("SPEND_LANE", "replay")
    assert spend_ledger.lane_of(2744) == "replay"
    assert llm.spend_lane() == "replay"


def test_the_lane_rides_the_recorder_to_pool_threads(monkeypatch):
    monkeypatch.delenv("SPEND_LANE", raising=False)
    fn = recorder("internal")
    seen = []

    def pool_thread():
        llm.set_recorder(fn)
        seen.append(llm.spend_lane())
        deferred = agent_tools._DeferredRecorder(fn)
        llm.set_recorder(deferred)
        seen.append(llm.spend_lane())
        llm.set_recorder(None)
    thread = threading.Thread(target=pool_thread)
    thread.start()
    thread.join()
    assert seen == ["internal", "internal"]


def test_a_job_recorder_knows_whose_job_it_is(monkeypatch):
    monkeypatch.delenv("SPEND_LANE", raising=False)
    job = {"project_id": 1, "id": 2, "user_id": 60}
    assert llm.JobRecorder(None, job).spend_lane == "internal"
    assert llm.JobRecorder(None, dict(job, user_id=2744)).spend_lane == "customer"


def _source(path):
    return path.read_text(encoding="utf-8")


def test_every_job_that_makes_a_recorder_says_whose_money_it_spends():
    for name in ("agent_loop.py", "shorts.py", "mcp_exec.py"):
        text = _source(WORKER / name)
        assert re.search(r"_recorder\.spend_lane = spend_ledger\.lane_of\(job", text), name


# Every place a model provider is called. A new one is added here on purpose,
# with its charge (llm.record, or the concierge's) and its fence.
PROVIDER_CALLS = {
    "worker/claude_lane.py": 1,       # guard_call, metered, recorded
    "worker/llm.py": 5,               # create_with_dialect, responses_create,
                                      # ask_audio, both image calls
    "worker/agent_loop.py": 1,        # the agent step
    "worker/transcript_verify.py": 1,
    "worker/videogen.py": 1,
}
CALL = re.compile(r"\.messages\.(?:stream|create)\(|\.chat\.completions\.create\("
                  r"|\.responses\.create\(|\.audio\.transcriptions\.create\("
                  r"|\.images\.generate\(|requests\.post\(\s*(?:url|submit_url)\b")
# Executors and downloads, and speech to text (billed by the minute, by Deepgram).
OUR_OWN_HTTP = ("worker/remote.py", "worker/net_fetch.py", "worker/transcribe.py")


def test_no_worker_module_reaches_a_provider_off_the_ledger():
    found = {}
    for path in sorted(WORKER.glob("*.py")):
        text = _source(path)
        doc_free = re.sub(r'''""".*?"""''', "", text, flags=re.S)
        name = f"worker/{path.name}"
        hits = list(CALL.finditer(doc_free))
        if not hits or name in OUR_OWN_HTTP:
            continue
        found[name] = len(hits)
        for hit in hits:
            before = doc_free[:hit.start()].splitlines()[-60:]
            assert any("fence(" in line or "guard_call(" in line
                       for line in before), (name, doc_free[hit.start():hit.end()])
    assert found == PROVIDER_CALLS


def test_the_operator_path_is_gone():
    assert not (WORKER / "operator_recovery.py").exists()
    for path in sorted(WORKER.glob("*.py")):
        assert "operator_repair" not in _source(path), path.name
    assert not hasattr(claude_credit, "repair_available")
    source = _source(ROOT / "backend" / "routes" / "admin_video.py")
    assert 'projects/<int:project_id>/repair",' not in source
    assert "def repair_project_edit" not in source


def test_the_twins_are_identical():
    assert _source(WORKER / "spend_ledger.py") == \
        _source(ROOT / "backend" / "spend_ledger.py")


def test_the_shares_fit_in_what_the_customers_budget_leaves():
    shares = spend_ledger.DEFAULT_SHARES
    company = (shares["internal"]["*"]["cycle_usd"]
               + shares["replay"]["anthropic"]["cycle_usd"])
    assert company <= 199.99 - 149.33
