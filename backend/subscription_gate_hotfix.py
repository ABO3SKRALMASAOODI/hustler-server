"""Pre-upload exception for the Studio subscription gate.

Unsubscribed accounts must be able to talk to the lightweight concierge while a
project is empty. The conversion wall begins once the project has source media;
all other subscription-gated surfaces keep their existing behavior.

This installer is deliberately small and idempotent so it is safe under Flask's
application factory and gunicorn worker imports.
"""

from flask import request

_SAVEPOINT = "preupload_subscription_gate"


def _project_has_original(cur, project_id):
    """Return True/False for source media, or False on lookup failure.

    The gate follows the existing fail-open policy: a transient lookup problem
    must not swallow a user's message. A savepoint keeps a failed observability
    query from poisoning the surrounding message transaction.
    """
    savepoint = False
    try:
        cur.execute(f"SAVEPOINT {_SAVEPOINT}")
        savepoint = True
        cur.execute(
            """SELECT EXISTS (
                   SELECT 1 FROM assets
                   WHERE project_id = %s AND kind = 'original'
               ) AS has_original""",
            (int(project_id),),
        )
        row = cur.fetchone() or {}
        cur.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
        return bool(row.get("has_original"))
    except Exception as exc:  # pragma: no cover - production fail-open guard
        if savepoint:
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
                cur.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
            except Exception:
                pass
        print(
            f"[subscribe_gate] source lookup failed for project "
            f"{project_id}: {exc}",
            flush=True,
        )
        return False


def install_subscription_gate_hotfix(video_routes=None):
    """Make the existing gate aware of the empty-project concierge stage."""
    if video_routes is None:
        from routes import video as video_routes

    current_gate = video_routes._subscribe_gate_applies
    if getattr(current_gate, "_preupload_aware", False):
        return

    def preupload_aware_gate(cur, user_id):
        try:
            is_message_send = request.endpoint == "video.post_message"
            project_id = (request.view_args or {}).get("project_id")
        except Exception:
            is_message_send = False
            project_id = None

        if (
            is_message_send
            and project_id is not None
            and not _project_has_original(cur, project_id)
        ):
            return False
        return current_gate(cur, user_id)

    preupload_aware_gate._preupload_aware = True
    preupload_aware_gate.__name__ = current_gate.__name__
    preupload_aware_gate.__doc__ = current_gate.__doc__

    video_routes._subscribe_gate_applies = preupload_aware_gate
    # Preserve the compatibility alias used by older tests and call sites.
    video_routes._trial_gate_applies = preupload_aware_gate
