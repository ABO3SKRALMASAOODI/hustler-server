"""R2 retention sweeper (owner decision D4, 25 Oct 2026).

Pinned: the policy windows; what is protected (any payment row, any billing
or trial trace, a subscription, a paid plan, the internal accounts); that a
key is selected only when every reference is; that a dry run deletes
nothing; that a live run deletes in batches, marks exactly the deleted rows,
stops on an error and writes a manifest of the deleted keys only; and that
an outsized or protected selection is refused before anything is deleted.
"""

import datetime as dt

import r2_retention as r2

NOW = dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.timezone.utc)


def _key(key, category, users=(500,), nbytes=10**9, kinds=("original",)):
    return {"storage_key": key, "bytes": nbytes, "category": category,
            "kept_reason": None if category else "paying",
            "kinds": list(kinds), "asset_ids": [1], "project_ids": [7],
            "user_ids": list(users)}


def test_the_policy_is_the_owners():
    assert r2.SOURCE_DAYS == 7 and r2.RENDER_DAYS == 30
    assert r2.OWNER_PREVIEW_DAYS == 14 and r2.OWNER_USER_ID == 60
    assert set(r2.SOURCE_KINDS) == {"original", "video_clip", "proxy",
                                    "audio"}
    assert r2.RENDER_KINDS == ("render",)
    assert r2.DEFAULT_MODE in r2.MODES


def test_the_selection_protects_every_customer_trace():
    sql = " ".join(r2.SELECTION_SQL.split())
    # any payment row of any status, any billing/trial trace, plan, flag
    assert "SELECT user_id AS id FROM payments WHERE user_id IS NOT NULL" in sql
    for clause in ("COALESCE(is_subscribed, 0) <> 0",
                   "subscription_id IS NOT NULL",
                   "COALESCE(plan, 'free') <> 'free'",
                   "billing_status IS NOT NULL", "trial_status IS NOT NULL",
                   "trial_subscription_id IS NOT NULL"):
        assert clause in sql
    assert "unnest(%(internal)s::int[])" in sql
    # protected accounts never reach the never-paid branches
    assert sql.index("WHEN pu.id IS NOT NULL THEN NULL") < sql.index(
        "THEN 'never_paid_source'")
    # activity is the account's newest trace anywhere
    for source in ("u.last_seen_at", "FROM video_jobs", "FROM client_events",
                   "FROM chat_messages", "FROM mcp_oauth_grants",
                   "FROM mcp_tokens", "FROM projects", "FROM edls"):
        assert source in sql
    # already-purged rows are skipped; a key needs EVERY reference selected
    assert "WHEN x.meta ? 'r2_purged_at' THEN NULL" in sql
    assert "CASE WHEN BOOL_AND(category IS NOT NULL) THEN MIN(category) END" \
        in sql
    # the owner keeps each project's newest render and active projects
    assert "AND x.id <> nr.id" in sql
    assert "pa.last_active <" in sql
    assert "x.meta->>'quality' = 'approval'" in sql


def test_verify_refuses_any_selected_key_of_a_protected_account():
    good = [_key("originals/7/a.mp4", "never_paid_source"),
            _key("media/9/p.mp4", "owner_approval_preview", users=(60,))]
    assert r2.verify(good, protected={900}) == []
    bad = [_key("originals/7/b.mp4", "never_paid_source", users=(500, 900)),
           _key("originals/7/c.mp4", "never_paid_render", users=(60,)),
           _key("media/9/q.mp4", "owner_approval_preview", users=(60, 500)),
           _key("x", "something_else")]
    assert r2.verify(bad, protected={900}) == [
        "originals/7/b.mp4", "originals/7/c.mp4", "media/9/q.mp4", "x"]


def test_the_report_counts_selected_and_kept_gigabytes():
    rows = [_key("a", "never_paid_source"), _key("b", "never_paid_render",
                                                kinds=("render",)),
            _key("c", None, nbytes=2 * 10**9)]
    out = r2.report(rows)
    assert out["selected_gb"] == 2.0 and out["selected_keys"] == 2
    assert out["kept"]["paying"] == {"keys": 1, "gb": 2.0}
    assert out["total_gb"] == 4.0
    assert out["selected_by_kind"]["never_paid_render:render"]["keys"] == 1


class _Cur:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.log.append((" ".join(sql.split()), params))


class _Conn:
    def __init__(self):
        self.log, self.commits = [], 0

    def cursor(self):
        return _Cur(self.log)

    def commit(self):
        self.commits += 1


