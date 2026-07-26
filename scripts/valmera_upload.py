#!/usr/bin/env python3
"""Upload a local file into a Valmera project over the MCP surface.

Exists because MCP tool arguments are JSON: a model can ask for a presigned
URL but it cannot hand us 900 MB of video. So the bytes go straight from this
machine to storage, and only the pointers travel over MCP. For a file under
64 MB the model can do this itself with one curl; past that it is a multipart
upload with an ETag per part, which is what this script is for.

    export VALMERA_MCP_TOKEN=vlm_mcp_...
    python3 scripts/valmera_upload.py ~/Movies/talk.mp4 [--kind original]

It uploads into whatever project the token currently has open (open_project
over MCP, or --project N here), then finishes the upload — which for a main
video starts the analysis. Prints what the model should do next.

Stdlib only, so it runs anywhere python3 does.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = os.getenv("VALMERA_MCP_URL",
                        "https://entrepreneur-bot-backend.onrender.com/mcp")
_id = [0]


def rpc(endpoint, token, method, params=None):
    _id[0] += 1
    body = json.dumps({"jsonrpc": "2.0", "id": _id[0], "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} failed: HTTP {e.code} {e.read()[:300].decode()}")
    if payload.get("error"):
        sys.exit(f"{method} failed: {payload['error'].get('message')}")
    return payload.get("result") or {}


def call_tool(endpoint, token, name, args):
    """Returns the tool's text. Session tools answer in prose by design — this
    script reads the JSON blob they embed, never a parsed schema."""
    res = rpc(endpoint, token, "tools/call", {"name": name, "arguments": args})
    parts = [c.get("text", "") for c in res.get("content", [])]
    text = "\n".join(parts)
    if res.get("isError"):
        sys.exit(text)
    return text


def embedded_json(text):
    """The upload_start reply is instructions for a model, with the machine
    part appended as JSON. Pull that out."""
    start = text.find("{")
    while start != -1:
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return None


def put(url, data, content_type=None):
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=data, method="PUT",
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=900) as r:
        # R2/S3 return the part's ETag in a header; multipart completion is
        # rejected without it.
        return r.headers.get("ETag")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--kind", default="original",
                    choices=["original", "clip", "music", "image"])
    ap.add_argument("--project", type=int, default=None,
                    help="Open this project first (else the token's current one)")
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    token = os.getenv("VALMERA_MCP_TOKEN", "").strip()
    if not token:
        sys.exit("Set VALMERA_MCP_TOKEN to your MCP token.")
    path = os.path.expanduser(args.path)
    if not os.path.isfile(path):
        sys.exit(f"No such file: {path}")
    size = os.path.getsize(path)
    name = os.path.basename(path)

    rpc(args.url, token, "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {},
         "clientInfo": {"name": "valmera_upload", "version": "1"}})
    if args.project:
        print(call_tool(args.url, token, "open_project",
                        {"project_id": args.project}).split("\n")[0])

    print(f"Preparing {name} ({size / 1e6:.1f} MB)…")
    # ONE upload_start call — a second one would mint a second storage key and
    # leave the first as an orphan half-upload.
    text = call_tool(args.url, token, "upload_start",
                     {"filename": name, "size_bytes": size,
                      "kind": args.kind})
    plan = embedded_json(text)
    if plan is None:
        sys.exit("Could not read the upload plan from:\n" + text)

    if plan["mode"] == "single":
        with open(path, "rb") as f:
            put(plan["url"], f.read(), plan.get("content_type"))
        finish = {"storage_key": plan["storage_key"], "filename": name,
                  "kind": args.kind}
    else:
        part_size = plan["part_size"]
        parts = []
        with open(path, "rb") as f:
            for p in plan["parts"]:
                chunk = f.read(part_size)
                etag = put(p["url"], chunk)
                parts.append({"part_number": p["part_number"], "etag": etag})
                print(f"  part {p['part_number']}/{len(plan['parts'])} "
                      f"uploaded", flush=True)
        finish = {"storage_key": plan["storage_key"], "filename": name,
                  "kind": args.kind, "upload_id": plan["upload_id"],
                  "parts": parts}

    print(call_tool(args.url, token, "upload_finish", finish))


if __name__ == "__main__":
    main()
