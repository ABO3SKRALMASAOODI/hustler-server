"""Ticket #1278 (15 Nov 2026): while OpenAI is out of credit no customer-facing look answers
"nothing found" or drops real candidates.

Every model call here goes through the real ClaudeLaneClient, claude_lane.create and
claude_trial.guard_call with only the provider SDKs faked: no paid call is made. Each test
asserts the provider (OpenAI is never sent a request once latched), the model (Haiku) and the
lane (the customer's own, never fenced)."""
import json
import shutil
from types import SimpleNamespace as NS

import pytest
from PIL import Image

import agent_tools
import broll_source
import claude_lane
import claude_trial
import config
import llm
import mcp_search
import net_fetch
import openai_credit
import spend_ledger
import stock

QUOTA = ("Error code: 429 - {'error': {'message': 'You have no credits remaining.', "
         "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}")
HAIKU = "claude-haiku-5-5"


class _OpenAI:
    base_url = "https://api.openai.com/v1"
    api_key = "sk-test"

    def __init__(self):
        self.calls = []
        outer = self

        class _C:
            def create(self, model=None, messages=None, **kw):
                outer.calls.append(model)
                raise RuntimeError(QUOTA)
        self.chat = NS(completions=_C())

    def with_options(self, **kw):
        return self


class _Anthropic:
    """The SDK surface claude_lane.create uses (messages.stream). Records the model, and whose
    lane the call was booked to, for every request."""

    def __init__(self, reply="[]", error=None):
        self.bodies, self.lanes, self.reply, self.error = [], [], reply, error
        self.messages = self

    def with_options(self, **kw):
        return self

    def stream(self, **body):
        self.bodies.append(body)
        self.lanes.append(llm.spend_lane())
        if self.error:
            raise self.error
        reply = self.reply(body) if callable(self.reply) else self.reply
        msg = NS(content=[NS(type="text", text=reply)], stop_reason="end_turn",
                 usage=NS(input_tokens=900, output_tokens=120), _request_id="req_test")

        class _S:
            def __enter__(self_inner):
                return NS(get_final_message=lambda: msg)

            def __exit__(self_inner, *a):
                return False
        return _S()


@pytest.fixture
def customer(monkeypatch, tmp_path):
    """A paying customer's MCP job: shared OpenAI vision, OpenAI wallet empty, real Claude lane,
    the company ledger unreadable (a customer's call never reads it)."""
    openai_credit.reopen()
    claude_lane.reopen()
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    oa = _OpenAI()
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(llm, "vision_client_for", lambda plan: (oa, "gpt-6-luna"))
    monkeypatch.setattr(llm, "claude_trial_lane", lambda: None)
    monkeypatch.setattr(llm, "turn_plan", lambda: "")
    monkeypatch.setattr(llm, "client", lambda: oa)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test", raising=False)
    monkeypatch.setattr(net_fetch, "download",
                        lambda url, path, **kw: open(path, "wb").write(b"\xff\xd8\xff\xd9"))
    monkeypatch.setattr(llm, "_on_ledger",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("ledger down")))
    monkeypatch.setattr(claude_trial, "_metering_db",
                        lambda: NS(run=lambda *a, **k: (None, None)))
    monkeypatch.setattr(claude_lane.time, "sleep", lambda s: None)
    llm.set_recorder(llm.LaneRecorder(spend_ledger.CUSTOMER))
    pic = tmp_path / "frame.jpg"
    pic.write_bytes(b"\xff\xd8\xff\xd9")
    yield NS(openai=oa, workdir=str(tmp_path), pic=str(pic))
    llm.set_recorder(None)
    openai_credit.reopen()
    claude_lane.reopen()


def _sdk(monkeypatch, **kw):
    sdk = _Anthropic(**kw)
    monkeypatch.setattr(claude_lane, "_sdk", lambda: sdk)
    return sdk


def _on_haiku_in_the_customers_lane(sdk, customer, n=None):
    assert customer.openai.calls == []                      # provider: nothing sent to OpenAI
    assert sdk.bodies and all(b["model"] == HAIKU for b in sdk.bodies)       # model
    assert set(sdk.lanes) == {spend_ledger.CUSTOMER}         # lane: the customer's, unfenced
    if n is not None:
        assert len(sdk.bodies) == n


# ------------------------------------------------------------------ 1. search_stock