class _S3:
    def __init__(self, fail_batch=None, errors=None):
        self.calls, self.fail_batch, self.errors = [], fail_batch, errors

    def delete_objects(self, Bucket, Delete):
        self.calls.append([o["Key"] for o in Delete["Objects"]])
        if self.fail_batch is not None and len(self.calls) - 1 == \
                self.fail_batch:
            raise RuntimeError("R2 down")
        keys = [o["Key"] for o in Delete["Objects"]]
        errs = [k for k in keys if k in (self.errors or ())]
        return {"Deleted": [{"Key": k} for k in keys if k not in errs],
                "Errors": [{"Key": k, "Code": "AccessDenied",
                            "Message": "no"} for k in errs]}


class _Storage:
    def __init__(self, s3):
        self.s3 = s3

    def client(self):
        return self.s3

    def bucket(self):
        return "valmera"


def _selection(monkeypatch, rows, protected=frozenset()):
    monkeypatch.setattr(r2, "select",
                        lambda conn, now: (rows, set(protected)))


def test_a_dry_run_deletes_nothing_and_writes_the_manifest(monkeypatch):
    rows = [_key(f"originals/7/{i}.mp4", "never_paid_source")
            for i in range(3)] + [_key("kept.mp4", None)]
    _selection(monkeypatch, rows)
    s3, conn, docs, saved = _S3(), _Conn(), [], []
    out = r2.run(conn, "dry_run", storage_mod=_Storage(s3), now=NOW,
                 write_manifest=lambda d: docs.append(d) or "m.json",
                 save_summary=saved.append)
    assert s3.calls == [] and conn.log == []
    assert out["selected_keys"] == 3 and out["manifest"] == "m.json"
    assert [k["key"] for k in docs[0]["keys"]] == [
        "originals/7/0.mp4", "originals/7/1.mp4", "originals/7/2.mp4"]
    assert docs[0]["mode"] == "dry_run" and saved[0] is out


def test_a_live_run_deletes_in_batches_and_marks_only_deleted_rows(
        monkeypatch):
    rows = [_key(f"originals/7/{i}.mp4", "never_paid_source", nbytes=10**6)
            for i in range(2500)] + [_key("kept.mp4", None)]
    _selection(monkeypatch, rows)
    s3 = _S3(errors={"originals/7/5.mp4"})
    conn, docs = _Conn(), []
    out = r2.run(conn, "live", storage_mod=_Storage(s3), now=NOW,
                 write_manifest=lambda d: docs.append(d) or "m.json")
    assert [len(c) for c in s3.calls] == [1000, 1000, 500]
    assert "kept.mp4" not in {k for c in s3.calls for k in c}
    assert out["deleted_keys"] == 2499 and out["errors"] == 1
    marks = [p for sql, p in conn.log if sql.startswith("UPDATE assets")]
    assert len(marks) == 3
    marked = [k for p in marks for k in p[2]]
    assert "originals/7/5.mp4" not in marked and len(marked) == 2499
    assert all(p[1] == r2.POLICY_VERSION for p in marks)
    # the manifest lists exactly the deleted keys
    assert len(docs[0]["keys"]) == 2499
    assert docs[0]["errors"][0]["key"] == "originals/7/5.mp4"


def test_a_live_run_stops_at_the_first_failed_batch(monkeypatch):
    rows = [_key(f"k{i}", "never_paid_render", nbytes=1)
            for i in range(2500)]
    _selection(monkeypatch, rows)
    s3, conn = _S3(fail_batch=1), _Conn()
    out = r2.run(conn, "live", storage_mod=_Storage(s3), now=NOW)
    assert len(s3.calls) == 2
    assert out["deleted_keys"] == 1000 and out["errors"] == 1


def test_an_outsized_or_protected_selection_is_refused(monkeypatch):
    s3 = _S3()
    _selection(monkeypatch, [_key("a", "never_paid_source",
                                  nbytes=int(r2.MAX_RUN_GB * 1e9) + 1)])
    out = r2.run(_Conn(), "live", storage_mod=_Storage(s3), now=NOW)
    assert "exceeds the per-run cap" in out["refused"] and s3.calls == []
    _selection(monkeypatch, [_key("b", "never_paid_source", users=(900,))],
               protected={900})
    saved = []
    out = r2.run(_Conn(), "live", storage_mod=_Storage(s3), now=NOW,
                 save_summary=saved.append)
    assert "protected account" in out["refused"] and s3.calls == []
    assert saved and saved[0]["refused_sample"] == ["b"]


def test_off_and_unknown_modes_do_nothing(monkeypatch):
    monkeypatch.setattr(r2, "select", lambda *a: (_ for _ in ()).throw(
        AssertionError("selected while off")))
    assert r2.run(_Conn(), "off")["skipped"] == "off"
    monkeypatch.setenv("R2_RETENTION_MODE", "delete-everything")
    assert r2.mode() == "off"
    monkeypatch.setenv("R2_RETENTION_MODE", "LIVE")
    assert r2.mode() == "live"
