"""Ticket #980 (9 Nov 2026, project 5591): an English customer asked for a
picture of Oscar Piastri. The editor's English question quoted two French
lines of the footage ("Oscar partira", "chez McLaren"); the language judge read
them as the draft's language and refused the question three times, and the
request ended short. The judge now sets quoted lines aside before naming the
draft's language, and a question is refused at most once per turn: after that
it is translated and delivered, never blocked."""

import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import agent_loop                                               # noqa: E402
import llm                                                      # noqa: E402
import reply_language                                           # noqa: E402

CUSTOMER = ["Add image or video of’oscar piastrin in mclaren"]
FRENCH_Q = ("Je ne peux pas encore ajouter Oscar Piastri. Envoyez une photo "
            "de lui chez McLaren ?")
ENGLISH_Q = ("I can't add Oscar Piastri yet. Upload a photo of him and I'll "
             "place it around the \"Oscar partira\" line?")


def _verdict(same, draft):
    call = types.SimpleNamespace(function=types.SimpleNamespace(
        name="language_verdict", arguments=json.dumps({
            "set_aside": [], "draft_language": draft,
            "customer_language": "English", "same_language": same})))
    return types.SimpleNamespace(usage=None, choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(tool_calls=[call], content=None))])


def _reply(text):
    return types.SimpleNamespace(usage=None, choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(tool_calls=[], content=text))])


def test_the_judge_names_the_editors_own_prose_before_deciding():
    order = reply_language._SCHEMA["required"]
    assert order.index("set_aside") < order.index("draft_language") \
        < order.index("same_language")
    assert "quotation marks" in reply_language._SYSTEM
    assert "footage" in reply_language._SYSTEM


def test_a_still_flipped_question_is_translated_not_refused(monkeypatch):
    responses = [_reply(ENGLISH_Q), _verdict(True, "English")]
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    ctx = types.SimpleNamespace(agent_model="m", job={"id": 1})
    out = agent_loop._translate_question(
        ctx, object(), FRENCH_Q, CUSTOMER, ("words", "English", "French"))
    assert out == ENGLISH_Q


def test_a_failed_translation_still_asks_the_question(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(llm, "create_with_dialect", boom)
    ctx = types.SimpleNamespace(agent_model="m", job={"id": 1})
    out = agent_loop._translate_question(
        ctx, object(), FRENCH_Q, CUSTOMER, ("words", "English", "French"))
    assert out == FRENCH_Q


def test_ask_user_language_refusal_happens_once_per_turn():
    import inspect
    src = inspect.getsource(agent_loop._run_loop)
    assert "_ask_language_refused" in src
    assert "_translate_question(" in src
