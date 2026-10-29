#!/usr/bin/env python3
"""Move every object from the legacy store (R2) to the next store, verified.

Runs FROM THE BOX (13.140.241.231), with only the standard library, boto3
and this directory's storage_routing.py. Never prints a secret.

    install:  scp backend/storage_migrate.py backend/storage_routing.py \
                  root@box:/opt/valmera/storage-migrate/
    env file: /etc/valmera/storage-migrate.env (0600), the app's own names:
              S3_ENDPOINT S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_BUCKET
              S3_REGION                                  (legacy = R2)
              S3_NEXT_ENDPOINT S3_NEXT_ACCESS_KEY_ID S3_NEXT_SECRET_ACCESS_KEY
              S3_NEXT_BUCKET S3_NEXT_REGION              (next = the new one)
              DATABASE_URL                       (mode / reconcile only)
              S3_SETUP_ACCESS_KEY_ID S3_SETUP_SECRET_ACCESS_KEY
                                    (setup-bucket only; a temporary key)

    M="python3 /opt/valmera/storage-migrate/storage_migrate.py \
       --env-file /etc/valmera/storage-migrate.env"
    $M smoke                  # every S3 call the product makes, on next
    $M setup-bucket           # CORS (+ B2 lifecycle) on next, setup key
    $M mode copy --yes        # app_kv: reads next-first, R2 stays complete
    $M inventory              # list both stores into the state DB
    $M copy --workers 24 --max-mbps 400
    $M verify                 # re-read both sides, SHA-256 per object
    $M status                 # ready_for_switch: true|false
    $M mode dual_write --yes  # then inventory + copy again (incremental)
    $M mode next --yes        # THE SWITCH (refused unless ready_for_switch)
    $M mode rollback --yes    # back to R2 writes; everything stays readable
    $M copy --reverse ...     # (rollback) bring next-only objects into R2
    $M reconcile [--delete-orphans]

Checksums: every object is read from the source once, with SHA-256 (and MD5,
compared to the source ETag when that ETag is a plain MD5) computed on the
bytes as they arrive; after the upload the destination is read back in full
and its SHA-256 must match. A mismatching copy is deleted at once, so a
next-first read can never serve it, and the key goes back to the queue. An
object the app wrote to the destination itself is never overwritten.

State: a SQLite file (default /var/lib/valmera/storage-migrate.sqlite), so a
killed run resumes where it stopped.
"""

import argparse
import base64
import hashlib
import json
import os
import random
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage_routing as routing  # noqa: E402

MIB = 1024 * 1024
SINGLE_MAX = 64 * MIB
PART_MIN = 64 * MIB
MAX_PARTS = 9000
SHA_META = "valmera-sha256"
DEFAULT_STATE = "/var/lib/valmera/storage-migrate.sqlite"
CORS_ORIGINS = ["https://valmera.io", "https://www.valmera.io",
                "http://localhost:3000"]
READY_MAX_AGE_S = 2 * 3600
STATUSES = ("pending", "copied", "present", "verified", "mismatch",
            "failed", "conflict", "source_gone")
DONE = ("verified",)


# ── environment and stores ─────────────────────────────────────────────────

def load_env(path=None, base=None):
    env = dict(os.environ if base is None else base)
    if path:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                env[k] = v.strip().strip('"').strip("'")
    return env


