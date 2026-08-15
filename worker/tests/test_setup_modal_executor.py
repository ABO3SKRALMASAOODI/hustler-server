import json

from worker import setup_modal_executor


def _service(proxy_item):
    env = [
        {"name": "DATABASE_URL", "value": "postgres://db"},
        {"name": "S3_ENDPOINT", "value": "https://s3.example"},
        {"name": "S3_ACCESS_KEY_ID", "value": "access"},
        {"name": "S3_SECRET_ACCESS_KEY", "value": "secret"},
        {"name": "S3_BUCKET", "value": "bucket"},
        proxy_item,
    ]
    return {
        "spec": {"template": {"spec": {"containers": [{"env": env}]}}},
        "status": {"url": "https://executor.example"},
    }


def test_service_env_resolves_secret_manager_values(monkeypatch):
    service = _service({
        "name": "YTDLP_PROXY",
        "valueFrom": {"secretKeyRef": {
            "name": "valmera-ytdlp-proxy", "key": "latest"}},
    })
    calls = []

    def fake_check_output(command, text):
        calls.append(command)
        if command[:3] == ["gcloud", "run", "services"]:
            return json.dumps(service)
        assert command == [
            "gcloud", "secrets", "versions", "access", "latest",
            "--secret", "valmera-ytdlp-proxy", "--project",
            setup_modal_executor.PROJECT,
        ]
        return "http://proxy-user:proxy-pass@proxy.example:8000\n"

    monkeypatch.setattr(
        setup_modal_executor.subprocess, "check_output", fake_check_output)

    env = setup_modal_executor._service_env()

    assert env["YTDLP_PROXY"] == (
        "http://proxy-user:proxy-pass@proxy.example:8000")
    assert env["REMOTE_EXECUTOR_URL"] == "https://executor.example"
    assert len(calls) == 2


def test_service_env_keeps_inline_proxy(monkeypatch):
    service = _service({"name": "YTDLP_PROXY",
                        "value": "http://inline.example:8000"})

    monkeypatch.setattr(
        setup_modal_executor.subprocess, "check_output",
        lambda command, text: json.dumps(service))

    env = setup_modal_executor._service_env()

    assert env["YTDLP_PROXY"] == "http://inline.example:8000"
