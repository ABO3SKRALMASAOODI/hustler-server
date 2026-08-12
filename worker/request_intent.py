"""Pass the latest request to the editor without regex-derived permissions.

The model receives the complete conversation and current project state.  A
hand-maintained phrase matcher cannot reliably decide whether a multilingual,
contextual editing request authorizes a tool.  This module therefore supplies
only the ordering rule the loop needs and leaves interpretation to the agent.
"""


def request_contract(_text):
    """Stable system anchor beside the latest user message."""
    return (
        "CURRENT REQUEST CONTRACT — the final user message has highest "
        "priority. Earlier messages supply missing context only; anything "
        "the latest message removes, reverses or narrows is superseded. "
        "Interpret the request from the full conversation and use any "
        "available editing tool needed to complete it; no keyword or regex "
        "grants or withholds tool permission."
    )
