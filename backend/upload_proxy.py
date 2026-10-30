"""A server-built playable copy of an upload the browser cannot play.

THE GAP (B_RELIABILITY.md, 29 Oct 2026). A free account's Studio upload meets
the subscribe wall (or, with the free trial on, an account the trial does not
admit). Such an upload is never analysed before the account pays, so it gets
no server proxy, and the browser's own WebCodecs proxy fails on exactly the
files that need one: HEVC and 4K .mov recordings, MediaError code 4. Eight
free users in 14 days watched "This browser couldn't play the preview" on
their own video and left.

THE FIX. When the Studio reports that the browser cannot decode the active
original (its existing ``player_error`` beacon, media_error 4) and nothing
else will produce a proxy for it (no live or finished analysis of that
upload), the backend queues ONE proxy-only job: an ``index`` job whose
payload carries ``proxy_only: true``. The executor (worker/indexer.py)
then only transcodes the 540p H.264 proxy the player reads, the same file
the full analysis would make at the same key; it writes no index, starts no
First cut, posts nothing in chat and spends no model.

FIXED COST, NEVER CLOUDFLARE. The job is pre-stamped
``execution_provider: "box"``: it runs on the owner's flat-price render box
(box/admission.py, class "trial", behind customers) and, like every box job,
waits in the durable queue when the box is busy. It is queued only while the
box routing row is enabled, so no proxy-only job ever reaches a billable
provider.

AUDIO ONLY. An original with sound and no picture never gets a proxy: the
browser plays it as it is. The proxy-only job that first probes such a
source ends done with ``skipped: "audio_only"`` (no failure filed) and marks
the asset ``meta.no_video``; ``decide`` then answers "audio_only".

BOUNDS. One proxy per upload (a failed one is retried once), at most
``max_live`` proxy-only jobs queued or running across all accounts, sources
up to ``max_bytes``. Kill switch: app_kv ``upload_proxy`` = {"on": false}.

Every index-job query that means "the analysis of this video" excludes these
jobs with ``NOT_PROXY_ONLY`` (a proxy job is not an analysis: the upload-wall
heal after checkout, the Studio's progress bar and the concierge's "still
analysing" must not see it).
"""
import json
import os

KEY = "upload_proxy"
DEFAULTS = {
    "on": True,
    "max_live": int(os.getenv("UPLOAD_PROXY_MAX_LIVE", "4")),
    "max_bytes": int(float(os.getenv("UPLOAD_PROXY_MAX_GB", "10")) * 1024 ** 3),
    "attempts_per_asset": 2,
}
# MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED: the bytes arrived, the browser
# could not decode them (B: "code 4; bytes fetch fine, 206").
UNDECODABLE = 4

# SQL: "this index job is an analysis" (not a proxy-only job). Use bare, or
# through ``not_proxy_only(alias)`` for a joined or aliased video_jobs.
NOT_PROXY_ONLY = "COALESCE(payload->>'proxy_only', '') <> 'true'"


def not_proxy_only(alias=""):
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}payload->>'proxy_only', '') <> 'true'"


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def _kv(cur, key):
    cur.execute("SELECT value FROM app_kv WHERE key = %s", (key,))
    raw = _v(cur.fetchone(), "value", 0)
    if raw is None:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def settings(cur):
    value = dict(DEFAULTS)
    stored = _kv(cur, KEY)
    if stored:
        value.update({k: stored[k] for k in stored if k in DEFAULTS})
    return value


def box_live(cur):
    """The render box is routed (app_kv box_routing enabled with a stage):
    the one place a proxy-only job may run. Absent or off: never queued."""
    routing = _kv(cur, "box_routing") or {}
    return bool(routing.get("enabled")) and any(
        bool(v) for v in (routing.get("stages") or {}).values())