HITS = [{"id": f"pexels:video:{n}", "provider": "pexels", "kind": "video", "duration_s": 8 + n,
         "page_url": f"https://www.pexels.com/video/lab-{n}/",
         "_thumb": f"https://images.pexels.com/{n}.jpg"} for n in range(1, 7)]


def test_search_stock_with_both_providers_down_returns_every_candidate_honestly(customer,
                                                                                monkeypatch):
    """OpenAI out AND the Anthropic call overloaded: the 6 library hits are returned with their
    own metadata, marked undescribed and unverified, and no description is invented."""
    class Overloaded(Exception):
        status_code = 529
    sdk = _sdk(monkeypatch, error=Overloaded("overloaded"))
    monkeypatch.setattr(stock, "available", lambda: True)
    monkeypatch.setattr(stock, "search", lambda q, **kw: [dict(h) for h in HITS])
    ctx = NS(workdir=customer.workdir, stock_results={}, db=None)
    answer = agent_tools.search_stock(ctx, "medical laboratory", kind="video",
                                      orientation="landscape", count=6)
    assert answer.startswith("6 stock video(s)")
    assert "could not be described" not in answer
    for h in HITS:
        assert h["id"] in answer and h["page_url"] in answer
        kept = ctx.stock_results[h["id"]]
        assert kept["description"] is None and kept["undescribed"] and kept["unverified"]
    assert "6 of these 6 candidate(s) are UNVERIFIED (6 UNDESCRIBED" in answer
    assert customer.openai.calls == ["gpt-6-luna"]          # one refused request
    assert sdk.bodies and all(b["model"] == HAIKU for b in sdk.bodies)
    assert set(sdk.lanes) == {spend_ledger.CUSTOMER}


def test_search_descriptions_still_answer_on_haiku(customer, monkeypatch):
    def described(body):
        ids = json.loads(body["messages"][-1]["content"][0]["text"]
                         .rsplit("Images in order: ", 1)[1])
        return json.dumps([{"id": i, "description": f"Lab bench {i}"} for i in ids])
    sdk = _sdk(monkeypatch, reply=described)
    openai_credit.trip("test", QUOTA)
    rows, missing = mcp_search.describe_missing(NS(workdir=customer.workdir),
                                                [dict(h) for h in HITS])
    assert missing == 0 and rows[0]["description"] == "Lab bench pexels:video:1"
    assert not any(r.get("unverified") for r in rows)
    _on_haiku_in_the_customers_lane(sdk, customer, n=1)


# ------------------------------------------------------------------ 2. find_visual_moments


def _visual_ctx(monkeypatch, tmp_path):
    source = tmp_path / "tile.jpg"
    Image.new("RGB", (64, 64), "navy").save(source)
    monkeypatch.setattr(agent_tools.storage, "download_to",
                        lambda _key, local: shutil.copyfile(source, local))
    return NS(workdir=str(tmp_path), duration=32.0, job={}, editing_metrics={},
              index={"video": {"duration": 32.0}, "tile_step_s": 1.0,
                     "tile_keys": [f"tiles/1/tile_{i:03d}.jpg" for i in range(8)]})


def test_find_visual_moments_looks_on_haiku_in_the_customers_lane(customer, monkeypatch,
                                                                  tmp_path):
    sdk = _sdk(monkeypatch, reply=json.dumps(
        [{"time": 0.5, "confidence": "high", "evidence": "a ball crosses the line"}]))
    customer.openai.calls.clear()
    result = agent_tools.find_visual_moments(_visual_ctx(monkeypatch, tmp_path), "a goal")
    assert result.startswith("VISUAL SEARCH 'a goal'") and "from 8 indexed tiles" in result
    assert "@0.50s [high]" in result
    # the first batch's refusal latched OpenAI; every batch was answered by Haiku
    assert customer.openai.calls in (["gpt-6-luna"], ["gpt-6-luna"] * 2)
    assert len(sdk.bodies) == 2 and all(b["model"] == HAIKU for b in sdk.bodies)
    assert set(sdk.lanes) == {spend_ledger.CUSTOMER}       # pool threads carry the lane


def test_find_visual_moments_with_no_model_says_it_could_not_look(customer, monkeypatch,
                                                                  tmp_path):
    class Overloaded(Exception):
        status_code = 529
    _sdk(monkeypatch, error=Overloaded("overloaded"))
    openai_credit.trip("test", QUOTA)
    result = agent_tools.find_visual_moments(_visual_ctx(monkeypatch, tmp_path), "a goal")
    assert result.startswith("TRANSIENT_FAILURE: visual moment search could not run")
    assert "No coarse sampled match" not in result and customer.openai.calls == []


