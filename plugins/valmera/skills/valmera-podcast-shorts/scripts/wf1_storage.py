"""Atomic receipt writes and whole-folder exchange on the factory volume."""
import json
import os
from pathlib import Path
import sys
import tempfile


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: object) -> None:
    write_atomic(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _swap_dirs(new: Path, dest: Path) -> bool:
    """Exchange two directories in one step (macOS renamex_np RENAME_SWAP).
    False when the platform cannot."""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        return fn(os.fsencode(new), os.fsencode(dest), 0x2) == 0
    except (AttributeError, OSError):
        return False
