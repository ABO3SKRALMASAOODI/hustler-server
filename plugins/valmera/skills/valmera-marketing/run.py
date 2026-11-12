#!/usr/bin/env python3
"""Run workflow 2 on the workflow shell.

    PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
    zsh /Users/masaoodi/Documents/Valmera/.valmera/claude_trial_env.sh $PY run.py run --out <deliver root> \
        [--args '{"style": "reading-wipe", "direction": {"hero": "s042"}, "look": false}']
    $PY run.py run --resume <run_id>
    $PY run.py status

The shell (../_shell) owns the stages, the ledger of styles, the brand finish, delivering once and
deleting the work folder. This file only tells it where workflow 2 lives.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(os.path.dirname(HERE), "_shell")
sys.path.insert(0, SHELL)
sys.path.insert(0, HERE)
import shell  # noqa: E402

_load = shell.load_workflow


def load(name):
    return _load(os.path.join(HERE, "wf2.py") if name == "wf2" else name)


def main():
    shell.load_workflow = load
    if "--workflow" not in sys.argv:
        sys.argv[2:2] = ["--workflow", "wf2"]
    return shell.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
