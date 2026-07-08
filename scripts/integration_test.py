#!/usr/bin/env python3
"""End-to-end acceptance test for the video editor core.

Exercises the REAL code paths: Flask API (via test client) -> presigned
upload to S3-compatible storage -> worker index job (ffmpeg/whisper/scenes)
-> chat message -> agent loop (against OPENAI_BASE_URL — use
scripts/fake_llm.py for a keyless run) -> EDL versions -> preview render ->
confirmation-gated final render.

Requires services reachable via env (see scripts/run_local_integration.sh
for a no-docker macOS harness, or docker-compose.yml for the containers):
  DATABASE_URL, S3_ENDPOINT, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
  S3_BUCKET, OPENAI_BASE_URL, OPENAI_API_KEY

The worker must be running against the same env. Asserts:
  - index completes; >3 shots and >3 silences found
  - agent turn writes a new EDL (output shorter than input) + captions
  - preview renders; duration < source duration
  - final render only via the explicit confirm endpoint, at source res
"""

import json
import os
import sys
import time

import jwt
import psycopg2
import requests
from psycopg2.extras import RealDictCursor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

DB_URL = os.environ["DATABASE_URL"]
TIMEOUT_INDEX = int(os.getenv("TEST_INDEX_TIMEOUT", "900"))
TIMEOUT_AGENT = int(os.getenv("TEST_AGENT_TIMEOUT", "600"))
TIMEOUT_RENDER = int(os.getenv("TEST_RENDER_TIMEOUT", "600"))


def die(msg):
    print(f"\nFAIL: {msg}")
    sys.exit(1)


def ok(msg):
    print(f"  ok  {msg}")


def db():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


JOB_TIMES = {}


def wait_job(job_id, timeout, label, poll_s=1.0):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT state, progress, error, result FROM video_jobs "
                        "WHERE id = %s", (job_id,))
            row = cur.fetchone()
        if row["state"] != last:
            print(f"    [{label}] {row['state']} {row['progress']}% "
                  f"(t+{time.time() - t0:.1f}s)")
            last = row["state"]
        if row["state"] == "done":
            elapsed = time.time() - t0
            JOB_TIMES.setdefault(label, []).append(elapsed)
            timings = (row.get("result") or {}).get("timings")
            print(f"    [{label}] total {elapsed:.1f}s"
                  + (f" timings={json.dumps(timings)}" if timings else ""))
            return row
        if row["state"] == "failed":
            die(f"{label} job failed: {row['error']}")
        time.sleep(poll_s)
    die(f"{label} job timed out after {timeout}s")


