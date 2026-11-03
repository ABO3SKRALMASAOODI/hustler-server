"""Fence queued operator recovery against later customer work."""


def _current(conn, project_id, session_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT
          (SELECT version FROM edls WHERE project_id=%s ORDER BY version DESC LIMIT 1) AS version,
          (SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE session_id=%s AND role='user') AS message_id""",
                    (project_id, session_id))
        return cur.fetchone() or {}


def superseded(ctx):
    payload = (getattr(ctx, "job", None) or {}).get("payload") or {}
    if payload.get("operator_repair") is not True or "operator_expected_edl_version" not in payload:
        return None
    current = ctx.db.run(_current, ctx.project_id, ctx.session_id)
    expected_version = payload["operator_expected_edl_version"]
    expected_message = payload.get("operator_expected_message_id")
    continuation = payload.get("continuation_state") or {}
    if continuation.get("durable_slice"):
        written = continuation.get("versions_written") or []
        expected_version = written[-1] if written else expected_version
        expected_message = continuation.get("seen_message_id", expected_message)
    # The same job can be retried after its write committed but before its
    # response/checkpoint did. Its transactional receipt distinguishes that
    # progress from another writer's edit; never treat our own work as a steer.
    receipt = payload.get("mutation_receipt") or {}
    if receipt.get("committed") is True and receipt.get("job_id") == ctx.job.get("id"):
        expected_version = receipt.get("after_version", expected_version)
    if (current.get("version") != expected_version
            or current.get("message_id") != expected_message):
        return {"status": "superseded", "outcome": "superseded", "billable": False,
                "reason": "Customer project changed after the recovery was queued; no recovery edit was applied."}
    return None
