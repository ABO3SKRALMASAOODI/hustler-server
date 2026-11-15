#!/usr/bin/env python3
"""Run workflow 4 (long-form voiceover stories) on the workflow shell.

    PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
    $PY run.py [--topic "a subject"] [--out <deliver root>]
    $PY run.py run --resume <run_id>
    $PY run.py status

The shell (../_shell) owns the stages, the ledger of styles, the brand finish, delivering once and deleting the work
folder. This file only tells it where workflow 4 lives.
"""
import os
import sys
import json
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(os.path.dirname(HERE), "_shell")
sys.path.insert(0, SHELL)
sys.path.insert(0, HERE)
import shell  # noqa: E402

_load = shell.load_workflow


def state_root(argv):
    base=Path('/Users/masaoodi/Documents/Valmera/.valmera')
    legacy=base/'week-1112/lanes/WF4-LONGFORM/state'
    if '--resume' in argv:
        run_id=argv[argv.index('--resume')+1]
        if (legacy/'work'/run_id/'run.json').is_file():return str(legacy)
    return str(base/'workflow/longform')


def load(name):
    return _load(os.path.join(HERE, "wf4.py") if name == "wf4" else name)


def main():
    # One command in either host; a topic is optional. Existing shell arguments
    # and resumable run IDs remain supported.
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("run", "status"):
        argv.insert(0, "run")
    topic = None
    if "--topic" in argv:
        i = argv.index("--topic")
        topic = argv.pop(i + 1)
        argv.pop(i)
    if argv[0] == "run":
        i = argv.index("--args") if "--args" in argv else None
        args = json.loads(argv[i + 1]) if i is not None else {}
        args.setdefault("look", False)
        args.setdefault("delivered_ledger", "off")
        if topic:
            args["line"] = topic
        if i is None:
            argv += ["--args", json.dumps(args)]
        else:
            argv[i + 1] = json.dumps(args)
        if "--out" not in argv and "--resume" not in argv:
            argv += ["--out", "/Users/masaoodi/Documents/Valmera/.valmera/week-1112/lanes/WF4-LONGFORM"]
    if "--state-root" not in argv:
        argv += ["--state-root", state_root(argv)]
    env_file = Path("/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env")
    if not os.environ.get("OPENAI_API_KEY") and env_file.exists():
        key = next((line.partition("=")[2].strip().strip("\"'")
                    for line in env_file.read_text().splitlines()
                    if line.startswith("OPENAI_API_KEY=")), None)
        if key:
            os.environ["OPENAI_API_KEY"] = key
    os.environ["WRITER_PROVIDER"] = "openai"
    os.environ["NAMED_PICTURE_MODEL"]="gpt-6-sol"
    os.environ["NAMED_CHECK_MODEL"]="gpt-6-sol"
    os.environ["FOOTAGE_MODEL"]="gpt-6-sol"
    os.environ.setdefault("VALMERA_WORKER", __import__("brand").worker_dir())
    sys.argv = [sys.argv[0]] + argv
    shell.load_workflow = load
    if "--workflow" not in sys.argv:
        sys.argv[2:2] = ["--workflow", "wf4"]
    return shell.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
