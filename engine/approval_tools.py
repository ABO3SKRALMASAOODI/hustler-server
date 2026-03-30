"""
approval_tools.py — Non-blocking backend & stripe approval flow

OLD FLOW (broken):
  1. Agent calls request_backend → BLOCKS for 300s polling files
  2. Agent calls request_stripe → BLOCKS again
  3. Total: 5+ minutes of blocking. Timeouts everywhere.

NEW FLOW:
  1. Agent calls request_backend + request_stripe IN PARALLEL → both return instantly
  2. Frontend shows BOTH approval prompts simultaneously
  3. User approves backend, fills stripe keys
  4. Agent calls check_approvals → polls until both resolved → registers tools
  5. Agent proceeds with migration + code

DROP-IN: Replace the old request_backend, request_stripe, and their tool definitions
in create_generator() with these.
"""

import os
import json
import time


def create_approval_tools(agent, workspace):
    """
    Create and return all approval-related tools and definitions.
    Call this from create_generator() to get the tool functions and schemas.
    
    Returns: (tool_map_additions, tool_definitions)
    """

    # ══════════════════════════════════════════════════════════════════
    #  request_backend — NON-BLOCKING, returns immediately
    # ══════════════════════════════════════════════════════════════════

    def request_backend(reason: str = "") -> str:
        if not workspace:
            return "BACKEND_ERROR: No workspace configured"

        meta_path = os.path.join(workspace, "meta.json")

        # Already enabled? Register tools and return immediately
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if meta.get("supabase_enabled"):
                    _register_backend(agent, meta, workspace)
                    return (
                        "BACKEND_ALREADY_ENABLED: Supabase is active. "
                        "Client is at src/integrations/supabase/client.ts. "
                        "Call migration to create tables. Do NOT use localStorage."
                    )
            except Exception:
                pass

        # Already requested? Don't double-write
        req_path = os.path.join(workspace, "backend_requested.json")
        if os.path.exists(req_path):
            return (
                "BACKEND_ALREADY_REQUESTED: Waiting for user approval. "
                "Call check_approvals to wait for the result."
            )

        # Write request file — frontend will detect this and show the approval card
        with open(req_path, "w") as f:
            json.dump({"reason": reason, "ts": time.time()}, f)

        print(f"[approvals] Backend requested — reason: {reason}")

        return (
            "BACKEND_REQUESTED: The user will see an approval prompt. "
            "Do NOT write any database or auth code yet. "
            "Call check_approvals to wait for backend + stripe to be ready, "
            "then proceed with migration."
        )

    # ══════════════════════════════════════════════════════════════════
    #  request_stripe — NON-BLOCKING, returns immediately
    # ══════════════════════════════════════════════════════════════════

    def request_stripe(reason: str = "") -> str:
        if not workspace:
            return "STRIPE_ERROR: No workspace configured"

        meta_path = os.path.join(workspace, "meta.json")

        # Already enabled?
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if meta.get("stripe_enabled"):
                    pk = meta.get("stripe_publishable_key", "")
                    job = os.path.basename(workspace)
                    return f"STRIPE_ALREADY_ENABLED: pk={pk}, proxy=https://entrepreneur-bot-backend.onrender.com/stripe/job/{job}"
            except Exception:
                pass

        # Already requested?
        req_path = os.path.join(workspace, "stripe_requested.json")
        if os.path.exists(req_path):
            return (
                "STRIPE_ALREADY_REQUESTED: Waiting for user to provide keys. "
                "Call check_approvals to wait for the result."
            )

        # Write request file
        with open(req_path, "w") as f:
            json.dump({"reason": reason, "ts": time.time()}, f)

        print(f"[approvals] Stripe requested — reason: {reason}")

        return (
            "STRIPE_REQUESTED: The user will see a form to enter Stripe keys. "
            "Do NOT write any payment code yet. "
            "Call check_approvals to wait for all pending approvals."
        )

    # ══════════════════════════════════════════════════════════════════
    #  check_approvals — BLOCKING, waits for ALL pending approvals
    # ══════════════════════════════════════════════════════════════════

    def check_approvals() -> str:
        """
        Wait for all pending approvals (backend + stripe) to resolve.
        Returns a summary of what was approved/denied.
        
        This is the ONLY blocking call. It waits up to 10 minutes,
        checking every 5 seconds. Both backend and stripe can resolve
        in parallel since the user sees both prompts simultaneously.
        """
        if not workspace:
            return "APPROVALS_ERROR: No workspace configured"

        meta_path = os.path.join(workspace, "meta.json")
        backend_req_path = os.path.join(workspace, "backend_requested.json")
        backend_approved_path = os.path.join(workspace, "backend_approved.json")
        backend_denied_path = os.path.join(workspace, "backend_denied.json")
        stripe_req_path = os.path.join(workspace, "stripe_requested.json")
        stripe_approved_path = os.path.join(workspace, "stripe_approved.json")
        stripe_denied_path = os.path.join(workspace, "stripe_denied.json")

        backend_pending = os.path.exists(backend_req_path)
        stripe_pending = os.path.exists(stripe_req_path)

        # Nothing pending? Check if already enabled
        if not backend_pending and not stripe_pending:
            return _build_status_summary(workspace, agent)

        max_wait = 600  # 10 minutes — generous for Supabase provisioning
        elapsed = 0
        backend_result = None  # "approved", "denied", or None
        stripe_result = None

        print(f"[approvals] Waiting for approvals (backend={backend_pending}, stripe={stripe_pending})")

        while elapsed < max_wait:
            time.sleep(5)
            elapsed += 5

            # ── Check backend ─────────────────────────────────────
            if backend_pending and backend_result is None:
                if os.path.exists(backend_approved_path):
                    _cleanup_file(backend_approved_path)
                    _cleanup_file(backend_req_path)

                    # Read credentials from meta.json
                    # (written by provisioning thread in supabase_routes)
                    meta = _read_meta(meta_path)
                    supabase_url = meta.get("supabase_url", "")
                    anon_key = meta.get("supabase_anon_key", "")

                    if supabase_url and anon_key:
                        _register_backend(agent, meta, workspace)
                        backend_result = "approved"
                        print(f"[approvals] Backend approved after {elapsed}s")
                    else:
                        # Approved but credentials not yet in meta.json
                        # (provisioning still running). Keep waiting.
                        print(f"[approvals] Backend approved but still provisioning ({elapsed}s)")
                        # Re-create the approval file so we detect it again
                        # Actually, just check meta.json directly
                        pass

                elif os.path.exists(backend_denied_path):
                    _cleanup_file(backend_denied_path)
                    _cleanup_file(backend_req_path)
                    backend_result = "denied"
                    print(f"[approvals] Backend denied after {elapsed}s")

                else:
                    # Check if provisioning completed directly (meta.json updated)
                    meta = _read_meta(meta_path)
                    if meta.get("supabase_enabled") and meta.get("supabase_url"):
                        _cleanup_file(backend_req_path)
                        _register_backend(agent, meta, workspace)
                        backend_result = "approved"
                        print(f"[approvals] Backend detected as enabled via meta.json after {elapsed}s")

            # ── Check stripe ──────────────────────────────────────
            if stripe_pending and stripe_result is None:
                if os.path.exists(stripe_approved_path):
                    _cleanup_file(stripe_approved_path)
                    _cleanup_file(stripe_req_path)
                    stripe_result = "approved"
                    print(f"[approvals] Stripe approved after {elapsed}s")

                elif os.path.exists(stripe_denied_path):
                    _cleanup_file(stripe_denied_path)
                    _cleanup_file(stripe_req_path)
                    stripe_result = "denied"
                    print(f"[approvals] Stripe denied after {elapsed}s")

                else:
                    # Check meta.json directly
                    meta = _read_meta(meta_path)
                    if meta.get("stripe_enabled"):
                        _cleanup_file(stripe_req_path)
                        stripe_result = "approved"
                        print(f"[approvals] Stripe detected as enabled via meta.json after {elapsed}s")

            # ── All resolved? ─────────────────────────────────────
            backend_done = (not backend_pending) or (backend_result is not None)
            stripe_done = (not stripe_pending) or (stripe_result is not None)

            if backend_done and stripe_done:
                break

            # Progress logging every 30s
            if elapsed % 30 == 0:
                print(f"[approvals] Still waiting ({elapsed}s) — backend={backend_result or 'pending'}, stripe={stripe_result or 'pending'}")

        # ── Build result message ──────────────────────────────────
        parts = []

        if backend_pending:
            if backend_result == "approved":
                meta = _read_meta(meta_path)
                parts.append(
                    f"BACKEND_APPROVED: Supabase is active!\n"
                    f"URL: {meta.get('supabase_url', '')}\n"
                    f"Anon Key: {meta.get('supabase_anon_key', '')}\n\n"
                    f"STOP — DISCARD any localStorage-based approach. "
                    f"Supabase is your database now.\n"
                    f"The client is ALREADY at src/integrations/supabase/client.ts — "
                    f"do NOT create your own.\n"
                    f"Types are at src/integrations/supabase/types.ts — "
                    f"auto-regenerated after each migration.\n\n"
                    f"Available tools: migration, insert_data, read_query, "
                    f"create_storage_bucket, configure_auth, get_project_info.\n\n"
                    f"NEXT: Call migration with ALL tables + RLS + triggers in one SQL block. "
                    f"Wait for result. THEN write frontend code."
                )
            elif backend_result == "denied":
                parts.append("BACKEND_DENIED: Build a frontend-only version using localStorage.")
            else:
                _cleanup_file(backend_req_path)
                parts.append("BACKEND_TIMEOUT: Build a frontend-only version using localStorage.")

        if stripe_pending:
            if stripe_result == "approved":
                meta = _read_meta(meta_path)
                pk = meta.get("stripe_publishable_key", "")
                job = os.path.basename(workspace)
                proxy = f"https://entrepreneur-bot-backend.onrender.com/stripe/job/{job}"
                parts.append(f"STRIPE_APPROVED: pk={pk}, proxy={proxy}")

                stripe_prompt = f"""

                ────────────────────────────────────────────────────────
                STRIPE PAYMENTS
                ────────────────────────────────────────────────────────
                INSTALL: run_install_command: "npm install @stripe/stripe-js @stripe/react-stripe-js -y"
                Publishable key (safe for frontend): {pk}
                Proxy URL: {proxy}

                CRITICAL: The preview runs inside an iframe. Use window.open(url, '_blank') — NEVER window.location.href.
                NEVER put sk_xxx in frontend code.
                Test card: 4242 4242 4242 4242, any future date, any CVC.
                """
                if "STRIPE PAYMENTS" not in agent.system_prompt:
                    agent.system_prompt += stripe_prompt
            elif stripe_result == "denied":
                parts.append("STRIPE_DENIED: Build a payment UI mockup with 'Coming soon' buttons.")
            else:
                _cleanup_file(stripe_req_path)
                parts.append("STRIPE_TIMEOUT: Build a payment UI mockup.")

        if not parts:
            parts.append("ALL_CLEAR: No pending approvals. Proceed with implementation.")

        return "\n\n".join(parts)

    # ── Return everything ─────────────────────────────────────────

    tool_map = {
        "request_backend": request_backend,
        "request_stripe": request_stripe,
        "check_approvals": check_approvals,
    }

    tool_definitions = [
        {
            "name": "request_backend",
            "description": (
                "Request a Supabase backend. Returns IMMEDIATELY — does not block.\n"
                "Call this EARLY, before writing any auth or database code.\n"
                "After calling this, call check_approvals to wait for the result.\n"
                "You CAN call request_backend and request_stripe in parallel."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"]
            }
        },
        {
            "name": "request_stripe",
            "description": (
                "Request Stripe payment integration. Returns IMMEDIATELY — does not block.\n"
                "Call this EARLY, before writing any payment code.\n"
                "After calling this, call check_approvals to wait for the result.\n"
                "You CAN call request_backend and request_stripe in parallel."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"]
            }
        },
        {
            "name": "check_approvals",
            "description": (
                "Wait for ALL pending approvals (backend + stripe) to resolve.\n"
                "Call this AFTER request_backend and/or request_stripe.\n"
                "This blocks until the user responds and provisioning completes.\n"
                "Returns the status of each: approved, denied, or timed out.\n"
                "If backend is approved, Supabase tools become available.\n\n"
                "IMPORTANT: Do NOT write any database, auth, or payment code "
                "until this returns with approved status."
            ),
            "input_schema": {"type": "object", "properties": {}}
        },
    ]

    return tool_map, tool_definitions


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _read_meta(meta_path):
    """Read meta.json safely."""
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path) as f:
            return json.load(f)
    except Exception:
        return {}


