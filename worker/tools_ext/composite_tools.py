"""Department ``compositing``: track 1C's verbs (INTERFACES_W1.md §4).

Wave 1 interface pass skeleton. The verb NAMES below are pinned; track 1C
owns this file and fills ``TOOL_SPECS`` (a module-level dict literal:
skill_validator reads it statically), ``REQUIRED_ARGS``, ``WRITE_TOOLS``,
``COMPACT`` and ``DISABLED`` when its verbs land. While ``TOOL_SPECS`` is
empty the department is not installed: no tool, no domain enum growth, the
core compact catalog byte-identical.

Write verbs commit through ``tools_ext._write.commit`` (edit_batch.apply_batch),
so a verb and apply_edit_batch validate the same items identically.
Its skill is ``skills/compositing.md``; it documents every verb AND the exact
apply_edit_batch operations that write and remove the same items (the
stale-client path).
"""

DEPARTMENT = "compositing"

# Pinned verb names (INTERFACES_W1.md §4). Renaming one is a round-lead
# change: 1F's planner, the skills and the refbench agent cases name them.
VERBS = ("set_composite", "add_type_window", "add_region_effect",
         "remove_region_effect")

TOOL_SPECS = {}
