"""A staged copy of the edit that today's verbs write into, in memory.

The ``_RecipeContext`` pattern (agent_tools): every verb reads the staged EDL
through ``latest_edl`` and writes through ``write_edl``, which here only
validates (with the repair pass) and stages. None of ``ToolContext.write_edl``'s
guard chain runs; the one commit at the end writes the version
(harness2.commit). The staged version number moves with each staged write, so
callers that compare versions (``place_scenes``) see a write happen.
"""
import json

import agent_tools
from agent_tools import edl_signature

from harness2 import repair as repair_mod

REJECTED_PREFIX = "REJECTED"


class Stage(agent_tools._RecipeContext):
    def __init__(self, base, edl, version):
        super().__init__(base, edl, version)
        object.__setattr__(self, "_base_version", int(version))
        object.__setattr__(self, "_writes", 0)
        object.__setattr__(self, "_repairs", [])
        object.__setattr__(self, "_descs", [])
        # v2 places what it was asked to place; the pixels come back to the
        # model in the envelope, never as a hold before dispatch.
        object.__setattr__(self, "_pending_visual_review_assets", set())
        object.__setattr__(self, "harness", "v2")

    # The version the verbs see: the head plus the staged writes.
    def latest_edl(self):
        return {"version": self._base_version + self._writes,
                "json": self._edl}

    @property
    def edl(self):
        return self._edl

    def replace(self, edl):
        """Stage an EDL directly (the core ops build it themselves)."""
        return self.write_edl(edl, "staged")

    def write_edl(self, edl, desc):
        normalized, notes, error = repair_mod.repair(
            edl, self.duration, before=self._edl)
        if normalized is None:
            return f"{REJECTED_PREFIX} while staging: {error}"
        self._repairs.extend(notes)
        if edl_signature(normalized) == edl_signature(self._edl):
            return "NO CHANGE while staging"
        object.__setattr__(self, "_edl", normalized)
        object.__setattr__(self, "_writes", self._writes + 1)
        self._descs.append(str(desc or "")[:200])
        return (f"EDL v{self._base_version + self._writes - 1} -> "
                f"v{self._base_version + self._writes} (staged): {desc}.")

    def take_repairs(self):
        out = list(self._repairs)
        del self._repairs[:]
        return out

    def snapshot(self):
        return json.loads(json.dumps(self._edl))

    def restore(self, edl):
        object.__setattr__(self, "_edl", json.loads(json.dumps(edl)))


def result_refused(result):
    """True when a verb's answer says it changed nothing for a reason."""
    text = str(result or "").lstrip()
    if text.upper().startswith((REJECTED_PREFIX, "RECIPE ABORTED")):
        return True
    try:
        return agent_tools.tool_result_kind(text) in (
            "refused", "failed", "prerequisite")
    except Exception:  # noqa: BLE001
        return False


def first_line(text, limit=400):
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""
