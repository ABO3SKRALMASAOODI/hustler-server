from argparse import Namespace
import io
import json

from scripts import ops_review


def verdict(**kwargs):
    defaults = dict(cause_class=None, verdict="delivered_good", root_cause=None,
                    score=8, reasoning="The titles obscure the speaker.",
                    request_summary="Caption the interview.")
    return Namespace(**dict(defaults, **kwargs))


def test_legacy_good_delivery_below_the_bar_gets_a_model_diagnosis(monkeypatch):
    seen = []
    def answer(request, timeout):
        seen.append(json.loads(request.data))
        return io.BytesIO(b'{"choices":[{"message":{"content":"tool"}}]}')
    monkeypatch.setattr(ops_review.urllib.request, "urlopen", answer)
    cls = ops_review._review_cause_class(verdict(), {"score_exact": 8.5},
                                         [{"caption": "Letters are doubled.", "storage_key": "private-key"}])
    assert cls == "tool"
    assert seen[0]["model"] == "gpt-6.2-sol"
    prompt = seen[0]["messages"][-1]["content"]
    assert "Letters are doubled." in prompt and "private-key" not in prompt


def test_explicit_class_legacy_prefix_and_perfect_delivery_need_no_call(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("unexpected classifier call")
    monkeypatch.setattr(ops_review.urllib.request, "urlopen", unexpected)
    assert ops_review._review_cause_class(verdict(cause_class="product"), {}, []) == "product"
    assert ops_review._review_cause_class(verdict(root_cause="tool: offset shadow"), {}, []) == "tool"
    assert ops_review._review_cause_class(verdict(score=10), {"score_exact":10}, []) is None


def test_ticket_class_uses_saved_diagnosis_or_its_own_summary(monkeypatch):
    from unittest.mock import MagicMock
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [{"history": [{"cause_class": "production"}]}, None]
    monkeypatch.setattr(ops_review, "connect", lambda: conn)
    seen = []

    def classify(diagnosis):
        assert conn.close.call_count == 2
        seen.append(diagnosis)
        return "product"

    monkeypatch.setattr(ops_review, "_infer_cause_class", classify)
    ticket = Namespace(cause="legacy-key", title="Checkout", summary="Paid user still on trial.")
    assert ops_review._legacy_ticket_class(ticket) == "production"
    assert ops_review._legacy_ticket_class(ticket) == "product"
    assert seen == [dict(cause=ticket.cause, title=ticket.title, summary=ticket.summary)]
