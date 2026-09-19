"""Durable acknowledgements for direct timeline commands.

Receipts live with the existing per-project edit audit, in the same database
transaction as the new version. A lost HTTP response can safely be retried
without applying a delta twice. No process-local cache is authoritative.
"""
import hashlib
import json
import re


def command_identity(data):
    operation_id = data.get("operation_id")
    if operation_id is None:
        return None, None
    if not isinstance(operation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{16,80}", operation_id):
        raise ValueError("Invalid edit operation ID.")
    payload = {k: data.get(k) for k in
               ("op", "args", "base_version", "defer_preview", "preview_mode")}
    fingerprint = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return operation_id, fingerprint


def read_receipt(cur, session_id, operation_id, fingerprint):
    if operation_id is None:
        return None
    cur.execute("""SELECT meta FROM chat_messages
                   WHERE session_id = %s AND role = 'activity'
                     AND meta->>'operation_id' = %s
                   ORDER BY id DESC LIMIT 1""", (session_id, operation_id))
    row = cur.fetchone()
    if not row:
        return None
    receipt = row["meta"]
    if receipt.get("operation_fingerprint") != fingerprint:
        raise ValueError("This edit ID was already used for a different change.")
    return receipt["acknowledgement"]


def receipt_meta(operation_id, fingerprint, acknowledgement):
    if operation_id is None:
        return {}
    return {"operation_id": operation_id,
            "operation_fingerprint": fingerprint,
            "acknowledgement": acknowledgement}
