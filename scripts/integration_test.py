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

    # ── zero-write false claims: corrective-note path ────────────────
    out = send("STUBBORN TEST same again")
    assert out.get("queued")
    turn = wait_job(out["job_id"], TIMEOUT_AGENT, "agent_turn")
    hon = (turn["result"] or {}).get("honesty") or {}
    assert hon.get("false_claims") == 2 and hon.get("corrective_note"), hon
    reply = latest_assistant()["content"]
    assert reply.startswith("*(system: no changes were made this turn)*"), \
        reply[:80]
    assert latest_edl()["version"] == v_before
    ok("stubborn false claims got the automatic corrective note "
       "(counters: false_claims=2, corrective_note=true)")

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

    print(f"\njob wall times: " + json.dumps(
        {k: [round(x, 1) for x in v] for k, v in JOB_TIMES.items()}))
    print("\nALL INTEGRATION CHECKS PASSED")


def _probe_duration(path):
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(out.stdout.strip())


if __name__ == "__main__":
    main()
