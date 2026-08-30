import hashlib
import os
import sys
from datetime import datetime, timezone

from flask import Flask, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import phone_status  # noqa: E402


def test_founder_local_day_uses_real_timezone_offset():
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    name, starts, ends = phone_status._day_window(
        "America/Los_Angeles", now=now)
    assert name == "America/Los_Angeles"
    assert starts == datetime(2026, 8, 29, 7, 0, tzinfo=timezone.utc)
    assert ends == now


def test_week_growth_uses_same_elapsed_prior_week():
    now = datetime(2026, 8, 29, 19, 0, tzinfo=timezone.utc)
    (name, current_start, current_end,
     previous_start, previous_cutoff) = phone_status._week_window(
        "America/Los_Angeles", now=now)
    assert name == "America/Los_Angeles"
    assert current_start == datetime(2026, 8, 24, 7, 0,
                                     tzinfo=timezone.utc)
    assert current_end == now
    assert previous_start == datetime(2026, 8, 17, 7, 0,
                                      tzinfo=timezone.utc)
    assert previous_cutoff == datetime(2026, 8, 22, 19, 0,
                                       tzinfo=timezone.utc)


def test_phone_token_is_digest_checked(monkeypatch):
    app = Flask(__name__)
    raw = "correct-horse-battery-staple"
    monkeypatch.setenv("VALMERA_PHONE_TOKEN_SHA256",
                       hashlib.sha256(raw.encode()).hexdigest())

    @phone_status.phone_token_required
    def protected():
        return jsonify({"ok": True})

    with app.test_request_context("/"):
        response, code = protected()
        assert code == 401
        assert response.get_json()["error"] == "Unauthorized"
    with app.test_request_context(
            "/", headers={"Authorization": "Bearer wrong"}):
        response, code = protected()
        assert code == 401
    with app.test_request_context(
            "/", headers={"Authorization": f"Bearer {raw}"}):
        assert protected().get_json() == {"ok": True}


def test_growth_rate_has_no_fabricated_zero_baseline():
    assert phone_status._growth_percent(8, 4) == 100.0
    assert phone_status._growth_percent(2, 4) == -50.0
    assert phone_status._growth_percent(2, 0) is None