def decide(cur, user_id, project_id, asset_id, detail, gated):
    """Why a proxy-only job may NOT be queued for this beacon, or None.

    ``gated``: the account meets the subscribe wall on this project (the
    caller's _subscribe_gate_applies and not a live trial admission). Only
    those uploads lack an analysis; a subscriber's upload always has one."""
    try:
        code = int((detail or {}).get("media_error"))
    except (TypeError, ValueError):
        code = None
    if code != UNDECODABLE:
        return "not_undecodable"
    # The beacon's is_proxy means "playing a source"; whether that source is
    # the original is decided by the asset id below, not by that flag.
    if not gated:
        return "not_gated"
    cfg = settings(cur)
    if not cfg.get("on"):
        return "switched_off"
    if not box_live(cur):
        return "box_not_routed"
    cur.execute("""SELECT id, kind, bytes, sha256, meta FROM assets
                   WHERE project_id = %s AND kind = 'original'
                   ORDER BY id DESC LIMIT 1""", (int(project_id),))
    original = cur.fetchone()
    if not original or int(_v(original, "id", 0)) != int(asset_id or 0):
        return "not_active_original"
    meta = _v(original, "meta", 4) or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) or {}
        except (TypeError, ValueError):
            meta = {}
    if (meta.get("upload_state") == "pending" or meta.get("r2_purged_at")
            or meta.get("link_src")):
        return "source_not_ready"
    if meta.get("no_video") is True:
        # Sound but no picture (an audio-only .mp4 uploaded as the video,
        # SWEEP_UPLOAD #43): the browser plays it as it is and a proxy of no
        # picture is not a thing. The worker's proxy-only job records this
        # the first time it probes such a source (and ends done, quietly).
        return "audio_only"
    nbytes = int(_v(original, "bytes", 2) or 0)
    if nbytes <= 0 or nbytes > int(cfg.get("max_bytes") or 0):
        return "size"
    sha = _v(original, "sha256", 3)
    if sha:
        cur.execute("""SELECT 1 FROM assets WHERE project_id = %s
                         AND kind = 'proxy' AND sha256 = %s LIMIT 1""",
                    (int(project_id), sha))
        if cur.fetchone():
            return "proxy_exists"
    cur.execute(f"""SELECT
          COUNT(*) FILTER (WHERE {NOT_PROXY_ONLY}
                             AND state IN ('queued', 'running', 'done'))
              AS analyses,
          COUNT(*) FILTER (WHERE NOT ({NOT_PROXY_ONLY})) AS proxies,
          COUNT(*) FILTER (WHERE NOT ({NOT_PROXY_ONLY})
                             AND state IN ('queued', 'running', 'done'))
              AS proxies_live
        FROM video_jobs
        WHERE project_id = %s AND type = 'index'
          AND payload->>'asset_id' = %s""",
                (int(project_id), str(int(asset_id))))
    row = cur.fetchone() or {}
    if int(_v(row, "analyses", 0) or 0):
        return "analysis_makes_proxy"
    if int(_v(row, "proxies_live", 2) or 0):
        return "already_queued"
    if int(_v(row, "proxies", 1) or 0) >= int(cfg.get("attempts_per_asset") or 1):
        return "attempts_used"
    cur.execute(f"""SELECT COUNT(*) AS n FROM video_jobs
                    WHERE type = 'index' AND NOT ({NOT_PROXY_ONLY})
                      AND state IN ('queued', 'running')""")
    if int(_v(cur.fetchone(), "n", 0) or 0) >= int(cfg.get("max_live") or 0):
        return "busy"
    return None


def payload(asset_id):
    """The proxy-only job: an index job the executor ends after the proxy,
    stamped for the box (the stamp is immutable; nothing re-routes it)."""
    return {"asset_id": int(asset_id), "proxy_only": True,
            "execution_provider": "box", "source": "player_error"}


def maybe_enqueue(cur, user_id, project_id, asset_id, detail, gated, enqueue):
    """Queue the proxy-only job when ``decide`` allows it. Called under the
    project row lock. Returns (job_id or None, reason or None)."""
    reason = decide(cur, user_id, project_id, asset_id, detail, gated)
    if reason:
        return None, reason
    job_id = enqueue(cur, int(project_id), int(user_id), "index",
                     payload(asset_id))
    return job_id, None
