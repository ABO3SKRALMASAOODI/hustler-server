import json

import opus_supervision
from test_trial_toggle import Conn, NOW
from test_studio_plus_switch import app  # noqa: F401 - isolated Flask fixture


def test_economy_mode_preserves_access_and_limits_and_restores_quality():
    original = {"on": True, "model": "claude-haiku-5-5",
                "planner_model": "claude-opus-5-5", "planner_effort": "max",
                "daily_usd_cap": 0, "per_account_usd_cap": 0.12,
                "pacing": {"budget_usd": 62.22}, "test_user_ids": [29]}
    paid = '{"on":true,"default_model":"claude-haiku-5-5"}'
    conn = Conn(kv={"free_result": json.dumps(original),
                    "studio_for_all_plans": paid})
    for _ in range(2):
        value = opus_supervision.switch(conn, False, now=NOW)
        assert value["planner_model"] == "off"
        assert opus_supervision.status(conn)["mode"] == "economy"
        for key in original.keys() - {"planner_model"}:
            assert value[key] == original[key]
        assert conn.kv["studio_for_all_plans"] == paid
    value = opus_supervision.switch(conn, True, now=NOW)
    assert value["planner_model"] == original["planner_model"]
    assert opus_supervision.status(conn)["mode"] == "quality"
    assert all(key == "free_result" for key, _ in conn.writes)


def test_missing_flag_can_disable_planner_without_enabling_trials():
    conn = Conn()
    value = opus_supervision.switch(conn, False, now=NOW)
    assert "on" not in value
    assert opus_supervision.status(conn)["owner_on"] is False
    opus_supervision.switch(conn, True, now=NOW)
    assert opus_supervision.status(conn)["owner_on"] is True
    assert "on" not in json.loads(conn.kv["free_result"])


def test_admin_route_rejects_other_users_and_invalid_bodies(app, monkeypatch):
    import jwt
    from contextlib import contextmanager
    from routes import admin_video
    conn = Conn()
    @contextmanager
    def db():
        yield conn
    monkeypatch.setattr(admin_video, "adb", db)
    def headers(email):
        return {"Authorization": "Bearer " + jwt.encode(
            {"sub": "1", "email": email}, app.config["SECRET_KEY"], algorithm="HS256")}
    admin = headers("thevalmera@gmail.com")
    client = app.test_client()
    url = "/admin/video/opus-supervision"
    assert client.post(url, json={"on": False}, headers=headers("other@example.com")).status_code == 403
    for body in ({}, [], {"on": "off"}, {"on": 0}):
        assert client.post(url, json=body, headers=admin).status_code == 400
    assert not conn.writes
    for on, mode in ((False, "economy"), (True, "quality"), (False, "economy")):
        result = client.post(url, json={"on": on}, headers=admin)
        assert result.status_code == 200
        assert result.get_json()["mode"] == mode