# ------------------------------------------------------------------ 3. Commons' AI check


def _commons(n):
    return {"id": f"openverse:photo:{n}", "provider": "openverse", "_origin": "wikimedia",
            "title": f"Laboratory {n}", "description": f"Laboratory {n} [wikimedia]"}


def test_the_commons_ai_check_runs_on_haiku_while_openai_is_out(customer, monkeypatch):
    """A paid turn whose own text model is on OpenAI (the edit rung) has its Commons check
    answered by Haiku, in the customer's lane, with no request to OpenAI once latched."""
    broll_source._AI_MADE.clear()
    monkeypatch.setattr(llm, "paid_editor_plan", lambda plan=None: True)
    monkeypatch.setattr(llm, "agent_client_for",
                        lambda *a, **k: (customer.openai, "gpt-6.1-sol"))
    sdk = _sdk(monkeypatch, reply="[2]")
    openai_credit.trip("test", QUOTA)
    kept, note = broll_source.drop_ai_made([_commons(1), _commons(2)], keep_unread=True)
    assert [c["id"] for c in kept] == ["openverse:photo:1"] and note is None
    assert "unverified" not in kept[0]
    _on_haiku_in_the_customers_lane(sdk, customer, n=1)
    broll_source._AI_MADE.clear()


def test_commons_with_no_model_stays_in_the_photo_search_marked_unverified(customer,
                                                                           monkeypatch):
    broll_source._AI_MADE.clear()
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    openai_credit.trip("test", QUOTA)
    monkeypatch.setattr(stock, "available", lambda: True)
    monkeypatch.setattr(stock, "NAMED_ROUTE", False)
    monkeypatch.setattr(stock, "PEXELS_KEY", "")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    monkeypatch.setattr(stock, "_openverse_search",
                        lambda q, k, o, c: [_commons(1), _commons(2)])
    hits = stock.search("laboratory", kind="photo", orientation="landscape", count=4)
    assert sorted(h["id"] for h in hits) == ["openverse:photo:1", "openverse:photo:2"]
    assert all(h["unverified"] and h["ai_check"] == "not run" for h in hits)
    line = stock.summarize(hits)
    assert line.count("UNVERIFIED: not checked for AI-made pictures") == 2
    assert customer.openai.calls == []
    broll_source._AI_MADE.clear()


# ------------------------------------------------------------------ 4. the optional looks


@pytest.mark.parametrize("purpose", ["vision_look", "vision_screen", "broll_rendition_review",
                                     "vision_visual_search"])
def test_each_customer_facing_look_runs_on_haiku_while_openai_is_out(customer, monkeypatch,
                                                                    purpose):
    sdk = _sdk(monkeypatch, reply="looked")
    openai_credit.trip("test", QUOTA)
    assert llm.ask_vision("look", [customer.pic], purpose=purpose) == "looked"
    _on_haiku_in_the_customers_lane(sdk, customer, n=1)


def test_the_refusal_itself_is_answered_on_haiku_in_the_same_look(customer, monkeypatch):
    sdk = _sdk(monkeypatch, reply="looked")
    assert llm.ask_vision("look", [customer.pic], purpose="vision_look") == "looked"
    assert customer.openai.calls == ["gpt-6-luna"] and not openai_credit.available()
    assert [b["model"] for b in sdk.bodies] == [HAIKU]
    assert sdk.lanes == [spend_ledger.CUSTOMER]


def test_a_look_no_model_can_run_says_not_checked_never_a_pass(customer, monkeypatch):
    class Overloaded(Exception):
        status_code = 529
    _sdk(monkeypatch, error=Overloaded("overloaded"))
    openai_credit.trip("test", QUOTA)
    ctx = NS(workdir=customer.workdir, sight_out=False, direct_sight=False, pending_images=[])
    text = agent_tools._deliver_frames(ctx, [customer.pic], ["2.0s"], "is the subject sharp?",
                                       "Automatic review of the downloaded stock clip")
    assert text.startswith("NOT CHECKED: no vision model answered")
    assert "not a pass" in text


def test_the_screen_read_with_no_model_reports_no_answer(customer, monkeypatch):
    class Overloaded(Exception):
        status_code = 529
    _sdk(monkeypatch, error=Overloaded("overloaded"))
    openai_credit.trip("test", QUOTA)
    corners, why = agent_tools._vision_screen_corners(NS(), customer.pic)
    assert corners is None and why == "the vision model did not answer"