class Store:
    """One S3-compatible bucket: a boto3 client plus its bucket name."""

    def __init__(self, name, cfg, client=None, pool=32):
        self.name = name
        self.cfg = cfg
        self.bucket = cfg["bucket"]
        self.host = cfg["host"]
        self._client = client
        self._pool = pool

    @property
    def c(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client(
                "s3", endpoint_url=self.cfg["endpoint"],
                aws_access_key_id=self.cfg["access_key_id"],
                aws_secret_access_key=self.cfg["secret_access_key"],
                region_name=self.cfg["region"],
                config=Config(**routing.botocore_config_kwargs(
                    self.cfg, connect_timeout=10, read_timeout=120,
                    retries={"max_attempts": 5, "mode": "standard"},
                    max_pool_connections=self._pool)))
        return self._client

    def list(self, prefix=""):
        """{key: {size, etag, mtime}} for every object under ``prefix``."""
        out, token = {}, None
        while True:
            kw = {"Bucket": self.bucket, "MaxKeys": 1000}
            if prefix:
                kw["Prefix"] = prefix
            if token:
                kw["ContinuationToken"] = token
            r = self.c.list_objects_v2(**kw)
            for o in r.get("Contents") or []:
                lm = o.get("LastModified")
                out[o["Key"]] = {
                    "size": int(o.get("Size") or 0),
                    "etag": str(o.get("ETag") or "").strip('"'),
                    "mtime": lm.timestamp() if hasattr(lm, "timestamp")
                    else float(lm or 0)}
            if not r.get("IsTruncated"):
                return out
            token = r.get("NextContinuationToken")
            if not token:
                return out

    def head(self, key):
        try:
            return self.c.head_object(Bucket=self.bucket, Key=key)
        except Exception as e:  # noqa: BLE001
            if routing.is_missing(e):
                return None
            raise


def stores(env, reverse=False, pool=32):
    legacy = routing.store_config(routing.LEGACY, env)
    nxt = routing.store_config(routing.NEXT, env)
    missing = [n for n, c in (("legacy (S3_*)", legacy),
                              ("next (S3_NEXT_*)", nxt)) if c is None]
    if missing:
        raise SystemExit("not configured: " + ", ".join(missing))
    src, dst = Store("legacy", legacy, pool=pool), Store("next", nxt,
                                                          pool=pool)
    return (dst, src) if reverse else (src, dst)


# ── state ──────────────────────────────────────────────────────────────────

class State:
    def __init__(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False,
                                  timeout=60)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects (
                    key TEXT PRIMARY KEY, size INTEGER, etag TEXT,
                    src_mtime REAL, status TEXT, sha256 TEXT,
                    dst_sha256 TEXT, attempts INTEGER DEFAULT 0,
                    error TEXT, seen_run INTEGER, updated_at REAL);
                CREATE INDEX IF NOT EXISTS objects_status
                    ON objects(status);
                CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            """)
            self.db.commit()

    def meta(self, k, v=None):
        with self.lock:
            if v is None:
                row = self.db.execute("SELECT v FROM meta WHERE k=?",
                                      (k,)).fetchone()
                return row[0] if row else None
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                            (k, str(v)))
            self.db.commit()

    def rows(self, where="1=1", args=()):
        with self.lock:
            cur = self.db.execute(
                f"SELECT key, size, etag, src_mtime, status, sha256, "
                f"dst_sha256, attempts FROM objects WHERE {where} "
                f"ORDER BY key", args)
            names = [c[0] for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def set(self, key, **fields):
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.db.execute(f"UPDATE objects SET {cols} WHERE key=?",
                            (*fields.values(), key))
            self.db.commit()

    def summary(self):
        with self.lock:
            by = {s: {"keys": n, "gb": round((b or 0) / 1e9, 3)}
                  for s, n, b in self.db.execute(
                      "SELECT status, COUNT(*), SUM(size) FROM objects "
                      "GROUP BY status")}
        return by


# ── throttling ─────────────────────────────────────────────────────────────

class Limiter:
    """A shared byte-rate ceiling (Mbit/s; 0 = none) so a copy never takes
    the box's whole uplink from customer renders."""

    def __init__(self, mbps):
        self.rate = max(0.0, float(mbps or 0)) * 1e6 / 8
        self.lock = threading.Lock()
        self.next_free = time.monotonic()

    def take(self, n):
        if self.rate <= 0 or n <= 0:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_free)
            self.next_free = start + n / self.rate
            wait = start - now
        if wait > 0:
            time.sleep(wait)


# ── copying one object ─────────────────────────────────────────────────────

