"""Structured internal tool results with backwards-compatible text rendering."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


STATUSES = {
    "success", "correction_needed", "prerequisite", "transient_failure",
    "unavailable", "unsafe",
}


@dataclass
class ToolOutcome:
    status: str
    message: str
    state_changed: bool = False
    retryable: bool = False
    idempotent: bool = False
    corrected_argument_guidance: Optional[str] = None
    prerequisite_tool: Optional[str] = None
    safe_fallback: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    affected_ranges: List[List[float]] = field(default_factory=list)

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown ToolOutcome status {self.status!r}")

    def to_dict(self):
        return asdict(self)

    def render_text(self):
        # Preserve existing successful response bodies and MCP envelopes. For
        # non-success statuses, the stable prefix is both human-readable and
        # machine-parsable by the in-house loop.
        if self.status == "success":
            return self.message
        # Existing MCP clients and recipe logic key off these historical
        # prefixes. Structure is carried internally; compatibility text stays
        # byte-shaped until the public protocol is versioned.
        if self.message.strip().upper().startswith(
                ("REJECTED", "RECIPE ABORTED", "PREREQUISITE",
                 "TRANSIENT_FAILURE", "UNAVAILABLE", "UNSAFE",
                 "CORRECTION_NEEDED", "CORRECTION NEEDED")):
            return self.message
        prefix = self.status.upper()
        text = self.message
        if not text.upper().startswith(prefix):
            text = f"{prefix}: {text}"
        details = []
        if self.corrected_argument_guidance:
            details.append("Correction: " + self.corrected_argument_guidance)
        if self.prerequisite_tool:
            details.append("Prerequisite tool: " + self.prerequisite_tool)
        if self.safe_fallback:
            details.append("Safe fallback: " + self.safe_fallback)
        if details:
            text += "\n" + "\n".join(details)
        return text


def from_legacy(result, *, state_changed=False, idempotent=False,
                affected_ranges=None):
    text = str(result if result is not None else "")
    upper = text.strip().upper()
    status = "success"
    retryable = False
    guidance = None
    prerequisite = None
    fallback = None
    if upper.startswith(("CORRECTION_NEEDED", "CORRECTION NEEDED", "REJECTED",
                         "RECIPE ABORTED")):
        status = "correction_needed"
        guidance = text.splitlines()[0][:500]
    elif upper.startswith("PREREQUISITE"):
        status = "prerequisite"
        retryable = True
    elif upper.startswith(("TRANSIENT_FAILURE", "TOOL ", "FAILED", "COULD NOT")):
        status = "transient_failure"
        retryable = True
    elif upper.startswith(("UNAVAILABLE", "UNKNOWN TOOL")):
        status = "unavailable"
        fallback = "load_tools/list the current capability directory"
    elif upper.startswith("UNSAFE"):
        status = "unsafe"
    if status == "prerequisite":
        # Common repair paths remain explicit while arbitrary prerequisite
        # prose is preserved in message.
        low = text.lower()
        for name in ("render_preview", "open_visual_page", "look_at",
                     "look_at_asset", "list_assets", "get_edl"):
            if name in low:
                prerequisite = name
                break
    return ToolOutcome(
        status=status, message=text, state_changed=bool(state_changed),
        retryable=retryable, idempotent=bool(idempotent),
        corrected_argument_guidance=guidance,
        prerequisite_tool=prerequisite, safe_fallback=fallback,
        affected_ranges=list(affected_ranges or []))
