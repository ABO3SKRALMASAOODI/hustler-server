#!/usr/bin/env python3
"""A tiny OpenAI-compatible server that plays a scripted video editor.

Lets the full agent loop (worker/agent_loop.py) run end-to-end with no real
LLM key: point OPENAI_BASE_URL at this server. It reads actual tool results
from the conversation and reacts — e.g. it computes keep segments from the
real find_silences output — so the loop's tool plumbing is genuinely
exercised.

Usage: python scripts/fake_llm.py [port]
"""

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def last_executed_tool(messages):
    """Name of the tool whose result is the most recent message."""
    if not messages or messages[-1].get("role") != "tool":
        return None
    tcid = messages[-1].get("tool_call_id")
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if tc["id"] == tcid:
                    return tc["function"]["name"]
    return None


def find_result(messages, tool_name):
    """Content of the latest result for tool_name."""
    ids = set()
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if tc["function"]["name"] == tool_name:
                    ids.add(tc["id"])
    for m in reversed(messages):
        if m.get("role") == "tool" and m.get("tool_call_id") in ids:
            return m.get("content") or ""
    return ""


def plan_next(messages):
    """Returns (tool_name, args) or (None, final_text)."""
    last = last_executed_tool(messages)
    if last is None:
        return "get_video_info", {}
    if last == "get_video_info":
        return "find_silences", {"min_seconds": 0.7}
    if last == "find_silences":
        info = find_result(messages, "get_video_info")
        m = re.search(r"duration=([0-9.]+)s", info)
        duration = float(m.group(1)) if m else 60.0
        sil = [(float(a), float(b)) for a, b in
               re.findall(r"(\d+\.\d+)-(\d+\.\d+) \(", find_result(
                   messages, "find_silences"))]
        keep, cursor = [], 0.0
        for s, e in sorted(sil):
            if s - cursor > 0.25:
                keep.append([round(cursor, 2), round(s, 2)])
            cursor = max(cursor, e)
        if duration - cursor > 0.25:
            keep.append([round(cursor, 2), round(duration, 2)])
        if not keep:
            keep = [[0.0, duration]]
        return "keep_segments", {"segments": keep}
    if last == "keep_segments":
        return "add_captions", {"mode": "from_transcript"}
    if last == "add_captions":
        return "render_preview", {}
    if last == "render_preview":
        return None, ("Removed the dead air and burned word-timed captions. "
                      "The preview is on the right — happy to tighten it "
                      "further.")
    return None, "Done."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        tool, payload = plan_next(body.get("messages", []))
        if tool:
            message = {"role": "assistant", "content": None,
                       "tool_calls": [{
                           "id": f"call_{int(time.time()*1000) % 100000}",
                           "type": "function",
                           "function": {"name": tool,
                                        "arguments": json.dumps(payload)}}]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": payload}
            finish = "stop"
        resp = {"id": "fake-1", "object": "chat.completion",
                "created": int(time.time()), "model": "fake-editor",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8189
    print(f"fake LLM listening on :{port}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