def part_size(size):
    need = -(-int(size) // MAX_PARTS)
    need = -(-need // MIB) * MIB
    return max(PART_MIN, need)


def _ranged(store, key, start, end, limiter, attempts=4):
    """Bytes [start, end] of one object, retried as a unit."""
    last = None
    for i in range(attempts):
        try:
            r = store.c.get_object(Bucket=store.bucket, Key=key,
                                   Range=f"bytes={start}-{end}")
            data = r["Body"].read()
            if len(data) != end - start + 1:
                raise IOError(f"short read {len(data)} of "
                              f"{end - start + 1} at {start}")
            limiter.take(len(data))
            return data, r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(30, 2 ** i))
    raise last


def _head_extra(head):
    """The headers a copy must carry over (content type and friends)."""
    extra = {"ContentType": head.get("ContentType")
             or "application/octet-stream"}
    for h in ("ContentEncoding", "ContentDisposition", "CacheControl",
              "ContentLanguage"):
        if head.get(h):
            extra[h] = head[h]
    meta = {k: v for k, v in (head.get("Metadata") or {}).items()
            if k != SHA_META}
    return extra, meta


def hash_object(store, key, size, limiter, chunk=PART_MIN):
    """SHA-256 of one stored object, read in ranges."""
    h = hashlib.sha256()
    if size == 0:
        r = store.c.get_object(Bucket=store.bucket, Key=key)
        h.update(r["Body"].read())
        return h.hexdigest()
    pos = 0
    while pos < size:
        end = min(size, pos + chunk) - 1
        data, _r = _ranged(store, key, pos, end, limiter)
        h.update(data)
        pos = end + 1
    return h.hexdigest()


def copy_one(src, dst, key, size, etag, src_mtime, limiter,
             verify_inline=True, overwrite=False):
    """Copy one object; returns (status, fields). Never raises.

    ``overwrite`` (copy --overwrite, only while the source is authoritative:
    modes copy and dual_write) replaces whatever the destination holds."""
    try:
        have = None if overwrite else dst.head(key)
        if have is not None:
            dsize = int(have.get("ContentLength") or 0)
            if dsize == int(size):
                return "present", {}
            lm = have.get("LastModified")
            dmtime = lm.timestamp() if hasattr(lm, "timestamp") else 0
            if dmtime >= float(src_mtime or 0):
                # Written to the destination after the source copy: the
                # app's own newer object. Never overwrite it.
                return "conflict", {"error": "destination is newer "
                                    f"({dsize} vs {size} bytes)"}
        sha, md5 = hashlib.sha256(), hashlib.md5()  # noqa: S324 — ETag check
        head = r = None
        if int(size or 0) <= SINGLE_MAX:
            # Small objects (95% of the bucket): the GET's own headers stand
            # in for a HEAD, one round trip to R2 instead of two.
            try:
                r = src.c.get_object(Bucket=src.bucket, Key=key)
            except Exception as e:  # noqa: BLE001
                if routing.is_missing(e):
                    return "source_gone", {}
                raise
            head = r
            if int(r.get("ContentLength") or 0) > SINGLE_MAX:
                r["Body"].close()            # grew since the inventory
                r = None
        if head is None or r is None:
            head = src.head(key)
            if head is None:
                return "source_gone", {}
        size = int(head.get("ContentLength") or 0)
        extra, meta = _head_extra(head)
        if r is None and size <= SINGLE_MAX:     # shrank since the inventory
            r = src.c.get_object(Bucket=src.bucket, Key=key)
        if r is not None:
            data = r["Body"].read()
            if len(data) != size:
                raise IOError(f"read {len(data)} of {size} bytes")
            limiter.take(len(data))
            sha.update(data)
            md5.update(data)
            meta[SHA_META] = sha.hexdigest()
            dst.c.put_object(Bucket=dst.bucket, Key=key, Body=data,
                             Metadata=meta, **extra)
        else:
            psize = part_size(size)
            up = dst.c.create_multipart_upload(Bucket=dst.bucket, Key=key,
                                               Metadata=meta, **extra)
            upload_id, parts, pos, n = up["UploadId"], [], 0, 1
            try:
                while pos < size:
                    end = min(size, pos + psize) - 1
                    data, _r = _ranged(src, key, pos, end, limiter)
                    sha.update(data)
                    md5.update(data)
                    res = dst.c.upload_part(Bucket=dst.bucket, Key=key,
                                            UploadId=upload_id,
                                            PartNumber=n, Body=data)
                    parts.append({"PartNumber": n, "ETag": res["ETag"]})
                    pos, n = end + 1, n + 1
                dst.c.complete_multipart_upload(
                    Bucket=dst.bucket, Key=key, UploadId=upload_id,
                    MultipartUpload={"Parts": parts})
            except BaseException:
                try:
                    dst.c.abort_multipart_upload(Bucket=dst.bucket, Key=key,
                                                 UploadId=upload_id)
                except Exception:  # noqa: BLE001
                    pass
                raise
        digest = sha.hexdigest()
        src_etag = str(head.get("ETag") or etag or "").strip('"').lower()
        if len(src_etag) == 32 and "-" not in src_etag and \
                src_etag != md5.hexdigest():
            _delete(dst, key)
            return "failed", {"error": "source bytes do not match the "
                              "source ETag (read corrupted in transit)",
                              "sha256": digest}
        if src.head(key) is None:
            # Deleted while we copied (retention, a user's delete): the
            # copy must not outlive it.
            _delete(dst, key)
            return "source_gone", {"sha256": digest}
        if not verify_inline:
            return "copied", {"sha256": digest, "error": None}
        back = hash_object(dst, key, size, limiter)
        if back != digest:
            _delete(dst, key)
            return "mismatch", {"sha256": digest, "dst_sha256": back,
                                "error": "destination read-back differs"}
        return "verified", {"sha256": digest, "dst_sha256": back,
                            "error": None}
    except Exception as e:  # noqa: BLE001
        return "failed", {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def _delete(store, key):
    try:
        store.c.delete_object(Bucket=store.bucket, Key=key)
    except Exception:  # noqa: BLE001
        pass


def verify_one(src, dst, row, limiter):
    """Full read of both sides. (status, fields)."""
    key, size = row["key"], int(row["size"] or 0)
    try:
        have = dst.head(key)
        if have is None:
            return "pending", {"error": "missing in destination"}
        if int(have.get("ContentLength") or 0) != size:
            return ("conflict" if row["status"] == "present"
                    else "pending"), {"error": "size differs"}
        if src.head(key) is None:
            return "source_gone", {}
        want = row.get("sha256") or hash_object(src, key, size, limiter)
        back = hash_object(dst, key, size, limiter)
        if back == want:
            return "verified", {"sha256": want, "dst_sha256": back,
                                "error": None}
        if row["status"] == "present":
            # Not our copy: never delete what the app may have written.
            return "conflict", {"sha256": want, "dst_sha256": back,
                                "error": "same size, different bytes"}
        _delete(dst, key)
        return "mismatch", {"sha256": want, "dst_sha256": back,
                            "error": "destination read-back differs"}
    except Exception as e:  # noqa: BLE001
        return "failed", {"error": f"{type(e).__name__}: {str(e)[:300]}"}


# ── commands ───────────────────────────────────────────────────────────────

def cmd_inventory(args, env, state, src=None, dst=None):
    if src is None:
        src, dst = stores(env, args.reverse)
    with state.lock:
        run = int(state.db.execute(
            "SELECT COALESCE(MAX(seen_run), 0) FROM objects").fetchone()[0]) + 1
    t0 = time.time()
    listed = src.list(args.prefix or "")
    with state.lock:
        cur = state.db.cursor()
        known = {k: (s, e, st) for k, s, e, st in cur.execute(
            "SELECT key, size, etag, status FROM objects")}
        for key, o in listed.items():
            old = known.get(key)
            if old is None:
                cur.execute(
                    "INSERT INTO objects (key, size, etag, src_mtime, "
                    "status, seen_run, updated_at) VALUES "
                    "(?, ?, ?, ?, 'pending', ?, ?)",
                    (key, o["size"], o["etag"], o["mtime"], run, time.time()))
            elif (old[0], old[1]) != (o["size"], o["etag"]) or \
                    old[2] == "source_gone":
                cur.execute(
                    "UPDATE objects SET size=?, etag=?, src_mtime=?, "
                    "status='pending', sha256=NULL, dst_sha256=NULL, "
                    "attempts=0, error=NULL, seen_run=?, updated_at=? "
                    "WHERE key=?",
                    (o["size"], o["etag"], o["mtime"], run, time.time(), key))
            else:
                cur.execute("UPDATE objects SET seen_run=? WHERE key=?",
                            (run, key))
        if not args.prefix:
            cur.execute("UPDATE objects SET status='source_gone', "
                        "updated_at=? WHERE seen_run <> ? "
                        "AND status <> 'source_gone'", (time.time(), run))
        state.db.commit()
    dst_listed = dst.list(args.prefix or "")
    present = 0
    with state.lock:
        cur = state.db.cursor()
        for key, o in dst_listed.items():
            src_o = listed.get(key)
            if src_o and src_o["size"] == o["size"]:
                n = cur.execute(
                    "UPDATE objects SET status='present', updated_at=? "
                    "WHERE key=? AND status IN ('pending', 'failed')",
                    (time.time(), key)).rowcount
                present += n
        state.db.commit()
    orphans = [k for k in dst_listed if k not in listed]
    state.meta("inventory_at", time.time())
    state.meta("inventory_full", "0" if args.prefix else "1")
    state.meta("route", f"{src.name}:{src.host}/{src.bucket} -> "
                        f"{dst.name}:{dst.host}/{dst.bucket}")
    out = {"source_keys": len(listed),
           "source_gb": round(sum(o["size"] for o in listed.values()) / 1e9,
                              3),
           "destination_keys": len(dst_listed),
           "destination_only": len(orphans),
           "newly_present": present,
           "by_prefix": _by_prefix(listed),
           "status": state.summary(),
           "seconds": round(time.time() - t0, 1)}
    print(json.dumps(out, indent=1))
    return out


def _by_prefix(listed):
    agg = {}
    for key, o in listed.items():
        p = key.split("/", 1)[0]
        a = agg.setdefault(p, [0, 0])
        a[0] += 1
        a[1] += o["size"]
    return {p: {"keys": n, "gb": round(b / 1e9, 3)}
            for p, (n, b) in sorted(agg.items(), key=lambda x: -x[1][1])}


def _run_pool(rows, fn, workers, label):
    total = len(rows)
    nbytes = sum(int(r["size"] or 0) for r in rows)
    done = moved = 0
    t0 = last = time.time()
    counts = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(fn, r): r for r in rows}
        for fut in as_completed(futs):
            r = futs[fut]
            status = fut.result()
            counts[status] = counts.get(status, 0) + 1
            done += 1
            moved += int(r["size"] or 0)
            if time.time() - last >= 30 or done == total:
                last = time.time()
                rate = moved / max(1e-6, last - t0) / 1e6
                print(f"[{label}] {done}/{total} objects, "
                      f"{moved / 1e9:.2f}/{nbytes / 1e9:.2f} GB, "
                      f"{rate:.1f} MB/s, {counts}", flush=True)
    return counts


def cmd_copy(args, env, state, src=None, dst=None, mode_reader=None):
    if src is None:
        src, dst = stores(env, args.reverse, pool=args.workers * 2 + 4)
    limiter = Limiter(args.max_mbps)
    statuses = "'pending', 'failed', 'mismatch'"
    if args.overwrite:
        mode = (mode_reader or (lambda: read_mode(env)))()
        if args.reverse or mode not in ("copy", "dual_write"):
            raise SystemExit(f"refusing --overwrite: mode is {mode!r}; the "
                             "source is authoritative only in copy or "
                             "dual_write (forward)")
        statuses += ", 'conflict'"
    where = f"status IN ({statuses}) AND attempts < ?"
    params = [args.max_attempts]
    if args.prefix:
        where += " AND key LIKE ?"
        params.append(args.prefix.replace("%", r"\%") + "%")
    rows = state.rows(where, tuple(params))
    if args.limit:
        rows = rows[:args.limit]

    def one(r):
        status, fields = copy_one(src, dst, r["key"], r["size"], r["etag"],
                                  r["src_mtime"], limiter,
                                  verify_inline=not args.no_verify,
                                  overwrite=bool(args.overwrite
                                                 and r["status"] == "conflict"))
        state.set(r["key"], status=status, attempts=int(r["attempts"]) + 1,
                  **fields)
        return status

    counts = _run_pool(rows, one, args.workers, "copy")
    out = {"copied": counts, "status": state.summary()}
    print(json.dumps(out, indent=1))
    return out


def cmd_verify(args, env, state, src=None, dst=None):
    if src is None:
        src, dst = stores(env, args.reverse, pool=args.workers * 2 + 4)
    limiter = Limiter(args.max_mbps)
    statuses = ("copied", "present") + (("verified",) if args.all else ())
    rows = state.rows("status IN (%s)" % ",".join("?" * len(statuses)),
                      statuses)
    if args.sample and len(rows) > args.sample:
        rows = random.sample(rows, args.sample)

    def one(r):
        status, fields = verify_one(src, dst, r, limiter)
        state.set(r["key"], status=status, **fields)
        return status

    counts = _run_pool(rows, one, args.workers, "verify")
    out = {"verified": counts, "status": state.summary()}
    print(json.dumps(out, indent=1))
    return out


def readiness(state, now=None):
    now = now or time.time()
    s = state.summary()
    blocking = {k: v for k, v in s.items()
                if k not in ("verified", "source_gone") and v["keys"]}
    inv = float(state.meta("inventory_at") or 0)
    fresh = now - inv <= READY_MAX_AGE_S and \
        state.meta("inventory_full") == "1"
    return {"ready_for_switch": bool(fresh and not blocking
                                     and s.get("verified")),
            "inventory_age_min": round((now - inv) / 60, 1) if inv else None,
            "inventory_full": state.meta("inventory_full") == "1",
            "blocking": blocking, "status": s,
            "route": state.meta("route")}


def cmd_status(args, env, state):
    out = readiness(state)
    print(json.dumps(out, indent=1))
    return out


def cmd_reconcile(args, env, state, src=None, dst=None, mode_reader=None):
    """Destination-only keys: in ``copy`` and ``dual_write`` every app
    write reaches the source first, so an object only the destination holds
    is a copy that outlived its source (deleted mid-copy) and may go."""
    if src is None:
        src, dst = stores(env, args.reverse)
    src_keys = src.list(args.prefix or "")
    dst_keys = dst.list(args.prefix or "")
    cutoff = time.time() - args.grace_min * 60
    orphans = [k for k, o in dst_keys.items()
               if k not in src_keys and o["mtime"] < cutoff]
    out = {"destination_only": len(orphans),
           "gb": round(sum(dst_keys[k]["size"] for k in orphans) / 1e9, 3),
           "sample": orphans[:20], "deleted": 0}
    if args.delete_orphans and orphans:
        mode = (mode_reader or (lambda: read_mode(env)))()
        if args.reverse or mode not in ("copy", "dual_write"):
            raise SystemExit(f"refusing to delete: mode is {mode!r}; "
                             "destination-only objects are orphans only in "
                             "copy or dual_write (forward)")
        for i in range(0, len(orphans), 1000):
            dst.c.delete_objects(Bucket=dst.bucket, Delete={
                "Objects": [{"Key": k} for k in orphans[i:i + 1000]],
                "Quiet": True})
        out["deleted"] = len(orphans)
    print(json.dumps(out, indent=1))
    return out


# ── the switch (app_kv) ────────────────────────────────────────────────────

def _db(env):
    import psycopg2
    url = env.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set (env file)")
    return psycopg2.connect(url, connect_timeout=10)


def read_mode(env):
    conn = _db(env)
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT value FROM app_kv WHERE key = %s",
                        (routing.KV_KEY,))
            row = cur.fetchone()
        conn.rollback()
        return routing.parse_mode(row[0] if row else None) or "legacy"
    finally:
        conn.close()


