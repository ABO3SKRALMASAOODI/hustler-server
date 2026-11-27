from unittest.mock import MagicMock, Mock

import jwt
import pytest
from flask import Flask

from routes import enterprise as route


REFERENCE = "1be80965-6295-46d1-af53-122d1d3ba668"
SESSION = "s_8f1e3a76-4ace-4f03-9f2a-247c8268b1a9"
FORM = {"reference": REFERENCE, "company": "Studio Example",
        "volume": "12 per month", "message": "Interviews and captions for our team.",
        "session_id": SESSION, "email": "not-the-account@example.test"}


@pytest.fixture
def setup(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="enterprise-local-test-key-000000000000")
    app.register_blueprint(route.enterprise_bp)
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(route, "get_db", Mock(return_value=conn))
    sender = Mock(return_value=True)
    monkeypatch.setattr(route.brevo_delivery, "send_email", sender)
    token = jwt.encode({"sub": "42"}, app.config["SECRET_KEY"], algorithm="HS256")
    return app.test_client(), {"Authorization": f"Bearer {token}"}, cur, sender


def test_verified_request_records_only_reference_and_routes_reply_to_account(setup):
    client, headers, cur, sender = setup
    cur.fetchone.side_effect = [
        {"email": "team@example.test"}, None, {"account": 0, "total": 2}, {"id": 882},
    ]
    response = client.post("/enterprise/inquiry", json=FORM, headers=headers)
    assert response.status_code == 200 and response.json == {"state": "accepted"}
    assert response.headers["Cache-Control"] == "no-store"
    payload = sender.call_args.args[0]
    assert payload["to"] == [{"email": route.SUPPORT_EMAIL}]
    assert payload["replyTo"] == {"email": "team@example.test"}
    assert FORM["message"] in payload["textContent"]
    assert FORM["email"] not in payload["textContent"]
    assert payload["headers"]["IdempotencyKey"] == REFERENCE
    assert f"enterprise-{REFERENCE}" in payload["tags"]
    sql = cur.execute.call_args_list
    inserted = next(call.args[1][2].adapted for call in sql if "INSERT INTO client_events" in call.args[0])
    assert inserted == {"state": "pending", "reference": REFERENCE, "session_id": SESSION}
    assert sql[-1].args[1] == ("accepted", 882, route.EVENT_KIND)


@pytest.mark.parametrize(("state", "status"), [("accepted", 200), ("pending", 202), ("unavailable", 503)])
def test_repeated_reference_does_not_send_again(setup, state, status):
    client, headers, cur, sender = setup
    cur.fetchone.side_effect = [{"email": "team@example.test"}, {"state": state}]
    response = client.post("/enterprise/inquiry", json=FORM, headers=headers)
    assert response.status_code == status
    assert response.json == {"state": state}
    sender.assert_not_called()


@pytest.mark.parametrize("malformed", [{"reference": "wrong"}, {"company": "  "},
                                      {"volume": [12]}, {"message": "a" * 2001}])
def test_malformed_inquiry_does_not_open_db_or_send(setup, malformed):
    client, headers, cur, sender = setup
    assert client.post("/enterprise/inquiry", json={**FORM, **malformed}, headers=headers).status_code == 400
    cur.execute.assert_not_called()
    sender.assert_not_called()


def test_requires_verified_account_and_limits_admission(setup):
    client, headers, cur, sender = setup
    assert client.post("/enterprise/inquiry", json=FORM).status_code == 401
    cur.fetchone.return_value = None
    assert client.post("/enterprise/inquiry", json=FORM, headers=headers).status_code == 403
    cur.fetchone.side_effect = [{"email": "team@example.test"}, None, {"account": 0, "total": 20}]
    assert client.post("/enterprise/inquiry", json=FORM, headers=headers).status_code == 429
    sender.assert_not_called()


def test_mail_failure_suggests_fallback_and_omits_untracked_session(setup):
    client, headers, cur, sender = setup
    cur.fetchone.side_effect = [
        {"email": "team@example.test"}, None, {"account": 0, "total": 0}, {"id": 882},
    ]
    sender.return_value = False
    response = client.post("/enterprise/inquiry", json={**FORM, "session_id": "private"}, headers=headers)
    assert response.status_code == 503 and response.json == {"state": "unavailable"}
    sql = cur.execute.call_args_list
    inserted = next(call.args[1][2].adapted for call in sql if "INSERT INTO client_events" in call.args[0])
    assert "session_id" not in inserted
    assert sql[-1].args[1] == ("unavailable", 882, route.EVENT_KIND)
