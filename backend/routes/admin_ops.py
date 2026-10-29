"""The outcome loop's views and its two storage helpers.

Admin (admin_required, read-only connections):
  GET /admin/video/ops/summary    the Overview strip: signups, outcome rate,
                                  tickets open / fixed this week
  GET /admin/video/ops/today      the top of the admin: today's results,
                                  failures and auto-fixes, signups and active
                                  customers, queue / box / spend
  GET /admin/video/ops/sessions   reviewed sessions with evidence pictures
  GET /admin/video/ops/tickets    tickets grouped by root cause

For backend/scripts/ops_review.py (the Reviewer/Fixer CLI on the owner's
Mac, which holds the database URL but no storage credentials), authorised by
a short-lived key whose SHA-256 the CLI stored in ops_review_keys:
  POST /ops/review/presign        download URLs for assets and image keys
  POST /ops/review/evidence       store ONE evidence picture under
                                  ops/evidence/<session>/<name>
  GET  /ops/review/whoami         is this key valid

Nothing here writes to the database. The evidence route writes one image
object to R2 and nothing else.
"""
import hashlib
import re
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Blueprint, current_app, jsonify, request

import storage
from ops_loop import EVIDENCE_PREFIX, VERDICTS, views
from routes.admin import (admin_required, gzip_large_json,
                          legacy_error_response)

admin_ops_bp = Blueprint("admin_ops", __name__)
admin_ops_bp.register_error_handler(Exception, legacy_error_response)
admin_ops_bp.after_request(gzip_large_json)

EVIDENCE_MAX_BYTES = 6 * 1024 * 1024
SLUG_RE = re.compile(r"^[a-z0-9_-]{3,80}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.(png|jpg|jpeg|webp)$")
PRESIGN_PREFIXES = ("media/", "renders/", "sheets/", "thumbs/", "filmstrip/",
                    "originals/", "proxies/", "clips/", "images/", "audio/",
                    EVIDENCE_PREFIX)
PRESIGN_MAX_ITEMS = 300
PRESIGN_MAX_S = 6 * 3600
MAGIC = (("png", b"\x89PNG\r\n\x1a\n"), ("jpg", b"\xff\xd8\xff"))
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg",
                 "jpeg": "image/jpeg", "webp": "image/webp"}


def _connect():
    conn = psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor, connect_timeout=5,
                            options="-c statement_timeout=15000 "
                                    "-c lock_timeout=2000 "
                                    "-c default_transaction_read_only=on")
    conn.autocommit = True
    return conn


def _admin_presign(key):
    if not key or not storage.is_configured():
        return None
    try:
        return storage.presign_get(key, expires=storage.PRESIGN_EXPIRY)
    except Exception:
        return None


# ── Admin views ──────────────────────────────────────────────────────────

@admin_ops_bp.route("/admin/video/ops/summary", methods=["GET"])
@admin_required
def ops_summary():
    conn = _connect()
    try:
        return jsonify(views.summary(conn.cursor()))
    finally:
        conn.close()


@admin_ops_bp.route("/admin/video/ops/today", methods=["GET"])
@admin_required
def ops_today():
    """The top of the admin: today so far in four plain answers (results,
    failures and what was fixed, signups and active customers, infra). The
    spend part comes from the shared report cache (it scans a month of
    executor calls); everything else is read live."""
    from admin_metrics import cache as admin_cache

    def compute_costs():
        conn = _connect()
        try:
            return views.cost_facts(conn.cursor(), datetime.now(timezone.utc))
        finally:
            conn.close()

    def costs():
        got = admin_cache.shared_report("/admin/video/ops/costs-now", {},
                                        compute_costs,
                                        logger=current_app.logger)
        if got.get("pending") or got.get("error"):
            return None
        return {k: v for k, v in got.items()
                if k not in ("computed_at", "cache_age_s", "refreshing")}
    conn = _connect()
    try:
        return jsonify(views.today(conn.cursor(), costs=costs))
    finally:
        conn.close()


@admin_ops_bp.route("/admin/video/ops/sessions", methods=["GET"])
@admin_required
def ops_sessions():
    verdict = request.args.get("verdict") or None
    if verdict and verdict not in VERDICTS:
        return jsonify({"error": "verdict must be one of "
                                 + ", ".join(VERDICTS)}), 400
    try:
        days = int(request.args.get("days", 30))
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 30))
    except ValueError:
        return jsonify({"error": "days, page and per_page are numbers"}), 400
    conn = _connect()
    try:
        return jsonify(views.sessions(conn.cursor(), days=days,
                                      verdict=verdict, page=page,
                                      per_page=per_page,
                                      presign=_admin_presign))
    finally:
        conn.close()


@admin_ops_bp.route("/admin/video/ops/tickets", methods=["GET"])
@admin_required
def ops_tickets():
    status = request.args.get("status") or "all"
    if status not in ("all", "active") + tuple(views.STATUS_ORDER):
        return jsonify({"error": "status must be all, active, open, fixing, "
                                 "fixed or wont_fix"}), 400
    conn = _connect()
    try:
        return jsonify(views.tickets(conn.cursor(), status=status,
                                     presign=_admin_presign))
    finally:
        conn.close()


