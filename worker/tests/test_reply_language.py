"""Ticket #387: a question in the customer's own language must reach them.

Project 5472, 5 Nov 2026: three Spanish ask_user questions to a customer who
wrote Spanish were refused, because the check read the product's English nudges
as the customer. Language is judged by a model from what the customer typed.
"""

import inspect
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import agent_loop                                               # noqa: E402
import llm                                                      # noqa: E402
import reply_language                                           # noqa: E402

CUSTOMER = ["Quiero que subas los subtítulos y cambies la música del video"]
QUESTION = ("¿Prefieres conservar la música actual o que busque una pista "
            "nueva para este video?")
NUDGE = ("[system: EXECUTION CHECKPOINT — 8 read-only tool calls have "
         "completed and no EDL write has landed. Do not reread the same "
         "evidence. Load another skill only when it materially informs the "
         "next concrete write.]")


def _verdict(same, customer="Spanish", draft="Spanish"):
    call = types.SimpleNamespace(function=types.SimpleNamespace(
        name="language_verdict", arguments=json.dumps({
            "same_language": same, "customer_language": customer,
            "draft_language": draft})))
    return types.SimpleNamespace(
        usage=None, choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(tool_calls=[call], content=None))])


def _reply(text):
    return types.SimpleNamespace(
        usage=None, choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(tool_calls=[], content=text))])


def _ctx(texts):
    return types.SimpleNamespace(
        job={"id": 1}, agent_model="m", customer_texts=list(texts))


def test_the_judge_sees_only_what_the_customer_typed(monkeypatch):
    seen = {}

    def create(_client, _model, messages, **_kw):
        seen["payload"] = messages[-1]["content"]
        return _verdict(True)

    monkeypatch.setattr(llm, "create_with_dialect", create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    messages = [{"role": "user", "content": CUSTOMER[0]},
                {"role": "user", "content": NUDGE}]
    out = agent_loop._enforce_reply_language(
        _ctx(CUSTOMER), object(), messages, [], QUESTION, CUSTOMER[0], {})
    assert out == QUESTION
    assert "EXECUTION CHECKPOINT" not in seen["payload"]
    assert CUSTOMER[0] in seen["payload"]


def test_customer_texts_ignore_the_prompts_user_rows():
    assert agent_loop._customer_texts(_ctx(CUSTOMER), "otra cosa") == [
        CUSTOMER[0], "otra cosa"]
    assert NUDGE not in " ".join(agent_loop._customer_texts(_ctx(CUSTOMER), ""))


def test_the_verdict_catches_a_same_script_flip(monkeypatch):
    responses = [_verdict(False, "English", "German"),
                 _reply("The pauses are shorter now."),
                 _verdict(True, "English", "English")]
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    honesty = {}
    out = agent_loop._enforce_reply_language(
        _ctx(["Cut the silences"]), object(), [], [],
        "Die langen Pausen wurden gekürzt.", "Cut the silences", honesty)
    assert out == "The pauses are shorter now."
    assert honesty["language_flip"] == "English->German (words)"
    assert honesty["language_fixed"] is True


def test_a_rewrite_the_judge_refuses_keeps_the_draft(monkeypatch):
    responses = [_verdict(False, "English", "German"), _reply("Noch Deutsch."),
                 _verdict(False, "English", "German")]
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    draft = "Die langen Pausen wurden gekürzt."
    assert agent_loop._enforce_reply_language(
        _ctx(["Cut the silences"]), object(), [], [], draft,
        "Cut the silences", {}) == draft


def test_a_script_change_the_customer_asked_for_is_left_alone():
    arabic = "اكتب الرد باللغة الإنجليزية من فضلك واختصر الفيديو"
    english = "The video is shorter now and the captions are larger."
    assert agent_loop._language_flip(
        arabic, arabic, english, {"same_language": True}) is None
    assert agent_loop._language_flip(arabic, arabic, english)[0] == "script"


def test_any_failure_means_do_not_act(monkeypatch):
    def down(*_a, **_k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm, "create_with_dialect", down)
    assert reply_language.judge(object(), "m", CUSTOMER, QUESTION) is None
    assert reply_language.judge(None, "m", CUSTOMER, QUESTION) is None
    assert reply_language.judge(object(), "m", [], QUESTION) is None
    assert agent_loop._language_flip(CUSTOMER[0], CUSTOMER[0], QUESTION) is None


def test_no_word_list_judges_language_any_more():
    for name in ("_LANG_MARKERS", "_LANG_MARKERS_RAW", "_marker_lang",
                 "_marker_hits", "_accented_flip"):
        assert not hasattr(agent_loop, name)
    assert "_marker" not in inspect.getsource(agent_loop._language_flip)