def check_health(urls, timeout=15):
    """[(url, ok, note)]: every process must report the next store."""
    out = []
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except Exception:  # noqa: BLE001
                out.append((url, False, f"HTTP {e.code}"))
                continue
        except Exception as e:  # noqa: BLE001
            out.append((url, False, f"{type(e).__name__}: {str(e)[:120]}"))
            continue
        st = body.get("storage") or {}
        ok = bool(st.get("next"))
        out.append((url, ok, json.dumps(st)[:200]))
    return out


def read_probes(env):
    """[(row, ok, note)] for every app_kv 'storage_probe_*' row (processes
    without a health route say which stores they use there)."""
    conn = _db(env)
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT key, value FROM app_kv "
                        "WHERE key LIKE 'storage_probe_%%'")
            rows = cur.fetchall()
        conn.rollback()
    finally:
        conn.close()
    out = []
    for key, value in rows:
        try:
            st = json.loads(value or "{}")
        except ValueError:
            st = {}
        out.append((key, bool(st.get("next")), str(value)[:200]))
    return out


def cmd_mode(args, env, state, reader=read_mode, writer=None,
             health=check_health, probes=read_probes):
    current = reader(env)
    if not args.mode:
        print(json.dumps({"mode": current}))
        return current
    want = routing.parse_mode(args.mode)
    if want is None:
        raise SystemExit(f"unknown mode {args.mode!r}; one of "
                         + ", ".join(routing.MODES))
    if want in ("next", "next_only") and not args.force:
        ready = readiness(state)
        if not ready["ready_for_switch"]:
            raise SystemExit("refusing the switch: " + json.dumps(ready))
    if want == "legacy" and current in ("next", "rollback", "next_only") \
            and not args.force:
        raise SystemExit(
            f"refusing {current} -> legacy: objects written to the next "
            "store since the switch would become unreadable. Use 'rollback' "
            "(R2 writes, both stores readable), then `copy --reverse`, "
            "then legacy with --force.")
    if want in ("next", "next_only") and not args.force:
        # Every process must see the next store, or it would miss objects
        # that only exist there (copy and dual_write are safe without, and
        # the rollback switch is never refused).
        seen = list(health(args.health)) if args.health else []
        seen += list(probes(env))
        bad = [h for h in seen if not h[1]]
        if bad or not seen:
            raise SystemExit("refusing: these processes do not report the "
                             "next store: " + json.dumps(bad or "none seen"))
    if not args.yes:
        print(json.dumps({"would_set": want, "current": current}))
        return current
    value = json.dumps({"mode": want, "previous": current,
                        "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime()),
                        "by": "storage_migrate", "note": args.note or ""})
    if writer is not None:
        writer(value)
    else:
        conn = _db(env)
        try:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                               VALUES (%s, %s, NOW())
                               ON CONFLICT (key) DO UPDATE
                               SET value = EXCLUDED.value,
                                   updated_at = NOW()""",
                            (routing.KV_KEY, value))
            conn.commit()
        finally:
            conn.close()
    print(json.dumps({"set": want, "previous": current,
                      "takes_effect_within_s": 30}))
    return want


# ── smoke test and bucket setup ────────────────────────────────────────────

def _http(method, url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _hdr(headers, name):
    for k, v in (headers or {}).items():
        if k.lower() == name.lower():
            return v
    return None


def cmd_smoke(args, env, state=None, store=None, http=_http):
    """Every call the product makes, against one store, under a scratch
    prefix that is removed afterwards. Exit status 1 on any failure."""
    if store is None:
        cfg = routing.store_config(args.store, env)
        if cfg is None:
            raise SystemExit(f"store {args.store} is not configured")
        store = Store(args.store, cfg)
    c, b = store.c, store.bucket
    prefix = f"_smoke/{int(time.time())}-{random.randrange(1 << 30):08x}/"
    results = []

    def check(name, fn):
        try:
            note = fn()
            results.append((name, True, note or ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, f"{type(e).__name__}: "
                                         f"{str(e)[:200]}"))

    small = prefix + "small.txt"
    body = b"valmera-smoke-0123456789"

    def put_head_range():
        c.put_object(Bucket=b, Key=small, Body=body, ContentType="text/plain")
        n = int(c.head_object(Bucket=b, Key=small)["ContentLength"])
        assert n == len(body), n
        got = c.get_object(Bucket=b, Key=small, Range="bytes=2-5")["Body"]
        got = got.read()
        assert got == body[2:6], got
        return f"{n} bytes, range ok"
    check("put/head/range-get", put_head_range)

    def presigned_get():
        url = c.generate_presigned_url(
            "get_object", Params={
                "Bucket": b, "Key": small,
                "ResponseContentDisposition": 'attachment; filename="v.txt"'},
            ExpiresIn=600)
        status, headers, data = http("GET", url, headers={
            "Range": "bytes=0-3"})
        assert status in (200, 206) and data == body[:4], (status, data)
        disp = _hdr(headers, "Content-Disposition") or ""
        return f"status {status}, disposition {'ok' if 'v.txt' in disp else 'IGNORED'}"
    check("presigned GET (range, filename)", presigned_get)

    def presigned_put():
        key = prefix + "put.bin"
        url = c.generate_presigned_url(
            "put_object", Params={"Bucket": b, "Key": key,
                                  "ContentType": "video/mp4"},
            ExpiresIn=600)
        status, headers, _ = http("PUT", url, data=b"x" * 1000, headers={
            "Content-Type": "video/mp4", "Origin": CORS_ORIGINS[0]})
        assert status == 200, status
        n = int(c.head_object(Bucket=b, Key=key)["ContentLength"])
        assert n == 1000, n
        expose = (_hdr(headers, "Access-Control-Expose-Headers") or "")
        return ("etag exposed" if "etag" in expose.lower()
                else "ETag NOT exposed to browsers (run setup-bucket)")
    check("presigned PUT", presigned_put)

    mp_key = prefix + "multi.bin"

    def multipart():
        up = c.create_multipart_upload(Bucket=b, Key=mp_key,
                                       ContentType="video/mp4")
        uid = up["UploadId"]
        sizes = [5 * MIB, 1024]
        parts = []
        for i, n in enumerate(sizes, 1):
            url = c.generate_presigned_url(
                "upload_part", Params={"Bucket": b, "Key": mp_key,
                                       "UploadId": uid, "PartNumber": i},
                ExpiresIn=600)
            status, headers, _ = http("PUT", url, data=os.urandom(n))
            assert status == 200, (i, status)
            parts.append({"PartNumber": i, "ETag": _hdr(headers, "ETag")})
        listed = c.list_parts(Bucket=b, Key=mp_key, UploadId=uid)
        assert len(listed.get("Parts") or []) == 2, listed.get("Parts")
        ups = c.list_multipart_uploads(Bucket=b, Prefix=mp_key)
        assert any(u.get("UploadId") == uid
                   for u in ups.get("Uploads") or []), "upload not listed"
        c.complete_multipart_upload(Bucket=b, Key=mp_key, UploadId=uid,
                                    MultipartUpload={"Parts": parts})
        n = int(c.head_object(Bucket=b, Key=mp_key)["ContentLength"])
        assert n == sum(sizes), n
        return f"{n} bytes in 2 presigned parts; list_parts and " \
               f"list_multipart_uploads ok"
    check("multipart (presigned parts)", multipart)

    def big_copy():
        from boto3.s3.transfer import TransferConfig
        cfg = TransferConfig(multipart_threshold=5 * MIB,
                             multipart_chunksize=5 * MIB)
        c.copy({"Bucket": b, "Key": mp_key}, b, prefix + "copy.bin",
               Config=cfg)
        n = int(c.head_object(Bucket=b, Key=prefix + "copy.bin")
                ["ContentLength"])
        assert n == 5 * MIB + 1024, n
        return "UploadPartCopy ok"
    check("server-side multipart copy", big_copy)

    def cors_preflight():
        url = c.generate_presigned_url(
            "put_object", Params={"Bucket": b, "Key": prefix + "cors.bin"},
            ExpiresIn=600)
        status, headers, _ = http("OPTIONS", url, headers={
            "Origin": CORS_ORIGINS[0],
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type"})
        allow = _hdr(headers, "Access-Control-Allow-Origin") or ""
        assert status in (200, 204) and allow in (CORS_ORIGINS[0], "*"), \
            (status, allow)
        return f"allow-origin {allow}"
    check("CORS preflight from valmera.io", cors_preflight)

    def cleanup():
        keys = [o["Key"] for o in c.list_objects_v2(
            Bucket=b, Prefix=prefix).get("Contents") or []]
        if keys:
            c.delete_objects(Bucket=b, Delete={
                "Objects": [{"Key": k} for k in keys], "Quiet": True})
        left = c.list_objects_v2(Bucket=b, Prefix=prefix).get("Contents")
        assert not left, f"{len(left)} left"
        return f"{len(keys)} deleted"
    check("delete + list", cleanup)

    ok = all(r[1] for r in results)
    print(json.dumps({"store": store.name, "host": store.host,
                      "bucket": b, "ok": ok,
                      "checks": [{"check": n, "ok": o, "note": t}
                                 for n, o, t in results]}, indent=1))
    if not ok and state is not None:
        raise SystemExit(1)
    return ok, results


def cors_rules(origins=None):
    return {"CORSRules": [{
        "AllowedOrigins": list(origins or CORS_ORIGINS),
        "AllowedMethods": ["GET", "PUT", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag", "Content-Length", "Content-Range",
                          "Accept-Ranges"],
        "MaxAgeSeconds": 3600}]}


B2_LIFECYCLE = [{
    # Keep only the newest version (an overwrite or a delete hides the old
    # one, gone a day later), and drop a multipart upload nobody finished
    # within 7 days: R2's defaults, which a versioned B2 bucket lacks.
    "fileNamePrefix": "",
    "daysFromUploadingToHiding": None,
    "daysFromHidingToDeleting": 1,
    "daysFromStartingToCancelingUnfinishedLargeFiles": 7,
}]


def _b2(method, url, payload=None, auth=None, http=_http):
    headers = {"Authorization": auth} if auth else {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    status, _h, body = http(method, url, data=data, headers=headers)
    try:
        doc = json.loads(body or b"{}")
    except ValueError:
        doc = {}
    if status != 200:
        raise RuntimeError(f"B2 {url.rsplit('/', 1)[-1]}: HTTP {status} "
                           f"{doc.get('code')}: {doc.get('message')}")
    return doc


def cmd_setup_bucket(args, env, state=None, client=None, http=_http):
    """CORS for browser uploads (with ETag exposed) and, on Backblaze, the
    lifecycle rules; needs a key that may change bucket settings
    (S3_SETUP_*, deleted afterwards). Bucket-restricted app keys may not."""
    cfg = routing.store_config(routing.NEXT, env)
    if cfg is None:
        raise SystemExit("S3_NEXT_* is not configured")
    kid = env.get("S3_SETUP_ACCESS_KEY_ID") or cfg["access_key_id"]
    secret = env.get("S3_SETUP_SECRET_ACCESS_KEY") or \
        cfg["secret_access_key"]
    setup_cfg = dict(cfg, access_key_id=kid, secret_access_key=secret)
    store = Store("next-setup", setup_cfg, client=client)
    out = {"bucket": cfg["bucket"], "host": cfg["host"]}
    try:
        store.c.put_bucket_cors(Bucket=cfg["bucket"],
                                CORSConfiguration=cors_rules())
        got = store.c.get_bucket_cors(Bucket=cfg["bucket"])
        out["cors"] = got.get("CORSRules")
    except Exception as e:  # noqa: BLE001
        out["cors_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    if cfg["host"].endswith("backblazeb2.com"):
        try:
            basic = base64.b64encode(f"{kid}:{secret}".encode()).decode()
            acct = _b2("GET", "https://api.backblazeb2.com/b2api/v2/"
                       "b2_authorize_account", auth="Basic " + basic,
                       http=http)
            api, tok = acct["apiUrl"], acct["authorizationToken"]
            buckets = _b2("POST", f"{api}/b2api/v2/b2_list_buckets",
                          {"accountId": acct["accountId"],
                           "bucketName": cfg["bucket"]}, auth=tok,
                          http=http).get("buckets") or []
            if not buckets:
                raise RuntimeError(f"bucket {cfg['bucket']} not visible "
                                   "to this key")
            done = _b2("POST", f"{api}/b2api/v2/b2_update_bucket",
                       {"accountId": acct["accountId"],
                        "bucketId": buckets[0]["bucketId"],
                        "lifecycleRules": B2_LIFECYCLE}, auth=tok,
                       http=http)
            out["lifecycle"] = done.get("lifecycleRules")
            out["bucket_type"] = done.get("bucketType")
        except Exception as e:  # noqa: BLE001
            out["lifecycle_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    ok = "cors" in out and ("lifecycle_error" not in out)
    out["ok"] = ok
    if not ok:
        out["hint"] = ("use a key allowed to change bucket settings: in "
                       "Backblaze, an Application Key with access to ALL "
                       "buckets (Read and Write), as S3_SETUP_ACCESS_KEY_ID "
                       "/ S3_SETUP_SECRET_ACCESS_KEY; delete it afterwards")
    print(json.dumps(out, indent=1))
    if not ok and state is not None:
        raise SystemExit(1)
    return out


# ── CLI ────────────────────────────────────────────────────────────────────

def parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--env-file")
    p.add_argument("--state", default=DEFAULT_STATE)
    p.add_argument("--reverse", action="store_true",
                   help="copy next -> legacy (after a rollback)")
    p.add_argument("--prefix", default="")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    for name in ("copy", "verify"):
        s = sub.add_parser(name)
        s.add_argument("--workers", type=int, default=24)
        s.add_argument("--max-mbps", type=float, default=400)
        if name == "copy":
            s.add_argument("--limit", type=int, default=0)
            s.add_argument("--max-attempts", type=int, default=5)
            s.add_argument("--no-verify", action="store_true")
            s.add_argument("--overwrite", action="store_true",
                           help="also re-copy 'conflict' keys over the "
                                "destination (modes copy/dual_write only)")
        else:
            s.add_argument("--all", action="store_true")
            s.add_argument("--sample", type=int, default=0)
    sub.add_parser("status")
    r = sub.add_parser("reconcile")
    r.add_argument("--grace-min", type=float, default=60)
    r.add_argument("--delete-orphans", action="store_true")
    m = sub.add_parser("mode")
    m.add_argument("mode", nargs="?")
    m.add_argument("--yes", action="store_true")
    m.add_argument("--force", action="store_true")
    m.add_argument("--note", default="")
    m.add_argument("--health", action="append", default=[],
                   help="a /health or /healthz URL that must report the "
                        "next store before dual_write/next (repeatable)")
    s = sub.add_parser("smoke")
    s.add_argument("--store", choices=routing.STORES, default="next")
    sub.add_parser("setup-bucket")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    env = load_env(args.env_file)
    if args.cmd in ("smoke", "setup-bucket"):
        fn = cmd_smoke if args.cmd == "smoke" else cmd_setup_bucket
        return fn(args, env, state=True)
    state = State(args.state)
    return {"inventory": cmd_inventory, "copy": cmd_copy,
            "verify": cmd_verify, "status": cmd_status,
            "reconcile": cmd_reconcile, "mode": cmd_mode}[args.cmd](
        args, env, state)


if __name__ == "__main__":
    main()
