#!/usr/bin/env python3
"""Run workflow 3 (topic and founder stories) on the workflow shell.

    PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
    zsh /Users/masaoodi/Documents/Valmera/.valmera/claude_trial_env.sh $PY run.py run --out <deliver root> \
        [--args '{"style": "character-sheet", "line": "...", "look": false}']
    $PY run.py run --resume <run_id>
    $PY run.py status

The shell (../_shell) owns the stages, the ledger of styles, the brand finish, delivering once and deleting the work
folder. This file only tells it where workflow 3 lives.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))        # through the Claude skill link to the plugin itself
SHELL = os.path.join(os.path.dirname(HERE), "_shell")
sys.path.insert(0, SHELL)
sys.path.insert(0, HERE)
import shell  # noqa: E402

_load = shell.load_workflow


def load(name):
    return _load(os.path.join(HERE, "wf3.py") if name == "wf3" else name)


def main():
    shell.load_workflow = load
    if "--workflow" not in sys.argv:
        sys.argv[2:2] = ["--workflow", "wf3"]
    return shell.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
