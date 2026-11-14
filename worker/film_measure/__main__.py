"""Bounded subprocess entry point; all output is local measurement evidence."""
import json
import sys
from .delta import measure, probe
info = probe(sys.argv[1])
total = info["video_duration"] or info["duration"]
program = float(sys.argv[3])
if total < program - .25:
    raise ValueError("comparison preview is shorter than its programme")
result = measure(sys.argv[1], [sys.argv[2]], max(0.0, total - program))
with open(sys.argv[4], "w") as handle:
    json.dump(result, handle, default=float)
