#!/usr/bin/env python3
"""PUT a local file to the presigned URLs of a Valmera upload_start plan.

Agents used to hand-copy upload_start's presigned URLs (16 parts for a long
podcast) into curl commands: slow, and one mistyped URL or ETag costs the
upload. Save the upload_start result instead (its structuredContent plan, the
JSON that closes its text, or the whole tool result) as plan.json, then:

    python3 upload_parts.py <LOCAL FILE> plan.json

It checks the file's size against the plan, PUTs every part (4 at a time,
3 tries each, streamed from disk), keeps finished parts in
<out>.parts.json so a re-run resumes, and writes the exact upload_finish
arguments (storage_key, upload_id, parts with their ETags in order, ...) to
--out (default: upload_finish.json beside the plan) and stdout. Then call
upload_finish with exactly those arguments. Needs only python3, no token.

exit 0 on success, 2 on a bad plan or a size mismatch, 1 when a part fails.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request

TRIES = 3
TIMEOUT_S = 900


class PlanError(ValueError):
    pass


def load_plan(path: Path) -> dict:
    """The plan dict from a saved upload_start result in any of its shapes:
    the plan itself, the tool result ({"content", "structuredContent"}), a
    JSON-RPC envelope ({"result": ...}) or the result's text."""
    raw = path.read_text(encoding="utf-8").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = _last_json_object(raw)
    for _ in range(4):
        if not isinstance(data, dict) or data.get("mode"):
            break
        if isinstance(data.get("result"), dict):
            data = data["result"]
        elif isinstance(data.get("structuredContent"), dict):
            data = data["structuredContent"]
        elif isinstance(data.get("content"), list):
            data = _last_json_object("\n".join(
                c.get("text", "") for c in data["content"] if isinstance(c, dict)))
        else:
            break
    if not isinstance(data, dict) or data.get("mode") not in ("single", "multipart"):
        raise PlanError("not an upload_start plan: save the upload_start result "
                        "(its structuredContent, or the JSON at the end of its text)")
    if not isinstance(data.get("upload_finish_arguments"), dict):
        raise PlanError("the plan has no upload_finish_arguments")
    if data["mode"] == "single" and not data.get("url"):
        raise PlanError("a single-mode plan needs its url")
    if data["mode"] == "multipart" and not data.get("parts"):
        raise PlanError("a multipart plan needs its parts")
    return data


def _last_json_object(text: str):
    """The last top-level {...} that parses, scanning from the end."""
    decoder = json.JSONDecoder()
    start = text.rfind("{")
    while start != -1:
        try:
            value, end = decoder.raw_decode(text[start:])
            if isinstance(value, dict) and value.get("mode"):
                return value
        except json.JSONDecodeError:
            pass
        start = text.rfind("{", 0, start)
    raise PlanError("no upload plan JSON found in the saved text")


class _Slice:
    """A read-only window of a file, streamed (never loaded whole)."""

    def __init__(self, path: Path, offset: int, length: int):
        self.f = path.open("rb")
        self.f.seek(offset)
        self.left = length

    def read(self, n: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left)
        chunk = self.f.read(n)
        self.left -= len(chunk)
        return chunk

    def close(self) -> None:
        self.f.close()


def put(url: str, path: Path, offset: int, length: int,
        content_type: str | None = None) -> str:
    """PUT length bytes at offset; returns the response ETag."""
    last = None
    for attempt in range(1, TRIES + 1):
        body = _Slice(path, offset, length)
        req = urllib.request.Request(url, data=body, method="PUT")
        req.add_header("Content-Length", str(length))
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                etag = r.headers.get("ETag")
                if not etag and content_type is None:
                    raise OSError("the part was accepted without an ETag")
                return etag or ""
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 403):
                break                       # an expired or wrong URL: retrying will not help
            time.sleep(min(30, 2 ** attempt))
        finally:
            body.close()
    raise OSError(f"PUT failed after {attempt} tries: {last}")


def upload(local: Path, plan: dict, out: Path, workers: int = 4,
           log=print) -> dict:
    size = local.stat().st_size
    declared = plan.get("size_bytes")
    if declared is not None and int(declared) != size:
        raise PlanError(f"{local.name} is {size} bytes but the plan was made for "
                        f"{declared}: call upload_start again with size_bytes={size}")
    args = dict(plan["upload_finish_arguments"])
    if plan["mode"] == "single":
        put(plan["url"], local, 0, size, plan.get("content_type") or
            "application/octet-stream")
        log(f"uploaded {local.name} ({size} bytes) in one PUT")
    else:
        progress = out.with_name(out.name + ".parts.json")
        done: dict[str, str] = {}
        if progress.exists():
            saved = json.loads(progress.read_text())
            if saved.get("upload_id") == plan.get("upload_id"):
                done = {str(k): v for k, v in (saved.get("etags") or {}).items()}
        parts = plan["parts"]
        total = sum(int(p["length"]) for p in parts)
        if total != size:
            raise PlanError(f"the plan's parts cover {total} bytes, the file has {size}")
        lock = threading.Lock()

        def one(p: dict) -> None:
            n = str(p["part_number"])
            if n in done:
                return
            etag = put(p["url"], local, int(p["offset"]), int(p["length"]))
            with lock:
                done[n] = etag
                progress.write_text(json.dumps({"upload_id": plan.get("upload_id"),
                                                "etags": done}))
                log(f"part {n}/{len(parts)} done ({len(done)} of {len(parts)})")

        failed = []
        with futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            jobs = {pool.submit(one, p): p for p in parts}
            for job in futures.as_completed(jobs):
                try:
                    job.result()
                except OSError as exc:
                    failed.append(f"part {jobs[job]['part_number']}: {exc}")
        if failed:
            raise OSError("; ".join(failed) + f" — re-run the same command to resume "
                          f"({len(done)} of {len(parts)} parts are kept)")
        args["parts"] = [{"part_number": int(n), "etag": done[n]}
                         for n in sorted(done, key=int)]
    out.write_text(json.dumps(args, indent=1))
    return args


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("local_file")
    ap.add_argument("plan", help="the saved upload_start result (JSON or its text)")
    ap.add_argument("--out", help="where to write the upload_finish arguments "
                    "(default: upload_finish.json beside the plan)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    local, plan_path = Path(a.local_file).expanduser(), Path(a.plan).expanduser()
    out = Path(a.out).expanduser() if a.out else plan_path.with_name("upload_finish.json")
    try:
        if not local.is_file():
            raise PlanError(f"no such file: {local}")
        args = upload(local, load_plan(plan_path), out, a.workers,
                      log=lambda m: print(m, file=sys.stderr))
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(args))
    print(f"upload_finish arguments written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
