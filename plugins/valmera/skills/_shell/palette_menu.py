"""The palette's menu (worker/scene/palette), for a prompt that chooses devices per moment, and the asks a workflow
hands the palette for display rows that perform.

The cards come from the engine's own registry (``python -m scene.palette.run menu``), so a device a lane adds is on
the menu without editing a list here. Read once a process; an engine without the palette gives an empty menu, and
the rows are then set as plain display words as before.
"""
import os
import subprocess

import brand

TYPE_FAMILIES = ("type", "behind", "hud")
# devices that draw the row's words themselves: the plain display line is not set and the caption omits the words
REPLACES = ("counter", "staircase", "name", "ladder", "brackets", "typewriter", "redact", "type_run", "verb", "behind", "marquee")
# devices that act on the row as set (a marker behind it, a flip on its hit) or beside it (a box on the face, fine print)
DECORATES = ("highlight", "flip", "track", "fineprint", "donut", "bar", "readalong", "chapter")
_MENU = {}


def menu(families=TYPE_FAMILIES, py=None):
    key = tuple(families)
    if key in _MENU:
        return _MENU[key]
    wk = brand.worker_dir()
    text = ""
    if os.path.isdir(os.path.join(wk, "scene", "palette")):
        env = dict(os.environ, PYTHONPATH=os.path.dirname(wk))
        try:
            r = subprocess.run([py or os.environ.get("SHELL_PY") or "python3", "-m", "scene.palette.run", "menu", "--family", ",".join(families)],
                               cwd=wk, env=env, capture_output=True, text=True, timeout=120)
            text = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            text = ""
    _MENU[key] = text
    return text


def names(text):
    """The device names on a menu (the first word of each card)."""
    return {ln.split(" ", 1)[0] for ln in (text or "").splitlines() if ln.strip()}


def ask_for(row, seg, text, a, b, n):
    """The palette ask for a display row a model gave a ``perform`` device: the device's own fields as the model wrote
    them, anchored on the row's words (sentence and frame) unless the model named its own anchor."""
    perf = dict(row.get("perform") or {})
    dev = str(perf.pop("dev", "") or "")
    args = {k: v for k, v in perf.items() if k not in ("at", "until", "lands")}
    args.setdefault("t", text)
    ask = {"dev": dev, "id": f"dv{n:02d}", "beat": row.get("role"), "args": args,
           "at": perf.get("at") if isinstance(perf.get("at"), dict) else {"seg": seg, "word": text, "f": a}}
    if isinstance(perf.get("until"), dict):
        ask["until"] = perf["until"]
    if isinstance(perf.get("lands"), dict):
        ask["lands"] = perf["lands"]
    if dev in DECORATES and dev not in ("chapter", "readalong", "track", "fineprint"):
        ask["until"] = b
    return ask