def _cleanup_file(path):
    """Remove a file if it exists."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _register_backend(agent, meta, workspace):
    """Register Supabase tools on the agent after backend approval."""
    try:
        from supabase_module import register_supabase_tools
        register_supabase_tools(agent, {
            "url": meta.get("supabase_url", ""),
            "anon_key": meta.get("supabase_anon_key", ""),
            "service_role_key": meta.get("supabase_service_role", ""),
            "project_ref": meta.get("supabase_project_ref", ""),
            "preview_url": f"https://entrepreneur-bot-backend.onrender.com/auth/preview-raw/{os.path.basename(workspace)}/",
        }, workspace)
        print(f"[approvals] Supabase tools registered")
    except Exception as e:
        print(f"[approvals] Warning: failed to register Supabase tools: {e}")


def _build_status_summary(workspace, agent):
    """Check current state when no pending requests exist."""
    meta_path = os.path.join(workspace, "meta.json")
    meta = _read_meta(meta_path)
    parts = []

    if meta.get("supabase_enabled"):
        _register_backend(agent, meta, workspace)
        parts.append(
            "BACKEND_ENABLED: Supabase is active. "
            "Client at src/integrations/supabase/client.ts. "
            "Call migration to create tables."
        )

    if meta.get("stripe_enabled"):
        pk = meta.get("stripe_publishable_key", "")
        job = os.path.basename(workspace)
        parts.append(f"STRIPE_ENABLED: pk={pk}, proxy=https://entrepreneur-bot-backend.onrender.com/stripe/job/{job}")

    if not parts:
        parts.append("NO_BACKEND: Neither backend nor stripe is enabled or requested.")

    return "\n\n".join(parts)