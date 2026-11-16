"""Fail closed before deleting workflow scratch, including nested lane run roots."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


def live_commands():
    lines = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True).splitlines()
    return [line.split(None, 1)[1] for line in lines
            if len(line.split(None, 1)) == 2 and int(line.split(None, 1)[0]) != os.getpid()]


def disposition(folder, commands, now=None, age_s=12 * 3600):
    folder = Path(folder).resolve()
    for command in commands:
        try:
            args = shlex.split(command)
        except ValueError:
            args = command.split()
        for arg in args:
            value = arg.split('=', 1)[1] if arg.startswith('--') and '=' in arg else arg
            if value.startswith('/'):
                try:
                    if Path(value).resolve().is_relative_to(folder):
                        return 'live-process'
                except OSError:
                    return 'process-path-unreadable'
    cutoff = (time.time() if now is None else now) - age_s
    def unreadable(error):
        raise error
    try:
        if not folder.is_dir():
            return 'not-a-directory'
        # A run writes beneath its root; that does not update the root's mtime.
        # Do not follow links into shared media or unrelated work.
        for base, directories, files in os.walk(folder, followlinks=False, onerror=unreadable):
            for path in [Path(base), *(Path(base) / name for name in directories + files)]:
                if path.lstat().st_mtime > cutoff:
                    return 'recent-nested-write'
    except OSError:
        return 'tree-changed-or-unreadable'
    return 'stale'


def main():
    try:
        reason = disposition(sys.argv[1], live_commands())
    except Exception as error:
        print(json.dumps(dict(path=sys.argv[1], kept=True, reason=type(error).__name__)))
        return 2
    print(json.dumps(dict(path=sys.argv[1], kept=reason != 'stale', reason=reason)))
    return 0 if reason == 'stale' else 1


if __name__ == '__main__':
    raise SystemExit(main())
