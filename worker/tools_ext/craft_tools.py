"""The craft tools (worker/customer_craft.py) as the direction department: plan_piece, direct, score, dress.

A request the ask ledger reads as an edited piece has its plan made and applied with these tools before
the editor's first step (agent_loop._plan_piece_first), which loads them by name for the rest of the
turn; any other turn loads them like every department tool."""
from customer_craft import COMPACT as _COMPACT, TOOLS as _TOOLS

DEPARTMENT = "direction"
TOOL_SPECS = {
    "plan_piece": _TOOLS["plan_piece"],
    "direct": _TOOLS["direct"],
    "score": _TOOLS["score"],
    "dress": _TOOLS["dress"],
}
WRITE_TOOLS = set(TOOL_SPECS)
COMPACT = {name: _COMPACT[name] for name in TOOL_SPECS}
