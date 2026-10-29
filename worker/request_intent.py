"""Pass the latest request to the editor without regex-derived permissions.

The model receives the complete conversation and current project state.  A
hand-maintained phrase matcher cannot reliably decide whether a multilingual,
contextual editing request authorizes a tool.  This module therefore supplies
only the ordering rule the loop needs and leaves interpretation to the agent.
"""

REVIEW_REPAIR_PREFIX = "Fix the remaining quality issues before export:"
# The Studio's "Improve it" button on a delivered version with review notes.
# Like Repair edit, its text is the reviewers' notes, not the customer's
# brief: the brief being improved stays the verification request (1769 pressed
# Repair edit five times and each pass was judged against critic prose).
IMPROVE_IT_PREFIX = "Improve it: keep what works and fix these review notes:"
REVIEW_REPAIR_PREFIXES = (REVIEW_REPAIR_PREFIX, IMPROVE_IT_PREFIX)


def is_review_repair(text):
    """Identify the literal request emitted by Studio's Repair edit or
    Improve it button.

    This identifies product-generated review prose; it does not decide which
    tools a customer's natural-language request permits.
    """
    return str(text or "").strip().startswith(REVIEW_REPAIR_PREFIXES)


def request_contract(_text):
    """Stable system anchor beside the latest user message."""
    return (
        "CURRENT REQUEST CONTRACT — the final user message has highest "
        "priority. Earlier messages supply missing context only; anything "
        "the latest message removes, reverses or narrows is superseded. "
        "Interpret the request from the full conversation and use any "
        "available editing tool needed to complete it; no keyword or regex "
        "grants or withholds tool permission. A concrete make/edit brief "
        "is permission to write the EDL this turn — do not stop at a "
        "proposed plan and wait for approval."
    )
