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


RENAME_SWAP = 0x2         # macOS renamex_np(2)
RENAME_EXCHANGE = 0x2     # Linux renameat2(2)
AT_FDCWD = -100           # Linux: paths relative to the working directory
# renameat2's number where libc has no wrapper (glibc before 2.28).
RENAMEAT2_SYSCALL = {"x86_64": 316, "amd64": 316, "aarch64": 276, "arm64": 276}
SYSTEM = object()         # the exchange this system offers (exchange_call())


def exchange_call(system=None, machine=None, libc=None):
    """The call that exchanges two paths in one step on this system, as
    call(a: bytes, b: bytes) raising OSError, or None where there is none.
    macOS: renamex_np RENAME_SWAP. Linux: renameat2 RENAME_EXCHANGE from libc,
    else the renameat2 system call by its number for this machine."""
    system = sys.platform if system is None else system
    if system != "darwin" and not system.startswith("linux"):
        return None
    try:
        import ctypes
        if libc is None:
            libc = ctypes.CDLL(None, use_errno=True)
        if system == "darwin":
            fn = libc.renamex_np
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            fn.restype = ctypes.c_int

            def args(a, b):
                return a, b, RENAME_SWAP
        elif getattr(libc, "renameat2", None) is not None:
            fn = libc.renameat2
            fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            fn.restype = ctypes.c_int

            def args(a, b):
                return AT_FDCWD, a, AT_FDCWD, b, RENAME_EXCHANGE
        else:
            number = RENAMEAT2_SYSCALL.get(machine or os.uname().machine)
            if number is None or ctypes.sizeof(ctypes.c_void_p) != 8:
                return None
            fn = libc.syscall               # variadic: every integer goes as a long
            fn.restype = ctypes.c_long

            def args(a, b):
                return (ctypes.c_long(number), ctypes.c_long(AT_FDCWD), a,
                        ctypes.c_long(AT_FDCWD), b, ctypes.c_long(RENAME_EXCHANGE))
    except (AttributeError, OSError):
        return None

    def call(a: bytes, b: bytes) -> None:
        if fn(*args(a, b)) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), os.fsdecode(a), None, os.fsdecode(b))
    return call


def _swap_dirs(new: Path, dest: Path, *, exchange=SYSTEM) -> bool:
    """Exchange two directories in one step. False, with nothing moved, when
    this system or filesystem cannot (no call, or the call refused: EINVAL,
    ENOSYS, ENOTSUP...)."""
    call = exchange_call() if exchange is SYSTEM else exchange
    if call is None:
        return False
    try:
        call(os.fsencode(new), os.fsencode(dest))
    except OSError:
        return False                # the exchange is all or nothing
    return True


def exchange_aside(dest: Path) -> Path:
    """Where exchange_dirs() keeps `dest`'s folder while it renames in steps."""
    dest = Path(dest)
    return dest.with_name(f".{dest.name}.exchanging")


def settle_exchange(new: Path, dest: Path) -> None:
    """Finish an exchange_dirs() of `new` and `dest` that stopped between its
    renames, so `dest` holds the finished folder and `new` the one it
    replaced. When the finished folder cannot be moved to `dest` (the rename
    that stopped the exchange fails again), the original goes back to `dest`
    and the finished folder stays at `new`, as before the exchange began, so
    the caller redoes it. Nothing to do when no exchange was left part way."""
    aside = exchange_aside(dest)
    if not os.path.lexists(aside):
        return
    if not os.path.lexists(dest):
        if not os.path.lexists(new):
            os.rename(aside, dest)  # only the original remains: it stays in place
            return
        try:
            os.rename(new, dest)    # stopped after the first rename
        except OSError:
            os.rename(aside, dest)  # never leave no folder at dest
            return
    os.rename(aside, new)           # stopped after the second rename


def exchange_dirs(new: Path, dest: Path, *, exchange=SYSTEM) -> None:
    """Leave `new`'s folder at `dest` and `dest`'s folder at `new`. One atomic
    step where the system and filesystem offer it (_swap_dirs); elsewhere three
    renames through exchange_aside(dest), which settle_exchange() completes
    when a process stops between them."""
    new, dest = Path(new), Path(dest)
    settle_exchange(new, dest)
    if _swap_dirs(new, dest, exchange=exchange):
        return
    aside = exchange_aside(dest)
    os.rename(dest, aside)
    try:
        os.rename(new, dest)
    except OSError:
        os.rename(aside, dest)      # never leave no folder at dest
        raise
    os.rename(aside, new)
