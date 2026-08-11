"""Tiny scale-to-zero bridge from Render to the Cloud Run Jobs API."""

import base64
import hmac
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SECRET = os.getenv("REMOTE_EXECUTOR_SECRET", "")
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "valmera")
REGION = os.getenv("BATCH_REGION", "us-central1")
JOB_NAME = os.getenv("REMOTE_BATCH_JOB_NAME", "valmera-batch")


def _authorized(headers):
    got = headers.get("Authorization", "")
    return bool(SECRET) and got.startswith("Bearer ") and hmac.compare_digest(
        got[7:], SECRET)


def _token():
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)["access_token"]


def _launch(job, token):
    raw = json.dumps(job, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    body = json.dumps({
        "overrides": {
            "containerOverrides": [{"args": ["batch_runner.py", encoded]}],
            "taskCount": 1,
            "timeout": "3600s",
        }
    }).encode("utf-8")
    url = (f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/"
           f"{REGION}/jobs/{JOB_NAME}:run")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/health", "/healthz"):
            return self.send_json(200, {"status": "ok", "job": JOB_NAME})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/launch":
            return self.send_json(404, {"error": "not found"})
        if not _authorized(self.headers):
            return self.send_json(401, {"error": "unauthorized"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            job = (json.loads(self.rfile.read(size) or b"{}").get("job") or {})
            if job.get("type") not in ("final", "index") or not job.get("id"):
                return self.send_json(400, {"error": "unsupported batch job"})
            # Token retrieval happens before the Jobs API request. A failure
            # here is definitely safe to fall back from; only failures after
            # the POST begins are ambiguous.
            try:
                token = _token()
            except Exception as exc:
                return self.send_json(503, {
                    "error": f"launcher identity unavailable: {str(exc)[:300]}",
                    "safe_to_fallback": True,
                })
            operation = _launch(job, token)
            return self.send_json(200, {
                "launched": True,
                "operation": operation.get("name") or "accepted",
            })
        except urllib.error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", "replace")
            # A Jobs API HTTP rejection did not accept an execution. The
            # dispatcher may safely clear its idempotency mark and use the
            # existing request service for this one job.
            return self.send_json(503, {
                "error": f"Jobs API refused launch ({exc.code}): {detail}",
                "safe_to_fallback": True,
            })
        except Exception as exc:
            # A timeout after sending is ambiguous: never tell the dispatcher
            # to run an 8-vCPU duplicate. Its durable launch mark + reaper are
            # the recovery path if no execution actually appears.
            return self.send_json(202, {
                "launched": True, "ambiguous": True,
                "operation": "response-unknown", "warning": str(exc)[:300],
            })


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))),
                        Handler).serve_forever()
