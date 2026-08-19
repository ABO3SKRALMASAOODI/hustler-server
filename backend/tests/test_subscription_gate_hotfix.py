from types import SimpleNamespace

import subscription_gate_hotfix as gate_fix


class FakeCursor:
    def __init__(self, has_original):
        self.has_original = has_original
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchone(self):
        return {"has_original": self.has_original}


def _module(original_result=True):
    def original_gate(_cur, _user_id):
        return original_result

    return SimpleNamespace(
        _subscribe_gate_applies=original_gate,
        _trial_gate_applies=original_gate,
    )


def test_empty_project_keeps_concierge_open(monkeypatch):
    routes = _module(original_result=True)
    monkeypatch.setattr(
        gate_fix,
        "request",
        SimpleNamespace(
            endpoint="video.post_message",
            view_args={"project_id": 41},
        ),
    )

    gate_fix.install_subscription_gate_hotfix(routes)

    assert routes._subscribe_gate_applies(FakeCursor(False), 9) is False
    assert routes._trial_gate_applies is routes._subscribe_gate_applies


def test_uploaded_project_still_uses_subscription_gate(monkeypatch):
    routes = _module(original_result=True)
    monkeypatch.setattr(
        gate_fix,
        "request",
        SimpleNamespace(
            endpoint="video.post_message",
            view_args={"project_id": 42},
        ),
    )

    gate_fix.install_subscription_gate_hotfix(routes)

    assert routes._subscribe_gate_applies(FakeCursor(True), 9) is True


def test_other_surfaces_keep_existing_gate(monkeypatch):
    routes = _module(original_result=True)
    monkeypatch.setattr(
        gate_fix,
        "request",
        SimpleNamespace(
            endpoint="video.start_shorts",
            view_args={"project_id": 43},
        ),
    )

    gate_fix.install_subscription_gate_hotfix(routes)

    cur = FakeCursor(False)
    assert routes._subscribe_gate_applies(cur, 9) is True
    assert cur.statements == []


def test_install_is_idempotent(monkeypatch):
    routes = _module(original_result=True)
    monkeypatch.setattr(
        gate_fix,
        "request",
        SimpleNamespace(
            endpoint="video.post_message",
            view_args={"project_id": 44},
        ),
    )

    gate_fix.install_subscription_gate_hotfix(routes)
    first = routes._subscribe_gate_applies
    gate_fix.install_subscription_gate_hotfix(routes)

    assert routes._subscribe_gate_applies is first