# ── The CLI's storage helpers ────────────────────────────────────────────

def _ops_key():
    """(ok, expires_at). The header is `Authorization: Ops <key>`."""
    head = request.headers.get("Authorization") or ""
    if not head.startswith("Ops "):
        return False, None
    key = head[4:].strip()
    if not 30 <= len(key) <= 200:
        return False, None
    digest = hashlib.sha256(key.encode()).hexdigest()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.ops_review_keys') AS t")
            if not (cur.fetchone() or {}).get("t"):
                return False, None
            cur.execute("""SELECT expires_at FROM ops_review_keys
                            WHERE key_sha256 = %s AND expires_at > NOW()""",
                        (digest,))
            row = cur.fetchone()
            return bool(row), (row or {}).get("expires_at")
    finally:
        conn.close()


def _refuse():
    return jsonify({"error": "a valid ops review key is required"}), 401


@admin_ops_bp.route("/ops/review/whoami", methods=["GET"])
def ops_whoami():
    ok, exp = _ops_key()
    if not ok:
        return _refuse()
    return jsonify({"ok": True, "expires_at": exp.isoformat() if exp else None,
                    "storage": storage.is_configured()})


@admin_ops_bp.route("/ops/review/presign", methods=["POST"])
def ops_presign():
    ok, _ = _ops_key()
    if not ok:
        return _refuse()
    if not storage.is_configured():
        return jsonify({"error": "storage is not configured"}), 503
    body = request.get_json(silent=True) or {}
    try:
        ids = sorted({int(x) for x in body.get("asset_ids") or []})
    except (TypeError, ValueError):
        return jsonify({"error": "asset_ids must be numbers"}), 400
    keys = sorted({str(k) for k in body.get("keys") or []})
    if len(ids) + len(keys) > PRESIGN_MAX_ITEMS:
        return jsonify({"error": f"at most {PRESIGN_MAX_ITEMS} items"}), 400
    bad = [k for k in keys if ".." in k or not k.startswith(PRESIGN_PREFIXES)]
    if bad:
        return jsonify({"error": "keys outside the media prefixes",
                        "keys": bad[:5]}), 400
    try:
        expires = max(60, min(int(body.get("expires") or 3600),
                              PRESIGN_MAX_S))
    except (TypeError, ValueError):
        expires = 3600
    assets = {}
    if ids:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT id, storage_key,
                                      meta->>'r2_purged_at' AS purged
                                 FROM assets WHERE id = ANY(%s)""", (ids,))
                for r in cur.fetchall():
                    if r["storage_key"] and not r["purged"]:
                        assets[str(r["id"])] = storage.presign_get(
                            r["storage_key"], expires=expires)
        finally:
            conn.close()
    signed = {k: storage.presign_get(k, expires=expires) for k in keys}
    return jsonify({"assets": assets, "keys": signed, "expires_s": expires})


def _image_type(data):
    for name, magic in MAGIC:
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@admin_ops_bp.route("/ops/review/evidence", methods=["POST"])
def ops_evidence():
    ok, _ = _ops_key()
    if not ok:
        return _refuse()
    if not storage.is_configured():
        return jsonify({"error": "storage is not configured"}), 503
    slug = (request.args.get("session") or "").strip().lower()
    name = (request.args.get("name") or "").strip().lower()
    if not SLUG_RE.match(slug):
        return jsonify({"error": "session must be the session's evidence "
                                 "slug (letters, digits, - and _)"}), 400
    if not NAME_RE.match(name):
        return jsonify({"error": "name must look like frame-012s.jpg "
                                 "(png, jpg or webp)"}), 400
    if (request.content_length or 0) > EVIDENCE_MAX_BYTES:
        return jsonify({"error": "evidence images are at most 6 MB"}), 413
    data = request.get_data(cache=False)
    if not data or len(data) > EVIDENCE_MAX_BYTES:
        return jsonify({"error": "an image body of at most 6 MB is "
                                 "required"}), 400
    kind = _image_type(data)
    ext = name.rsplit(".", 1)[1]
    if kind is None or (kind != ext and not (kind == "jpg"
                                             and ext == "jpeg")):
        return jsonify({"error": "the body is not a PNG, JPEG or WebP "
                                 "image matching the file name"}), 400
    key = f"{EVIDENCE_PREFIX}{slug}/{name}"
    storage.client().put_object(
        Bucket=storage.bucket(), Key=key, Body=data,
        ContentType=CONTENT_TYPES[ext],
        Metadata={"uploaded": datetime.now(timezone.utc)
                  .strftime("%Y%m%dT%H%M%SZ"), "via": "ops_review"})
    return jsonify({"storage_key": key, "bytes": len(data),
                    "url": storage.presign_get(key, expires=3600)})
