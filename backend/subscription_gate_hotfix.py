"""Runtime bridge for the Studio subscription-gate rollout.

Two surfaces must agree:

* an empty project is concierge chat, so an unsubscribed user can say "hi" and
  receive the instruction to upload footage;
* once the account qualifies for the conversion wall, `/auth/credits` must
  expose the same server-priced offer used by the chat 402 so the Studio can
  show cards immediately after a successful upload.

The installer is deliberately small and idempotent so it is safe under Flask's
application factory and gunicorn worker imports.
"""

from flask import request

_SAVEPOINT = "preupload_subscription_gate"


def _project_has_original(cur, project_id):
    """Return whether the project has source media, failing open on DB skew."""
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


def _install_preupload_gate(video_routes):
    current_gate = video_routes._subscribe_gate_applies
    if getattr(current_gate, "_preupload_aware", False):
        return current_gate

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
    video_routes._trial_gate_applies = preupload_aware_gate
    return preupload_aware_gate


def _install_credit_offer(auth_routes, video_routes):
    current_balance = auth_routes.get_balance
    if getattr(current_balance, "_includes_subscribe_offer", False):
        return

    def balance_with_subscribe_offer(conn, user_id):
        info = current_balance(conn, user_id)
        if not isinstance(info, dict):
            return info

        gated = False
        try:
            with conn.cursor() as cur:
                gated = bool(
                    video_routes._subscribe_gate_applies(cur, int(user_id))
                )
        except Exception as exc:  # pragma: no cover - status must fail open
            print(
                f"[subscribe_gate] credits status failed for user "
                f"{user_id}: {exc}",
                flush=True,
            )

        result = dict(info)
        result["subscribe_gated"] = gated
        result["subscribe_offer"] = (
            video_routes._subscribe_offer_body() if gated else None
        )
        return result

    balance_with_subscribe_offer._includes_subscribe_offer = True
    balance_with_subscribe_offer.__name__ = current_balance.__name__
    balance_with_subscribe_offer.__doc__ = current_balance.__doc__
    auth_routes.get_balance = balance_with_subscribe_offer


def install_subscription_gate_hotfix(video_routes=None, auth_routes=None):
    """Install the pre-upload exception and the upload-card status contract."""
    if video_routes is None:
        from routes import video as video_routes
    if auth_routes is None:
        from routes import auth as auth_routes

    _install_preupload_gate(video_routes)
    _install_credit_offer(auth_routes, video_routes)
