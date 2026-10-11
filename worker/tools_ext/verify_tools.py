"""Department ``verify``: track 1F's verbs (INTERFACES_W1.md §4).

Wave 1 interface pass skeleton. The verb NAMES below are pinned; track 1F
owns this file and fills ``TOOL_SPECS`` (a module-level dict literal:
skill_validator reads it statically), ``REQUIRED_ARGS``, ``WRITE_TOOLS``,
``COMPACT`` and ``DISABLED`` when its verbs land. While ``TOOL_SPECS`` is
empty the department is not installed: no tool, no domain enum growth, the
core compact catalog byte-identical.

Write verbs commit through ``tools_ext._write.commit`` (edit_batch.apply_batch),
so a verb and apply_edit_batch validate the same items identically.
1F also edits the shared skills and agent_prompt.py at wave close-out.
"""

DEPARTMENT = "verify"

# Pinned verb names (INTERFACES_W1.md §4). Renaming one is a round-lead
# change: 1F's planner, the skills and the refbench agent cases name them.
VERBS = ("suggest_devices", "set_intensity", "proof_device", "audit_motion")

TOOL_SPECS = {}
