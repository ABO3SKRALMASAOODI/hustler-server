#!/usr/bin/env python3
"""Add a render box to the dispatcher's routing, with a parity smoke first.

Run on the owner's Mac by box/host/add_box.sh (or by hand):

    add_box.py show
    add_box.py register --id h2 --url https://1-2-3-4.sslip.io   # smoke-only
    add_box.py smoke    --id h2 --ip 1.2.3.4 [--n 10] [--out report.json]
    add_box.py promote  --id h2       # h2: the factory's home; h1: customers
                                      # first plus factory overflow
    add_box.py demote   --id h2       # no new work on h2 (calls stay reachable)

The routing document is the app_kv row ``box_routing``
(worker/box_routing.py). ``register`` lists the new box with no classes, so
nothing but a pinned owner-account job (``payload.box_pin``) can reach it.
``smoke`` renders the owner's own recent EDLs (user 60 only) on the new box
and, in the same minutes, on box 1, then requires: every job done, every new
box job run under a ``bx-<id>-`` identity, equal durations, and each pair
byte-identical or SSIM >= 0.999 (measured inside a slot of the new box).
``promote`` writes the two-box document only after a passing smoke report.

Writes are limited to the app_kv row box_routing and INSERTs of owner-account
video_jobs. The database URL is read from --env-file and never printed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid

OWNER = 60
DEFAULT_ENV = ("/Users/masaoodi/Documents/Valmera/hustler-server/"
               "backend/.env")
BOX_ID = re.compile(r"^h[2-9]$|^h[1-9][0-9]$")
CLASSES = ("customer", "trial", "owner")
SAFE_TOOLS = ("look_at", "audit_captions", "__state__", "audit_motion")
SSIM_GATE = 0.999


def _db_url(env_file):
    with open(env_file, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no DATABASE_URL in the env file")


def _connect(env_file, read_only):
    import psycopg2
    import psycopg2.extras
    options = "-c statement_timeout=60000"
    if read_only:
        options += " -c default_transaction_read_only=on"
    conn = psycopg2.connect(_db_url(env_file), connect_timeout=15,
                            options=options,
                            cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = read_only
    return conn


def _rows(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


# ── the routing document ───────────────────────────────────────────────────
def read_routing(conn):
    got = _rows(conn, "SELECT value FROM app_kv WHERE key = 'box_routing'")
    if not got:
        return {}
    value = got[0]["value"]
    value = json.loads(value) if isinstance(value, str) else value
    return value if isinstance(value, dict) else {}


def _boxes_of(doc):
    """The document's boxes, materialising today's single box as h1."""
    boxes = [dict(b) for b in doc.get("boxes") or [] if isinstance(b, dict)]
    if not boxes:
        url = str(doc.get("url") or "https://13-140-241-231.sslip.io")
        boxes = [{"id": "h1", "url": url.rstrip("/")}]
    for index, box in enumerate(boxes):
        box.setdefault("id", "h1" if index == 0 else "")
    return boxes


def with_box(doc, box_id, url, mode):
    """A new routing document with ``box_id`` at ``url`` in ``mode``:
    smoke (no classes), home (the owner factory's home, h1 customer-first
    plus factory overflow) or off (no new work). Other fields are kept."""
    if not BOX_ID.match(box_id):
        raise SystemExit(f"bad box id {box_id} (h2..h99; h1 is box 1)")
    out = json.loads(json.dumps(doc or {}))
    boxes = _boxes_of(out)
    existing = next((b for b in boxes if b["id"] == box_id), None)
    if existing and url and existing["url"].rstrip("/") != url.rstrip("/"):
        raise SystemExit(f"{box_id} is already {existing['url']}; never "
                         "give a listed id another host")
    if any(b["url"].rstrip("/") == (url or "").rstrip("/")
           and b["id"] != box_id for b in boxes):
        raise SystemExit(f"{url} is already listed under another id")
    if existing is None:
        if not url:
            raise SystemExit(f"{box_id} is not listed; give --url")
        existing = {"id": box_id, "url": url.rstrip("/")}
        boxes.append(existing)
    primary = boxes[0]
    if mode == "smoke":
        existing.update({"classes": [], "accepts": []})
    elif mode == "home":
        existing.update({"classes": ["owner"], "accepts": list(CLASSES)})
        primary.update({"classes": ["customer", "trial"],
                        "accepts": list(CLASSES)})
        out.setdefault("overflow_wait_s", 30)
    elif mode == "off":
        existing.update({"classes": [], "accepts": []})
        homed = set()
        for box in boxes:
            if box is not existing:
                homed |= set(box.get("classes", CLASSES))
        if not set(CLASSES) <= homed:
            # Nothing else is home for some class: box 1 is home of all.
            primary.pop("classes", None)
            primary.pop("accepts", None)
    else:
        raise SystemExit(f"bad mode {mode}")
    out["boxes"] = boxes
    out.pop("url", None)
    return out


def write_routing(conn, doc):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES ('box_routing', %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (json.dumps(doc),))
    conn.commit()


# ── the smoke ──────────────────────────────────────────────────────────────
def pick(conn, n):
    return _rows(conn, """
        SELECT DISTINCT ON (j.project_id) j.project_id,
               (j.payload->>'edl_version')::int AS edl_version,
               COALESCE(j.payload->>'quality', 'approval') AS quality
          FROM video_jobs j
          JOIN projects p ON p.id = j.project_id AND p.user_id = %s
         WHERE j.user_id = %s AND j.type = 'preview' AND j.state = 'done'
           AND j.created_at > NOW() - INTERVAL '14 days'
           AND j.payload ? 'edl_version'
           AND NOT EXISTS (SELECT 1 FROM video_jobs r
                            WHERE r.project_id = j.project_id
                              AND r.created_at > NOW() - INTERVAL '2 hours'
                              AND COALESCE(r.payload->>'box_smoke', '') = '')
         ORDER BY j.project_id DESC, j.id DESC
         LIMIT %s""", (OWNER, OWNER, n))


def enqueue(conn, project_id, jtype, payload):
    payload = dict(payload, execution_provider="box")
    payload.setdefault("execution_policy", "redesign")
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM projects WHERE id = %s",
                    (project_id,))
        owner = cur.fetchone()
        if not owner or owner["user_id"] != OWNER:
            raise SystemExit(f"project {project_id} is not the owner's")
        cur.execute("""INSERT INTO video_jobs (project_id, user_id, type,
                                               payload)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (project_id, OWNER, jtype, json.dumps(payload)))
        job_id = cur.fetchone()["id"]
    conn.commit()
    return job_id


