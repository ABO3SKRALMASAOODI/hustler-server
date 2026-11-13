#!/usr/bin/env python3
"""Run the footage cut of a podcast moment (workflow 1) on the workflow shell. See wf1_cut.py.

    PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
    $PY run_cut.py run --source <podcast file> --out <deliver root> --args '{"again": "<delivered wf1 folder>"}'
    $PY run_cut.py run --resume <run_id>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(os.path.dirname(os.path.dirname(HERE)), "_shell")
sys.path.insert(0, SHELL)
import shell  # noqa: E402

_load = shell.load_workflow


def load(name):
    return _load(os.path.join(HERE, "wf1_cut.py") if name == "wf1" else name)


def main():
    shell.load_workflow = load
    if "--workflow" not in sys.argv:
        sys.argv[2:2] = ["--workflow", "wf1"]
    return shell.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
