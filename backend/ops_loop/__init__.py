"""The outcome loop (owner design, 25 Oct 2026).

"The agent that traces everything should be Claude Opus": the JUDGING and the
FIXING are done by Claude Code sessions on the owner's Mac, on the owner's
subscription, never by the product's API keys and never by a weaker model.
The backend only provides the data, the evidence storage, the tickets and the
views. Nothing in this package calls a language model.

  sessions  finds every real user's SETTLED edit session (a Studio request,
            a Make-shorts run, a real customer's MCP session) and builds its
            full context: the request and follow-ups, the agent's replies,
            the tool-call timeline with errors, the jobs, the source and the
            delivered renders, and what the user did next.
  store     the only writes: session_reviews, tickets, ticket_reviews and
            ops_review_keys (migration 041). Used by scripts/ops_review.py.
  views     the admin read models (the SIGNUPS strip, the outcome rate, the
            Sessions and Tickets tabs) and the digest's outcome section.

Runbooks: /Users/masaoodi/Documents/Valmera/.valmera/ops-loop/REVIEWER.md and
FIXER.md. The CLI: backend/scripts/ops_review.py.
"""
from admin_metrics.cockpit import INTERNAL_IDS as _COCKPIT_INTERNAL

# The owner's list (25 Oct) plus the cockpit's own internal accounts, so the
# outcome rate and the Health board never disagree about who is a customer.
OWNER_LISTED_INTERNAL = (22, 28, 29, 45, 46, 49, 60, 61, 62, 68, 108, 398,
                         1058)
EXCLUDED_USER_IDS = tuple(sorted(set(OWNER_LISTED_INTERNAL)
                                 | set(_COCKPIT_INTERNAL)))

VERDICTS = ("delivered_good", "delivered_poor", "not_delivered")
SURFACES = ("studio", "shorts", "mcp")
SEVERITIES = ("P0", "P1", "P2")
TICKET_STATUSES = ("open", "fixing", "fixed", "wont_fix")
EVIDENCE_KINDS = ("contact_sheet", "frame", "chat", "error", "render",
                  "source", "other")
EVIDENCE_PREFIX = "ops/evidence/"