def wait_terminal(conn, ids, timeout_s):
    deadline = time.time() + timeout_s
    while True:
        got = _rows(conn, "SELECT id, state FROM video_jobs WHERE "
                    "id = ANY(%s)", (list(ids),))
        live = [r for r in got if r["state"] not in ("done", "failed")]
        if not live or time.time() > deadline:
            return {r["id"]: r["state"] for r in got}
        time.sleep(10)


SSIM_SCRIPT = r'''
import json, os, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, "/app")
import storage
for a, b in json.loads(os.environ["PAIRS"]):
    work = tempfile.mkdtemp(dir="/tmp/valmera")
    try:
        pa, pb = os.path.join(work, "a.mp4"), os.path.join(work, "b.mp4")
        storage.download_to(a, pa)
        storage.download_to(b, pb)
        v = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", pa,
                            "-i", pb, "-lavfi", "[0:v][1:v]ssim", "-f",
                            "null", "-"], capture_output=True, text=True)
        m = re.search(r"SSIM .*All:([0-9.]+)", v.stderr)
        print(json.dumps({"a": a, "b": b,
                          "ssim": float(m.group(1)) if m else None}),
              flush=True)
    except Exception as exc:
        print(json.dumps({"a": a, "b": b, "error": str(exc)[:200]}),
              flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
'''


def ssim_on_box(ip, key, pairs, container="vbox-batch1"):
    if not pairs:
        return {}
    out = subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
         f"root@{ip}", "docker", "exec", "-i", "-e",
         "PAIRS=" + json.dumps(pairs).replace("'", ""), container,
         "python", "-"], input=SSIM_SCRIPT, capture_output=True, text=True,
        timeout=3600)
    got = {}
    for line in out.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        got[(row.get("a"), row.get("b"))] = row
    return got


