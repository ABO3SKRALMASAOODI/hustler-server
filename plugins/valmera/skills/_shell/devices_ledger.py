"""Measure which devices each delivered film laid, in programme order.

    /Users/masaoodi/Documents/Valmera/.valmera/workflow/devices.jsonl      (VALMERA_DEVICES_LEDGER overrides it)

One JSON object per line, appended under a file lock: {wf, at, run_id, folder, by, seq: ["dev@role", ...],
counts: {dev: n}, surfaces: [...]}. It records variety only; it never refuses anything.
"""
import collections
import contextlib
import datetime as dt
import fcntl
import json
import os
import tempfile

DEFAULT = "/Users/masaoodi/Documents/Valmera/.valmera/workflow/devices.jsonl"


def path():
    p = os.environ.get("VALMERA_DEVICES_LEDGER")
    if not p and os.environ.get("PYTEST_CURRENT_TEST"):        # a test never reads or writes the owner's ledger
        return os.path.join(tempfile.gettempdir(), f"valmera-devices-test-{os.getpid()}.jsonl")
    return p or DEFAULT


@contextlib.contextmanager
def _locked(p):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def rows(p=None):
    p = p or path()
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def sequence(log, director=None):
    """["dev@role", ...] in film order from the palette's log (what landed) and the Director's beats (their roles)."""
    beats = (director or {}).get("beats") or []
    seq = []
    for r in sorted((r for r in log or [] if r.get("done")), key=lambda r: (r.get("f") or [0])[0]):
        role = None
        b = r.get("beat")
        if isinstance(b, int) and 0 <= b < len(beats):
            role = beats[b].get("role")
        dev = r.get("landed") or r.get("dev")
        seq.append(f"{dev}@{role}" if role else str(dev))
    return seq


def row_of(wf, run_id, folder, log, director=None):
    seq = sequence(log, director)
    counts = collections.Counter(s.split("@")[0] for s in seq)
    surfaces = [s.get("surface") for s in (director or {}).get("sections") or [] if s.get("surface") not in (None, "colour")]
    return dict(wf=wf, at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), run_id=run_id, folder=folder,
                by=(director or {}).get("by"), seq=seq, counts=dict(counts), surfaces=surfaces)


def append(row, p=None):
    p = p or path()
    with _locked(p):
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def record(wf, run_id, folder, supply):
    """At delivery: the film's landed devices (supply/devices_log.json) and its Director's beats (supply/director.json)."""
    def load(name):
        try:
            return json.load(open(os.path.join(supply, name)))
        except (OSError, ValueError):
            return None
    log = load("devices_log.json") or []
    director = load("director.json") or {}
    if not log and not director:
        return None
    return append(row_of(wf, run_id, folder, log, director))


def recent(n=12, p=None):
    """The last n films' device sequences as one line each, for the Director's prompt."""
    out = []
    for r in rows(p)[-n:]:
        seq = ", ".join(r.get("seq") or [])
        surf = ("; surfaces " + ", ".join(r.get("surfaces") or [])) if r.get("surfaces") else ""
        out.append(f"{r.get('wf')} {str(r.get('at'))[:10]}: {seq}{surf}")
    return out
