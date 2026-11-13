"""Ticket #1088: a user's pick of a library track is read by a model.

Production, 13 Nov 2026 (p5744): offered "1) Calm Currents ...", the customer
answered "Calm current". The exact-title rule saw no pick, add_library_music
was refused five times and the turn looped on the same question. Owner rule 1
still holds: delegation ("do what suits the story") and a bare mood are never
a pick, and any failure to read is no pick.
"""
import json
from types import SimpleNamespace

import pytest

from w0c_support import tool_context

import agent_tools  # noqa: E402
import llm  # noqa: E402
import music_gate  # noqa: E402
import music_library  # noqa: E402
import music_pick  # noqa: E402
from schemas import default_edl  # noqa: E402

CALM = "chill-calm-currents-lofi-relax-calm"
OFFER = ("Chill mood ke andar ye teen options hain: 1) Calm Currents (chill, "
         "relaxed lofi), 2) Bubbles (bright, relaxed lofi), 3) Canon Event "
         "(lofi, reflective). Number boliye.")


@pytest.fixture
def store(monkeypatch):
    objects = {r["storage_key"] for r in music_library.entries()}
    monkeypatch.setattr(music_library.storage, "exists",
                        lambda k: k in objects)
    monkeypatch.setattr(music_library.storage, "copy_object",
                        lambda s, d: objects.add(d))


@pytest.fixture
def model(monkeypatch):
    """A fake model lane: answers with ``state['verdict']`` and records
    what it was shown."""
    state = {"verdict": None, "seen": [], "fail": False}

    def create(client, mdl, messages, **kw):
        state["seen"].append(json.loads(messages[-1]["content"]))
        if state["fail"]:
            raise RuntimeError("provider down")
        call = SimpleNamespace(function=SimpleNamespace(
            arguments=json.dumps(state["verdict"])))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(tool_calls=[call]))], usage=None)
    monkeypatch.setattr(llm, "create_with_dialect", create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    return state


def _offer_then_reply(reply):
    ctx, fake = tool_context(default_edl(20.0), user_message="add music",
                             job_id=9)
    ctx.llm_client = object()
    with pytest.raises(agent_tools.AskUser):
        agent_tools.ask_user(ctx, OFFER)
    ctx2, fake2 = tool_context(default_edl(20.0), user_message=reply,
                               job_id=10)
    ctx2.llm_client = object()
    fake2.kv = fake.kv
    return ctx2, fake2


def test_a_near_title_answer_to_the_offer_places_that_track(store, model):
    model["verdict"] = {"kind": "picked", "slug": CALM,
                        "why": "'Calm current' is option 1's title"}
    ctx, fake = _offer_then_reply("Calm current")
    out = music_library.add_library_music(ctx, CALM)
    assert out.startswith("EDL v7 -> v8"), out
    item = fake.rows[-1]["json"]["music"][0]
    assert item["provenance"] == "user_picked_library"
    picks = json.loads(fake.kv["music_picks:7"])
    assert picks[CALM]["via"] == "chat_read"
    shown = model["seen"][-1]
    assert shown["customer_reply"] == "Calm current"
    assert [t["slug"] for t in shown["tracks"]][0] == CALM
    assert shown["tracks"][0]["position_in_offer"] == 1


def test_the_model_reading_another_track_still_refuses_this_one(store, model):
    model["verdict"] = {"kind": "picked",
                        "slug": "chill-bubbles-lofi-bright-relaxed",
                        "why": "bubbles"}
    ctx, fake = _offer_then_reply("the bubbly sounding track")
    out = music_library.add_library_music(ctx, CALM)
    assert out.startswith("REJECTED") and "picked 'chill-bubbles" in out, out
    assert len(fake.rows) == 1


@pytest.mark.parametrize("verdict", [
    {"kind": "delegated", "slug": "", "why": "leaves it to the editor"},
    {"kind": "mood", "slug": "", "why": "only a mood"},
    {"kind": "picked", "slug": "not-a-real-track", "why": "invented"},
])
def test_delegation_moods_and_invented_slugs_are_no_pick(store, model,
                                                         verdict):
    model["verdict"] = verdict
    ctx, fake = _offer_then_reply("do what is suitable for the story")
    out = music_library.add_library_music(ctx, CALM)
    assert out.startswith("REJECTED") and "ask_user" in out, out
    assert len(fake.rows) == 1


def test_a_failed_reading_is_no_pick(store, model):
    model["fail"] = True
    ctx, fake = _offer_then_reply("Calm current")
    out = music_library.add_library_music(ctx, CALM)
    assert out.startswith("REJECTED"), out
    assert len(fake.rows) == 1


def test_no_client_is_no_pick_and_no_call(store, model, monkeypatch):
    monkeypatch.setattr(music_pick.config, "OPENAI_API_KEY", "")
    ctx, fake = tool_context(default_edl(20.0), user_message="Calm current")
    out = music_library.add_library_music(ctx, CALM)
    assert out.startswith("REJECTED")
    assert model["seen"] == []


def test_the_turn_binds_a_near_title_answer_before_the_editor_runs(model):
    model["verdict"] = {"kind": "picked", "slug": CALM, "why": "option 1"}
    ctx, fake = tool_context(default_edl(20.0), user_message="Calm current",
                             job_id=10)
    ctx.llm_client = object()
    note = music_gate.choice_note(ctx, "Calm current", OFFER,
                                  previous_reply_id=41)
    assert CALM in note and "explicit user pick" in note
    assert json.loads(fake.kv["music_picks:7"])[CALM]["offer_message_id"] \
        == 41


def test_an_exact_answer_needs_no_model(store, model):
    ctx, fake = _offer_then_reply("2")
    out = music_library.add_library_music(
        ctx, "chill-bubbles-lofi-bright-relaxed")
    assert out.startswith("EDL v7 -> v8"), out
    assert model["seen"] == []