def main():
    from app import create_app                       # real Flask app
    app = create_app()                               # also creates base tables
    client = app.test_client()

    # migrations (idempotent, in order)
    mig_dir = os.path.join(ROOT, "backend/migrations")
    for name in sorted(os.listdir(mig_dir)):
        if name.endswith(".sql"):
            with db() as conn, conn.cursor() as cur:
                cur.execute(open(os.path.join(mig_dir, name)).read())
    ok("schema ready")

    # bucket
    import boto3
    s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                      aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
                      region_name=os.getenv("S3_REGION", "auto"))
    try:
        s3.create_bucket(Bucket=os.environ["S3_BUCKET"])
    except Exception:
        pass

    # user + token
    email = f"itest_{int(time.time())}@example.com"
    with db() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (email, password, is_verified) "
                    "VALUES (%s, 'x', 1) RETURNING id", (email,))
        user_id = cur.fetchone()["id"]
    token = jwt.encode({"sub": str(user_id)},
                       app.config["SECRET_KEY"], algorithm="HS256")
    H = {"Authorization": f"Bearer {token}"}
    ok(f"user {user_id}")

    # project
    r = client.post("/projects", json={"title": "integration test"}, headers=H)
    assert r.status_code == 201, r.get_data(as_text=True)
    project_id = r.get_json()["project"]["id"]
    ok(f"project {project_id}")

    # test video
    video_path = os.getenv("TEST_VIDEO", os.path.join(ROOT, "test_video.mp4"))
    if not os.path.exists(video_path):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import make_test_video
        make_test_video.main(video_path)
    nbytes = os.path.getsize(video_path)
    src_duration = _probe_duration(video_path)
    ok(f"test video {nbytes} bytes, {src_duration:.1f}s")

    # presigned upload (full path: presign -> HTTP PUT -> complete)
    r = client.post(f"/projects/{project_id}/uploads",
                    json={"filename": "test_video.mp4", "bytes": nbytes},
                    headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    up = r.get_json()
    with open(video_path, "rb") as f:
        if up["mode"] == "single":
            pr = requests.put(up["url"], data=f.read(),
                              headers={"Content-Type": "video/mp4"})
            assert pr.status_code in (200, 204), pr.text[:300]
            parts = []
        else:
            parts = []
            for part in up["part_urls"]:
                chunk = f.read(up["part_size"])
                pr = requests.put(part["url"], data=chunk)
                assert pr.status_code in (200, 204), pr.text[:300]
                parts.append({"part_number": part["part_number"],
                              "etag": pr.headers.get("ETag", '"x"')})
    r = client.post(f"/projects/{project_id}/uploads/complete",
                    json={"storage_key": up["storage_key"],
                          "upload_id": up.get("upload_id"),
                          "parts": parts, "filename": "test_video.mp4"},
                    headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    index_job = r.get_json()["index_job_id"]
    ok(f"uploaded via presigned {up['mode']}; index job {index_job}")

    wait_job(index_job, TIMEOUT_INDEX, "index")
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT i.json FROM indexes i JOIN assets a
                       ON a.sha256 = i.video_sha256
                       WHERE a.project_id = %s AND a.kind='original'
                       ORDER BY a.id DESC LIMIT 1""", (project_id,))
        idx = cur.fetchone()["json"]
    shots, silences = idx["shots"], idx["silences"]
    words = idx["words"]
    assert len(shots) > 3, f"expected >3 shots, got {len(shots)}"
    assert len(silences) > 3, f"expected >3 silences, got {len(silences)}"
    ok(f"index: {len(shots)} shots, {len(silences)} silences, "
       f"{len(words)} words")

    r = client.get(f"/projects/{project_id}", headers=H)
    pj = r.get_json()
    assert pj["indexed"] is True
    assert any(a["kind"] == "proxy" for a in pj["assets"]), "no proxy asset"
    ok("proxy asset present; project reports indexed")

    # ── transcript quality: hard caps + tail coverage (issue 6) ──────
    sentences = idx["sentences"]
    if words:
        worst = max(s["t1"] - s["t0"] for s in sentences)
        assert worst <= 6.05, f"run-on sentence: {worst:.1f}s > 6s cap"
        assert max(len(s["text"].split()) for s in sentences) <= 12, \
            "sentence over the 12-word cap"
        assert any(w["t0"] >= 50.0 for w in words), \
            "tail words missing — nothing transcribed in the final slate"
        ok(f"transcript: {len(sentences)} sentences, longest {worst:.1f}s, "
           "tail words present")

    def send(text, attachments=None, client_msg_id=None):
        payload = {"text": text}
        if attachments:
            payload["attachments"] = attachments
        if client_msg_id:
            payload["client_msg_id"] = client_msg_id
        r = client.post(f"/projects/{project_id}/messages", json=payload,
                        headers=H)
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()

    def latest_edl():
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM edls WHERE project_id = %s "
                        "ORDER BY version DESC LIMIT 1", (project_id,))
            return cur.fetchone()

    def latest_assistant():
        with db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT cm.* FROM chat_messages cm
                           JOIN projects p ON p.chat_session_id = cm.session_id
                           WHERE p.id = %s AND cm.role='assistant'
                           ORDER BY cm.id DESC LIMIT 1""", (project_id,))
            return cur.fetchone()

    def get_state():
        r = client.get(f"/projects/{project_id}/state?after_id=0", headers=H)
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()

    # ── chat turn 1 + idempotency (issue 1) ──────────────────────────
    cmid = "itest-dedup-001"
    out = send("remove the silences and add captions", client_msg_id=cmid)
    assert out.get("queued"), f"agent turn not queued: {out}"
    dup = send("remove the silences and add captions", client_msg_id=cmid)
    assert dup.get("duplicate") and dup["message_id"] == out["message_id"], \
        f"duplicate POST not idempotent: {dup} vs {out}"
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM video_jobs
                       WHERE project_id = %s AND type='agent_turn'""",
                    (project_id,))
        assert cur.fetchone()["n"] == 1, "duplicate POST enqueued a 2nd turn"
        cur.execute("""SELECT COUNT(*) AS n FROM chat_messages cm
                       JOIN projects p ON p.chat_session_id = cm.session_id
                       WHERE p.id = %s AND cm.role='user'""", (project_id,))
        assert cur.fetchone()["n"] == 1, "duplicate POST stored a 2nd message"
    ok("duplicate POST with same client_msg_id: same message, one agent turn")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")

    edl = latest_edl()
    assert edl["version"] >= 2, "agent did not write a new EDL version"
    kept = sum(e - s for s, e in edl["json"]["keep"])
    assert kept < src_duration - 1, \
        f"expected trimmed output, kept {kept}s of {src_duration}s"
    caps = edl["json"].get("captions")
    assert caps, "captions missing from EDL"
    ok(f"agent wrote EDL v{edl['version']}: kept {kept:.1f}s of "
       f"{src_duration:.1f}s, captions={json.dumps(caps)[:60]}")

    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM video_jobs WHERE project_id = %s AND
                       type='preview' ORDER BY id DESC LIMIT 1""",
                    (project_id,))
        pv = cur.fetchone()
    assert pv and pv["state"] == "done", "agent preview did not complete"
    prev_dur = pv["result"]["duration_s"]
    assert prev_dur < src_duration - 1, \
        f"preview {prev_dur}s not shorter than source {src_duration}s"
    ok(f"preview rendered: {prev_dur:.1f}s (< {src_duration:.1f}s)")
    ok(f"assistant replied: {latest_assistant()['content'][:70]}...")

    state = get_state()
    assert state["latest_preview"], "state endpoint missing latest_preview"
    assert state["edl_versions"] and state["messages"], "state incomplete"
    preview_before = state["latest_preview"]["asset_id"]
    ok(f"state endpoint: preview asset {preview_before}, "
       f"{len(state['edl_versions'])} versions, "
       f"{len(state['messages'])} messages")

    # ── turn 2: no-op honesty + auto-render (issue 3) ────────────────
    v_before = latest_edl()["version"]
    out = send("NOOP TEST then tighten the opening slightly")
    assert out.get("queued")
    turn = wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM chat_messages cm
                       JOIN projects p ON p.chat_session_id = cm.session_id
                       WHERE p.id = %s AND cm.role='activity'
                         AND cm.content LIKE %s""",
                    (project_id, "%NO CHANGE%"))
        assert cur.fetchone()["n"] >= 1, "no-op write did not report NO CHANGE"
    v_after = latest_edl()["version"]
    assert v_after == v_before + 1, \
        f"no-op created a version: v{v_before} -> v{v_after} (expected +1)"
    assert (turn["result"] or {}).get("auto_render") is True, \
        f"loop did not auto-render: {turn['result']}"
    reply = latest_assistant()
    assert (reply["meta"] or {}).get("preview"), \
        "auto-rendered preview not attached to the reply"
    assert (reply["meta"] or {}).get("preview", {}).get("edl_version") == \
        v_after, "attached preview is not for the new version"
    ok(f"NO CHANGE surfaced; identical write made no version; "
       f"auto-render attached preview for v{v_after}")

    # ── turn 3: styled captions land in EDL + UI state (issue 2/5) ───
    out = send("make the captions 3 words max, red, at the top")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    edl = latest_edl()
    caps = edl["json"]["captions"]
    assert caps.get("max_words_per_caption") == 3, caps
    assert (caps.get("style") or {}).get("color") == "#FF0000", caps
    assert (caps.get("style") or {}).get("position") == "top", caps
    state = get_state()
    assert state["latest_preview"]["asset_id"] != preview_before, \
        "new preview did not surface via the state endpoint"
    assert state["latest_edl"]["version"] == edl["version"]
    ok(f"styled captions in EDL v{edl['version']} "
       f"(<=3 words, #FF0000, top); state shows new preview "
       f"{state['latest_preview']['asset_id']} without refresh")

    # ── zero-write false claims: regeneration path ───────────────────
    v_before = latest_edl()["version"]
    out = send("ZWC TEST cut at the silence and make the font colour red")
    assert out.get("queued")
    turn = wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    hon = (turn["result"] or {}).get("honesty") or {}
    assert hon.get("false_claims") == 1 and not hon.get("corrective_note"), hon
    assert latest_edl()["version"] == v_before, "zero-write turn made a version"
    reply = latest_assistant()["content"]
    assert "nothing was changed" in reply.lower(), reply
    assert "#FF0000" not in reply and "rendered" not in reply.lower(), reply
    ok("zero-write false claims caught; one regeneration produced an "
       "honest reply")

    # ── zero-write false claims: stubborn regen -> drafts DISCARDED ──
    # (round 4 hardened this: the old corrective-note path published the
    # fabrication behind a prefix; now the user only sees the fallback)
    out = send("STUBBORN TEST same again")
    assert out.get("queued")
    turn = wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    hon = (turn["result"] or {}).get("honesty") or {}
    assert hon.get("false_claims") == 2 and hon.get("corrective_note"), hon
    assert hon.get("fallback_reply") is True, hon
    assert hon.get("discarded_drafts"), hon
    reply = latest_assistant()["content"]
    assert reply.startswith("I wasn't able to make that change"), reply[:80]
    assert "Captions are now red" not in reply and "Cuts applied" not in reply
    assert latest_edl()["version"] == v_before
    ok("stubborn false claims: both drafts discarded, system fallback "
       "posted (false_claims=2, fallback_reply=true)")

    # ── mid-word protection: warning, dirty audit, snap fixes it ─────
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT i.json FROM indexes i JOIN assets a
                       ON a.sha256 = i.video_sha256
                       WHERE a.project_id = %s AND a.kind='original'
                       ORDER BY a.id DESC LIMIT 1""", (project_id,))
        target_word = next(w for w in cur.fetchone()["json"]["words"]
                           if w["t0"] >= 10.0)
    v_before = latest_edl()["version"]
    out = send("WORDCUT TEST keep only the start")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    edl = latest_edl()
    assert abs(edl["json"]["keep"][0][1] - target_word["t1"]) < 0.03, \
        f"snap did not land on word end: {edl['json']['keep']} vs {target_word}"
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM chat_messages cm
                       JOIN projects p ON p.chat_session_id = cm.session_id
                       WHERE p.id = %s AND cm.role='activity'
                         AND cm.content LIKE %s""",
                    (project_id, "%lands inside the word%"))
        assert cur.fetchone()["n"] >= 1, "mid-word WARNING never surfaced"
        cur.execute("""SELECT result FROM video_jobs
                       WHERE project_id = %s AND type='preview'
                         AND (result->>'edl_version')::int = %s""",
                    (project_id, v_before + 1))
        dirty = cur.fetchone()["result"]
        assert dirty.get("midword_audit"), \
            f"render audit missed the mid-word boundary: {dirty}"
        cur.execute("""SELECT result FROM video_jobs
                       WHERE project_id = %s AND type='preview'
                         AND (result->>'edl_version')::int = %s""",
                    (project_id, v_before + 2))
        clean = cur.fetchone()["result"]
        assert clean.get("midword_audit") == [], clean.get("midword_audit")
    ok(f"mid-word boundary warned ('{target_word['w']}'), render audit "
       "flagged it, snap_to_words landed on the word end, clean audit after")

    # ── cut_range / restore_range + regression warning ───────────────
    v_before = latest_edl()["version"]
    out = send("RANGE TEST local fix roundtrip")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    assert latest_edl()["version"] == v_before + 3, \
        "expected cut + restore + keep to make exactly 3 versions"
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM chat_messages cm
                       JOIN projects p ON p.chat_session_id = cm.session_id
                       WHERE p.id = %s AND cm.role='activity'
                         AND cm.content LIKE '%%re-includes%%'
                         AND cm.content LIKE '%%silence%%'""", (project_id,))
        assert cur.fetchone()["n"] >= 1, "regression warning never surfaced"
        cur.execute("""SELECT COUNT(*) AS n FROM chat_messages cm
                       JOIN projects p ON p.chat_session_id = cm.session_id
                       WHERE p.id = %s AND cm.role='activity'
                         AND cm.content LIKE '%%restored 2.0%%'""",
                    (project_id,))
        assert cur.fetchone()["n"] >= 1, "restore_range diff missing"
    ok("cut_range/restore_range versioned with diffs; keep_segments "
       "regression warning flagged the re-included silence")

    # ── set_caption_style merges without touching anything else ──────
    keep_before = latest_edl()["json"]["keep"]
    out = send("STYLE TEST make it golden")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    edl = latest_edl()
    caps = edl["json"]["captions"]
    assert (caps.get("style") or {}).get("color") == "#FFD700", caps
    assert caps.get("max_words_per_caption") == 3, \
        f"style merge reset max_words: {caps}"
    assert caps.get("mode") == "from_transcript"
    assert edl["json"]["keep"] == keep_before, "style change touched the cut"
    ok("set_caption_style merged color only — chunking and keep untouched")

    def download_asset(asset_id, dest):
        r = client.get(f"/assets/{asset_id}/url", headers=H)
        assert r.status_code == 200, r.get_data(as_text=True)
        url = r.get_json()["url"]
        assert url.startswith(os.environ["S3_ENDPOINT"]), \
            f"asset URL not direct-from-storage: {url[:80]}"
        blob = requests.get(url)
        assert blob.status_code == 200
        with open(dest, "wb") as f:
            f.write(blob.content)
        return dest

    def probe_dims(path):
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True)
        w, h = out.stdout.strip().split(",")[:2]
        return int(w), int(h)

    def last_preview_result():
        with db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT result FROM video_jobs
                           WHERE project_id = %s AND type='preview'
                             AND state='done'
                           ORDER BY id DESC LIMIT 1""", (project_id,))
            return cur.fetchone()["result"]

    def all_chat_text():
        with db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT string_agg(cm.content, '\n') AS t
                           FROM chat_messages cm
                           JOIN projects p ON p.chat_session_id = cm.session_id
                           WHERE p.id = %s AND cm.role IN
                                 ('assistant','user')""", (project_id,))
            return cur.fetchone()["t"] or ""

    def user_edl_op(op, args, expect=200):
        r = client.post(f"/projects/{project_id}/edl",
                        json={"op": op, "args": args}, headers=H)
        assert r.status_code == expect, r.get_data(as_text=True)
        return r.get_json()

    # ── ISSUE 2: zero-write 9:16 fabrication — regen also fabricates,
    #    the user must ONLY see the system-authored fallback ─────────────
    v_before = latest_edl()["version"]
    out = send("RATIO916 TEST make the video 9:16")
    assert out.get("queued")
    turn = wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    hon = (turn["result"] or {}).get("honesty") or {}
    assert hon.get("false_claims") == 2 and hon.get("corrective_note"), hon
    assert hon.get("fallback_reply") is True, hon
    assert any("cropped to 9:16" in d for d in hon.get("discarded_drafts")
               or []), f"discarded drafts not stored: {hon}"
    assert latest_edl()["version"] == v_before, "fabrication turn wrote EDL"
    reply = latest_assistant()["content"]
    assert reply.startswith("I wasn't able to make that change"), reply[:90]
    assert "What I CAN do" in reply, f"no alternative hint: {reply}"
    chat = all_chat_text()
    assert "cropped to 9:16" not in chat and "Preview attached" not in chat, \
        "fabricated sentence leaked into user-visible chat"
    ok("9:16 fabrication: both drafts discarded, system fallback + honest "
       "alternative shown; drafts stored for admin")

    # ── ISSUE 1: set_frame as an agent tool ─────────────────────────────
    out = send("FRAME TEST make it vertical for tiktok")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    edl = latest_edl()
    assert (edl["json"].get("frame") or {}).get("ratio") == "9:16", edl["json"]
    pv = last_preview_result()
    assert pv["edl_version"] == edl["version"]
    prev_path = os.path.join(ROOT, "itest_preview_916.mp4")
    download_asset(pv["render_asset_id"], prev_path)
    w, h = probe_dims(prev_path)
    assert h > w and h == 480, f"9:16 preview not vertical: {w}x{h}"
    ok(f"set_frame 9:16: preview is vertical {w}x{h}, direct storage URL")

    # keyframe density for accurate scrubbing (Safari)
    import subprocess as _sp
    kf = _sp.run(["ffprobe", "-v", "error", "-skip_frame", "nokey",
                  "-select_streams", "v:0", "-show_entries", "frame=pts_time",
                  "-of", "csv=p=0", prev_path], capture_output=True, text=True)
    kts = [float(x.split(",")[0]) for x in kf.stdout.split() if x.strip(",")]
    gaps = [b - a for a, b in zip(kts, kts[1:])]
    assert kts and (not gaps or max(gaps) <= 2.2), \
        f"keyframes too sparse for scrubbing: max gap {max(gaps):.1f}s"
    ok(f"preview keyframes dense: {len(kts)} keyframes, "
       f"max gap {max(gaps) if gaps else 0:.2f}s")

    # ── ISSUE 3: middle captions ─────────────────────────────────────────
    out = send("MIDPOS TEST captions in the middle")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    caps = latest_edl()["json"]["captions"]
    assert (caps.get("style") or {}).get("position") == "middle", caps
    assert (caps.get("style") or {}).get("color") == "#FFD700", \
        f"middle merge reset color: {caps}"
    dur_before_insert = last_preview_result()["duration_s"]
    ok(f"caption position middle merged (color kept); baseline program "
       f"{dur_before_insert:.1f}s")

    # ── ISSUE 4: image insert via the agent ─────────────────────────────
    img_path = os.path.join(ROOT, "test_image.png")
    if not os.path.exists(img_path):
        _sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                 "color=c=orange:size=640x360:duration=0.1", "-frames:v", "1",
                 img_path], check=True, capture_output=True)
    ibytes = os.path.getsize(img_path)
    r = client.post(f"/projects/{project_id}/uploads",
                    json={"filename": "test_image.png", "bytes": ibytes,
                          "kind": "image"}, headers=H)
    up = r.get_json()
    with open(img_path, "rb") as f:
        requests.put(up["url"], data=f.read(),
                     headers={"Content-Type": up["content_type"]})
    r = client.post(f"/projects/{project_id}/uploads/complete",
                    json={"storage_key": up["storage_key"], "kind": "image",
                          "filename": "test_image.png"}, headers=H)
    assert r.status_code == 200

    out = send("IMGINSERT TEST splice the image in at the start")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    edl = latest_edl()
    ins = edl["json"].get("inserts") or []
    assert len(ins) == 1 and ins[0]["kind"] == "image" and \
        ins[0]["at_output_s"] == 0 and ins[0]["duration_s"] == 3.0, ins
    pv = last_preview_result()
    assert abs(pv["duration_s"] - (dur_before_insert + 3.0)) < 0.25, \
        f"program did not grow by 3s: {dur_before_insert} -> {pv['duration_s']}"
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM chat_messages cm
                       JOIN projects p ON p.chat_session_id = cm.session_id
                       WHERE p.id = %s AND cm.role='activity'
                         AND cm.content LIKE '%%not transcribed%%'""",
                    (project_id,))
        assert cur.fetchone()["n"] >= 1, "insert caption note missing"
    ok(f"image insert at 0s: program {pv['duration_s']:.1f}s "
       f"(+3.0s), captions-scope note surfaced")

    # ── ISSUE 4: video clip insert + remove via the UI endpoint ─────────
    clip_path = os.path.join(ROOT, "test_clip.mp4")
    if not os.path.exists(clip_path):
        _sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                 "testsrc=size=320x240:rate=15:duration=2",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                 "-shortest", clip_path], check=True, capture_output=True)
    cbytes = os.path.getsize(clip_path)
    r = client.post(f"/projects/{project_id}/uploads",
                    json={"filename": "test_clip.mp4", "bytes": cbytes,
                          "kind": "clip"}, headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    up = r.get_json()
    assert up["storage_key"].startswith(f"clips/{project_id}/")
    with open(clip_path, "rb") as f:
        requests.put(up["url"], data=f.read(),
                     headers={"Content-Type": up["content_type"]})
    r = client.post(f"/projects/{project_id}/uploads/complete",
                    json={"storage_key": up["storage_key"], "kind": "clip",
                          "filename": "test_clip.mp4", "duration_s": 2.0},
                    headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    clip_asset = r.get_json()["asset_id"]
    state = get_state()
    assert any(a["id"] == clip_asset and a["kind"] == "video_clip"
               for a in state.get("media_assets") or []), \
        "clip not in state media_assets"

    dur_before_clip = last_preview_result()["duration_s"]
    res = user_edl_op("insert_media",
                      {"asset_id": clip_asset, "at_output_s": 99999})
    assert res.get("version") and res.get("preview_job_id"), res
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT created_by FROM edls WHERE project_id=%s
                       AND version=%s""", (project_id, res["version"]))
        assert cur.fetchone()["created_by"] == "user"
    wait_job(res["preview_job_id"], TIMEOUT_RENDER, "preview-clip")
    pv = last_preview_result()
    assert abs(pv["duration_s"] - (dur_before_clip + 2.0)) < 0.25, \
        f"clip insert wrong growth: {dur_before_clip} -> {pv['duration_s']}"
    clip_prev = os.path.join(ROOT, "itest_preview_clip.mp4")
    download_asset(pv["render_asset_id"], clip_prev)
    w, h = probe_dims(clip_prev)
    assert h > w and h == 480, \
        f"240p/15fps clip not normalized into 9:16 frame: {w}x{h}"
    clip_ins_id = next(i["id"] for i in latest_edl()["json"]["inserts"]
                       if i["asset_key"] == up["storage_key"])
    res = user_edl_op("remove_insert", {"id": clip_ins_id})
    wait_job(res["preview_job_id"], TIMEOUT_RENDER, "preview-clip-removed")
    pv = last_preview_result()
    assert abs(pv["duration_s"] - dur_before_clip) < 0.06, \
        f"remove_insert did not restore timing: {dur_before_clip} vs " \
        f"{pv['duration_s']}"
    ok(f"user-endpoint clip insert (mixed res/fps, normalized) then "
       f"remove_insert restored {pv['duration_s']:.2f}s exactly")

    # ── turn 4: music attachment -> add_music with duck (issue 8) ────
    music_path = os.path.join(ROOT, "test_music.wav")
    if not os.path.exists(music_path):
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=18", "-ac", "2",
                        music_path], check=True, capture_output=True)
    mbytes = os.path.getsize(music_path)
    r = client.post(f"/projects/{project_id}/uploads",
                    json={"filename": "test_music.wav", "bytes": mbytes,
                          "kind": "music"}, headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    up = r.get_json()
    assert up["storage_key"].startswith(f"music/{project_id}/")
    with open(music_path, "rb") as f:
        pr = requests.put(up["url"], data=f.read(),
                          headers={"Content-Type": up["content_type"]})
        assert pr.status_code in (200, 204)
    r = client.post(f"/projects/{project_id}/uploads/complete",
                    json={"storage_key": up["storage_key"], "kind": "music",
                          "filename": "test_music.wav", "duration_s": 18.0},
                    headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    music_asset = r.get_json()["asset_id"]
    state = get_state()
    assert any(a["id"] == music_asset for a in state["music_assets"]), \
        "uploaded music not listed in state"
    ok(f"music uploaded as asset {music_asset} (18.0s), visible in state")

    out = send("add this music quietly under the speech",
               attachments=[music_asset])
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    edl = latest_edl()
    music_items = edl["json"].get("music") or []
    assert music_items, "music missing from EDL"
    assert music_items[0]["duck"] is True
    assert music_items[0]["storage_key"] == up["storage_key"]
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT cm.meta FROM chat_messages cm
                       JOIN projects p ON p.chat_session_id = cm.session_id
                       WHERE p.id = %s AND cm.role='user'
                       ORDER BY cm.id DESC LIMIT 1""", (project_id,))
        umeta = cur.fetchone()["meta"] or {}
    assert umeta.get("attachments") == [music_asset], umeta
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM video_jobs WHERE project_id = %s AND
                       type='preview' ORDER BY id DESC LIMIT 1""",
                    (project_id,))
        pv = cur.fetchone()
    assert pv["state"] == "done" and \
        pv["result"]["edl_version"] == edl["version"]
    ok(f"music in EDL v{edl['version']} (duck=true), preview rendered")

    # ── ISSUE 4: voiceover over the whole program (agent tool) ──────────
    out = send("VOICEOVER TEST lay my narration over everything")
    assert out.get("queued")
    wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    edl = latest_edl()
    vos = edl["json"].get("voiceover") or []
    assert vos and vos[0]["duck_others"] is True and \
        vos[0]["start_output_s"] == 0, vos
    pv = last_preview_result()
    assert pv["edl_version"] == edl["version"], "voiceover preview missing"
    ok(f"voiceover in EDL v{edl['version']} (duck_others=true), "
       "preview rendered with the mix")

    # ── ISSUE 1: UI frame toggle writes a user version + auto-previews ──
    res = user_edl_op("set_frame", {"ratio": "1:1", "mode": "pad"})
    assert res.get("version") and res.get("preview_job_id"), res
    wait_job(res["preview_job_id"], TIMEOUT_RENDER, "preview-1x1")
    sq_prev = os.path.join(ROOT, "itest_preview_1x1.mp4")
    download_asset(last_preview_result()["render_asset_id"], sq_prev)
    w, h = probe_dims(sq_prev)
    assert w == h, f"1:1 pad preview not square: {w}x{h}"
    # back to source frame so the final-render resolution assertion below
    # keeps guarding "finals render at source resolution"
    res = user_edl_op("set_frame", {"ratio": "source"})
    wait_job(res["preview_job_id"], TIMEOUT_RENDER, "preview-src-frame")
    ok(f"UI frame toggle: user EDL versions + auto previews "
       f"(1:1 pad {w}x{h}, then back to source)")

    # ── stale index self-heals on project open (pipeline version) ────
    with db() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE indexes SET pipeline_version = 1
                       WHERE video_sha256 = (SELECT sha256 FROM assets
                           WHERE project_id = %s AND kind='original'
                           ORDER BY id DESC LIMIT 1)""", (project_id,))
    state = get_state()          # "project open" — must trigger the refresh
    assert state["indexed"] is True, "old index should keep serving"
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT id FROM video_jobs WHERE project_id = %s
                       AND type='index' ORDER BY id DESC LIMIT 1""",
                    (project_id,))
        reindex_job = cur.fetchone()["id"]
    row = wait_job(reindex_job, TIMEOUT_INDEX, "reindex")
    assert (row["result"] or {}).get("cached") is not True, \
        "stale index served from cache instead of rebuilding"
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT pipeline_version, json FROM indexes
                       WHERE video_sha256 = (SELECT sha256 FROM assets
                           WHERE project_id = %s AND kind='original'
                           ORDER BY id DESC LIMIT 1)""", (project_id,))
        idx2 = cur.fetchone()
    assert idx2["pipeline_version"] == 2, idx2["pipeline_version"]
    assert all(s["t1"] - s["t0"] <= 6.05 for s in idx2["json"]["sentences"])
    ok("stale index (pipeline v1) re-indexed on open; fresh index is v2 "
       "with capped sentences")

    # ── final render — only through the explicit confirm endpoint ────
    edl = latest_edl()
    r = client.post(f"/projects/{project_id}/render/final",
                    json={"edl_version": edl["version"]}, headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    wait_job(r.get_json()["job_id"], TIMEOUT_RENDER, "final")
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets WHERE project_id = %s
                       AND kind='render' AND meta->>'variant'='final'
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        fin = cur.fetchone()
    assert fin, "final render asset missing"
    assert fin["height"] == 720 and fin["width"] == 1280, \
        f"final not at source resolution: {fin['width']}x{fin['height']}"
    r = client.get(f"/assets/{fin['id']}/url", headers=H)
    assert r.status_code == 200 and r.get_json()["url"]
    ok(f"final render {fin['width']}x{fin['height']}, "
       f"{fin['duration_s']:.1f}s, presigned GET works")

    # faststart: moov atom must lead so duration is known immediately
    fin_path = os.path.join(ROOT, "itest_final.mp4")
    download_asset(fin["id"], fin_path)
    head = open(fin_path, "rb").read(64 * 1024)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    assert moov != -1 and (mdat == -1 or moov < mdat), \
        f"final not faststart (moov={moov}, mdat={mdat})"
    ok("final has +faststart (moov before mdat); download URL is a direct "
       "storage presigned GET")

    # render cache: re-requesting the same version must serve the cached
    # asset (no second encode)
    r = client.post(f"/projects/{project_id}/render/final",
                    json={"edl_version": edl["version"]}, headers=H)
    assert r.status_code == 200
    row = wait_job(r.get_json()["job_id"], TIMEOUT_RENDER, "final-cached")
    assert (row["result"] or {}).get("cached") is True, \
        f"expected cached render, got {row['result']}"
    assert row["result"]["render_asset_id"] == fin["id"]
    ok("re-render of same version served from cache")

    # ── ISSUE 7: admin observability ─────────────────────────────────────
    admin_token = jwt.encode({"sub": str(user_id),
                              "email": "thevalmera@gmail.com"},
                             app.config["SECRET_KEY"], algorithm="HS256")
    AH = {"Authorization": f"Bearer {admin_token}"}

    r = client.get("/admin/video/overview", headers=H)
    assert r.status_code == 403, "non-admin was not blocked"
    r = client.get("/admin/video/overview", headers=AH)
    assert r.status_code == 200, r.get_data(as_text=True)
    ov = r.get_json()
    row = next((u for u in ov["users"] if u["id"] == user_id), None)
    assert row and row["projects"] >= 1 and row["messages"] >= 10, row
    assert row["storage_bytes"] > 0
    assert ov["ops"]["turns_total"] >= 10
    assert ov["ops"]["false_claims"] >= 5, ov["ops"]     # zwc 1 + stubborn 2 + ratio 2
    assert ov["ops"]["fallback_replies"] >= 1, ov["ops"]
    assert ov["ops"]["auto_renders"] >= 1
    assert ov["ops"]["no_change_count"] >= 1
    assert ov["ops"]["stage_medians"].get("agent_turn", {}).get("total_s") \
        is not None
    ok(f"admin overview: user rollup + ops counters "
       f"(false_claims={ov['ops']['false_claims']}, "
       f"fallback_replies={ov['ops']['fallback_replies']}, "
       f"auto_renders={ov['ops']['auto_renders']})")

    r = client.get("/admin/video/projects?search=", headers=AH)
    assert any(p["id"] == project_id for p in r.get_json()["projects"])
    r = client.get(f"/admin/video/projects/{project_id}", headers=AH)
    assert r.status_code == 200
    det = r.get_json()
    assert len(det["messages"]) > 20 and \
        any(m["role"] == "activity" and (m["meta"] or {}).get("tool")
            for m in det["messages"]), "activity steps missing meta"
    assert len(det["edls"]) >= 10 and det["edls"][0]["json"].get("keep")
    assert any(j["type"] == "agent_turn" and
               (j["result"] or {}).get("timings") for j in det["jobs"])
    sheets_ = [a for a in det["assets"] if a["kind"] == "sheet"]
    assert sheets_ and all(a.get("url") for a in sheets_), \
        "contact sheets not viewable"
    fabricated = [j for j in det["jobs"]
                  if (j.get("result") or {}).get("honesty", {})
                  .get("discarded_drafts")]
    assert fabricated, "discarded honesty drafts not visible in admin jobs"
    assert det["llm_call_count"] > 10
    r = client.get(f"/admin/video/projects/{project_id}/llm_calls",
                   headers=AH)
    calls = r.get_json()["calls"]
    assert calls and any(c["purpose"] == "agent" and
                         (c["request"] or {}).get("messages")
                         for c in calls), "agent llm_calls missing context"
    assert any(c["purpose"] == "honesty_regen" for c in calls) or \
        any(c["purpose"] == "honesty_regen"
            for c in _all_llm_calls(project_id)), "regen call not recorded"
    r = client.get(f"/admin/video/projects/{project_id}/index", headers=AH)
    assert r.status_code == 200 and r.get_json()["index"].get("words")
    r = client.get("/admin/video/costs", headers=AH)
    assert r.status_code == 200 and "daily" in r.get_json()
    ok(f"admin inspector: chat+activity, {len(det['edls'])} EDL versions, "
       f"jobs w/ timings, assets incl contact sheets, index, "
       f"{det['llm_call_count']} llm_calls (incl honesty regen), costs")

    print(f"\njob wall times: " + json.dumps(
        {k: [round(x, 1) for x in v] for k, v in JOB_TIMES.items()}))
    print("\nALL INTEGRATION CHECKS PASSED")


def _all_llm_calls(project_id):
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT purpose FROM llm_calls WHERE project_id = %s""",
                    (project_id,))
        return cur.fetchall()


def _probe_duration(path):
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(out.stdout.strip())


if __name__ == "__main__":
    main()
