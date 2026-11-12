"""Department ``direction``: measured activity over the main video (P0 #60).

  find_activity   where the picture moves and sharp sounds hit, over every
                  frame (activity.py), as active / idle stretches and a keep
                  list: "cut the space between serves" on a 45-minute match
                  cuts the dead time instead of asking for timestamps.

Model-free signal processing; the implementation lives in agent_tools
(find_activity), which shares the proxy, heartbeat and storage helpers.
"""

DEPARTMENT = "direction"


def find_activity(ctx, **kwargs):
    import agent_tools
    return agent_tools.find_activity(ctx, **kwargs)


TOOL_SPECS = {
    "find_activity": (
        find_activity,
        "READ: MEASURED activity of the main video: where the picture moves "
        "and sharp sounds hit, over every frame at 4 samples a second (not "
        "sampled stills), as active and idle SOURCE stretches with a ready "
        "keep list. Use it on long footage when the user asks to cut dead "
        "time, the space between plays, serves, points, reps or takes, or "
        "anything where nothing is happening (sports, games, workouts, "
        "wordless footage). Verify a few stretch starts with ONE batched "
        "look_at, then keep_segments. Never ask the user for timestamps "
        "this tool can measure. sensitivity 0-1 (0.5 = this take's own "
        "split; higher finds quieter activity); min_idle_s joins activity "
        "across shorter lulls.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "min_active_s": {"type": "number"},
         "min_idle_s": {"type": "number"},
         "sensitivity": {"type": "number", "minimum": 0, "maximum": 1},
         "pad_before_s": {"type": "number"},
         "pad_after_s": {"type": "number"}}),
}

COMPACT = {
    "find_activity": (
        "Measured motion + sound-hit activity over every frame: active/idle "
        "SOURCE stretches and a keep list, to cut dead time between plays."),
}
