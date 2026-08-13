#!/usr/bin/env python3
"""Copy the proven Cloud Run runtime environment into a Modal Secret.

Values are piped through a mode-0600 temporary JSON file and are never printed.
Requires active ``gcloud`` and ``modal`` logins. Safe to rerun.
"""

import json
import os
import stat
import subprocess
import tempfile


PROJECT = os.getenv("GCP_PROJECT", "valmera")
REGION = os.getenv("GCP_REGION", "us-central1")
SERVICE = os.getenv("GCP_EXECUTOR_SERVICE", "valmera-executor")
SECRET = os.getenv("MODAL_EXECUTOR_SECRET_NAME",
                   "valmera-executor-production")
ENVIRONMENT = os.getenv("MODAL_ENVIRONMENT", "main")


def _service_env():
    raw = subprocess.check_output([
        "gcloud", "run", "services", "describe", SERVICE,
        "--project", PROJECT, "--region", REGION, "--format=json",
    ], text=True)
    service = json.loads(raw)
    containers = service["spec"]["template"]["spec"]["containers"]
    env = {}
    for item in containers[0].get("env", []):
        name = item.get("name")
        # Platform and role settings are owned by modal_app.py. Secret-backed
        # Cloud Run valueFrom entries cannot be exported and are reported.
        if not name or name in {"PORT", "K_SERVICE", "WORKER_ROLE",
                                "EXECUTOR_PROVIDER",
                                "MODAL_EXECUTOR_PROFILE"}:
            continue
        if "value" in item:
            env[name] = str(item["value"])
    # Agent turns running on Modal can launch nested render-tool calls. Keep
    # Cloud Run's public service URL available as a pre-launch fallback; the
    # existing shared bearer secret still authenticates every request.
    service_url = ((service.get("status") or {}).get("url") or "").strip()
    if service_url:
        env.setdefault("REMOTE_EXECUTOR_URL", service_url)
    required = {"DATABASE_URL", "S3_ENDPOINT", "S3_ACCESS_KEY_ID",
                "S3_SECRET_ACCESS_KEY", "S3_BUCKET"}
    missing = sorted(required - env.keys())
    if missing:
        raise SystemExit(
            "Cloud Run is missing exportable required variables: "
            + ", ".join(missing))
    return env


def main():
    env = _service_env()
    path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", prefix="valmera-modal-secret-", suffix=".json",
                delete=False) as handle:
            path = handle.name
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            json.dump(env, handle)
        subprocess.run([
            "modal", "secret", "create", SECRET,
            "--from-json", path, "--force", "--env", ENVIRONMENT,
        ], check=True)
    finally:
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    print(f"Modal secret {SECRET!r} updated with {len(env)} runtime values "
          f"in environment {ENVIRONMENT!r}; no values were printed.")


if __name__ == "__main__":
    main()