def smoke(args):
    ro = _connect(args.env_file, True)
    doc = read_routing(ro)
    listed = {b["id"]: b for b in _boxes_of(doc)}
    if args.id not in listed:
        raise SystemExit(f"{args.id} is not in box_routing; run register")
    run = f"{args.id}-{time.strftime('%m%d%H%M')}-{uuid.uuid4().hex[:4]}"
    picks = pick(ro, args.n)
    if len(picks) < min(4, args.n):
        raise SystemExit(f"only {len(picks)} idle owner EDLs to render")
    rw = _connect(args.env_file, False)
    pairs, mcp = [], []
    for i, p in enumerate(picks):
        base = {"source": "box_smoke", "box_smoke": run,
                "quality": p["quality"], "edl_version": p["edl_version"],
                "force": True, "audio_model_review": False}
        if i % 4 == 3:
            base["force_lane"] = "batch"        # the batch slots too
        ids = {box: enqueue(rw, p["project_id"], "preview",
                            dict(base, box_pin=box))
               for box in ("h1", args.id)}
        pairs.append(dict(p, ids=ids, lane=base.get("force_lane",
                                                    "interactive")))
    for p in picks[:3]:
        calls = _rows(ro, """SELECT payload FROM video_jobs
                              WHERE project_id = %s AND user_id = %s
                                AND type = 'mcp_tool' AND state = 'done'
                                AND payload->>'tool' = ANY(%s)
                                AND payload->>'mutation' = 'false'
                              ORDER BY id DESC LIMIT 1""",
                      (p["project_id"], OWNER, list(SAFE_TOOLS)))
        if calls:
            payload = dict(calls[0]["payload"])
            for k in ("execution_provider", "execution_shape",
                      "cloudflare_busy_deferred", "box_wait_claims",
                      "box_wait_deferrals", "box_pin"):
                payload.pop(k, None)
            payload.update({"mcp_call_id": uuid.uuid4().hex[:16],
                            "box_smoke": run, "box_pin": args.id})
            mcp.append(enqueue(rw, p["project_id"], "mcp_tool", payload))
    every = [i for p in pairs for i in p["ids"].values()] + mcp
    print(f"smoke {run}: {len(pairs)} preview pairs + {len(mcp)} MCP reads "
          f"enqueued; waiting", flush=True)
    states = wait_terminal(ro, every, args.timeout)
    rows = {r["id"]: r for r in _rows(ro, """
        SELECT j.id, j.state, j.error, j.result->>'sha256' AS sha,
               (j.result->>'duration_s')::float AS dur,
               j.result->'timings'->>'compute_provider' AS ran_on,
               a.storage_key, r.call_id
          FROM video_jobs j
          LEFT JOIN assets a ON a.id = (j.result->>'render_asset_id')::int
          LEFT JOIN remote_executions r ON r.job_id = j.id
         WHERE j.id = ANY(%s)""", (every,))}
    tag = f"bx-{args.id}-"
    problems, compare = [], []
    for jid in every:
        row = rows.get(jid) or {}
        if row.get("state") != "done":
            problems.append(f"job {jid} is {states.get(jid)}: "
                            f"{str(row.get('error'))[:160]}")
    for p in pairs:
        one, new = rows.get(p["ids"]["h1"], {}), rows.get(p["ids"][args.id], {})
        if new.get("call_id") and not str(new["call_id"]).startswith(tag):
            problems.append(f"job {new['id']} ran as {new['call_id']}")
        if one.get("call_id") and str(one["call_id"]).startswith("bx-h"):
            problems.append(f"box 1 job {one['id']} ran as {one['call_id']}")
        if one.get("dur") is not None and new.get("dur") is not None and \
                abs(one["dur"] - new["dur"]) >= 0.05:
            problems.append(f"project {p['project_id']}: duration "
                            f"{one['dur']} vs {new['dur']}")
        if one.get("sha") and one.get("sha") != new.get("sha") and \
                one.get("storage_key") and new.get("storage_key"):
            compare.append([one["storage_key"], new["storage_key"]])
    for jid in mcp:
        call = str((rows.get(jid) or {}).get("call_id") or "")
        if call and not call.startswith(tag):
            problems.append(f"MCP job {jid} ran as {call}")
    measured = ssim_on_box(args.ip, args.key, compare) if args.ip else {}
    for a, b in compare:
        got = measured.get((a, b)) or {}
        if got.get("ssim") is None or got["ssim"] < SSIM_GATE:
            problems.append(f"{a} vs {b}: SSIM {got.get('ssim')} "
                            f"{got.get('error') or ''}".strip())
    report = {
        "run": run, "box": args.id, "pairs": len(pairs), "mcp": len(mcp),
        "done": sum(1 for j in every if (rows.get(j) or {}).get("state")
                    == "done"),
        "identical": sum(1 for p in pairs if (rows.get(p["ids"]["h1"]) or
                                              {}).get("sha") and
                         rows[p["ids"]["h1"]].get("sha") ==
                         (rows.get(p["ids"][args.id]) or {}).get("sha")),
        "ssim": {f"{a} | {b}": (measured.get((a, b)) or {}).get("ssim")
                 for a, b in compare},
        "lanes": sorted({p["lane"] for p in pairs}),
        "problems": problems, "passed": not problems,
        "jobs": {str(j): {k: (rows.get(j) or {}).get(k) for k in
                          ("state", "call_id", "ran_on", "dur", "sha")}
                 for j in every},
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=str)
    print(json.dumps({k: report[k] for k in (
        "run", "box", "pairs", "mcp", "done", "identical", "ssim", "lanes",
        "problems", "passed")}, indent=1, default=str))
    return 0 if report["passed"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("show", "register", "smoke",
                                        "promote", "demote"))
    ap.add_argument("--id", default="h2")
    ap.add_argument("--url", default="")
    ap.add_argument("--ip", default="")
    ap.add_argument("--key", default=os.path.expanduser(
        "~/.ssh/valmera_box_ed25519"))
    ap.add_argument("--env-file", default=DEFAULT_ENV)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--out", default="")
    ap.add_argument("--report", default="",
                    help="promote: the passing smoke report (required)")
    args = ap.parse_args(argv)
    if args.command == "smoke":
        return smoke(args)
    ro = _connect(args.env_file, True)
    doc = read_routing(ro)
    if args.command == "show":
        print(json.dumps(doc, indent=1))
        return 0
    mode = {"register": "smoke", "promote": "home", "demote": "off"}[
        args.command]
    if mode == "home":
        try:
            with open(args.report, encoding="utf-8") as fh:
                passed = json.load(fh)
        except (OSError, ValueError):
            raise SystemExit("promote needs --report <a passing smoke report>")
        if not (passed.get("passed") and passed.get("box") == args.id):
            raise SystemExit(f"the smoke report for {args.id} did not pass")
    new = with_box(doc, args.id, args.url, mode)
    write_routing(_connect(args.env_file, False), new)
    print(json.dumps(new, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
