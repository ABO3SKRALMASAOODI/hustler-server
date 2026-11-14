"""Bounded subprocess entry point; all output is local measurement evidence."""
import json
import sys
from .delta import measure
result = measure(sys.argv[1], [sys.argv[2]], float(sys.argv[3]))
with open(sys.argv[4], "w") as handle:
    json.dump(result, handle, default=float)
