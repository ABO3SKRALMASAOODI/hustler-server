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
# devices that draw the row's words themselves: the plain display line is not set and the caption omits the words they
# draw (a chapter page says its own title)
REPLACES = ("counter", "staircase", "name", "ladder", "brackets", "typewriter", "redact", "type_run", "verb", "behind", "marquee",
            "chapter")
# devices that act on the row as set (a marker behind it, a flip on its hit) or beside it (a box on the face, fine print,
# the caption line read along, an analogy's dial whose needle moves on the spoken change): they draw none of the row's
# words, so the row and its caption keep them
DECORATES = ("highlight", "flip", "track", "fineprint", "donut", "bar", "readalong")
# decorations that live as long as the row they mark; the others live to the end of the row's sentence
ON_THE_ROW = ("highlight", "flip", "donut", "bar")
# a figure device draws the whole row: the row is the figure and its unit, set by the device's own format
FIGURES = ("counter",)
# the fields in which a device is handed the words it draws
TEXT_FIELDS = ("t", "text", "word", "name", "title", "small", "label", "kicker")
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
    """The device names on a menu (the first word of each card, after its "[layer]" tag)."""
    out = set()
    for ln in (text or "").splitlines():
        words = ln.split()
        if words and words[0].startswith("[") and words[0].endswith("]"):
            words = words[1:]
        if words:
            out.add(words[0])
    return out


def ask_for(row, seg, text, a, b, n, limit=None):
    """The palette ask for a display row a model gave a ``perform`` device: the device's own fields as the model wrote
    them, anchored on the row's words (sentence and frame) unless the model named its own anchor.

    ``b`` is the row's window end (the film already clamps it before the next documentary subject); ``limit`` the end
    of the row's sentence, never past the next subject. A device that draws the row's words ends by ``b`` unless the
    model named its own end (then by ``limit``); a decoration ends with its row (``b``) or with its sentence
    (``limit``): the caption line read along is that one sentence, never the rest of the film. A decoration beside the
    row (not ON_THE_ROW) keeps the end the model named for it (an analogy dial's ``until``), bounded by ``limit``."""
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
    end = int(limit) if limit is not None else int(b)
    end = max(end, int(b))
    if dev in DECORATES:
        if dev in ON_THE_ROW or "until" not in ask:
            ask["until"] = int(b) if dev in ON_THE_ROW else end
            ask["bound"] = ask["until"]
        else:
            ask["bound"] = end
    else:
        ask["bound"] = int(b) if "until" not in ask else end
    return ask


def drawn(ask, text):
    """Which words of the row's ``text`` the device draws itself, one flag a word (text.split()). A figure device
    draws its whole row; another draws the words it was handed (its text, name, title, small line, label, kicker,
    lines and words). A word it does not draw stays in the caption."""
    toks = str(text or "").split()
    if ask.get("dev") in DECORATES:
        return [False] * len(toks)
    if ask.get("dev") in FIGURES:
        return [True] * len(toks)
    args = dict(ask.get("args") or {})
    got = []
    for k in TEXT_FIELDS:
        if isinstance(args.get(k), str):
            got.append(args[k])
    for k in ("lines", "words"):
        for x in args.get(k) or []:
            got.append(str(x.get("t") if isinstance(x, dict) else x))
    have = {_norm(w) for g in got for w in str(g).split() if _norm(w)}
    return [_norm(w) in have or not _norm(w) for w in toks]


def _norm(w):
    return "".join(ch for ch in str(w).lower() if ch.isalnum())


def omit_of(span, text, mask, norm=_norm):
    """The clock's word records of a row (``span``: said_span's records, in order) that a device draws, by ``mask``
    (drawn): each record is matched to the row's next word it spells; a filler between two drawn words goes with them."""
    toks = [norm(w) for w in str(text or "").split()]
    out, j, pend = [], 0, []
    for r in span:
        k = norm(r.get("w"))
        i = next((q for q in range(j, len(toks)) if toks[q] == k), None)
        if i is None:
            pend.append(r)
            continue
        if mask[i]:
            if out and j > 0 and mask[j - 1]:
                out += pend
            out.append(r)
        pend = []
        j = i + 1
    return out


def caption_handoff(items):
    """Give resolved display lettering its visible frames and return the captions on the remaining frames."""
    from scene.palette.kit import cut
    intervals = []
    for row in items:
        if row.get("k") != "word" or row.get("role") == "cap" or not row.get("t"):
            continue
        a, b = row["f"]
        opacity = row.get("op", 1)
        if isinstance(opacity, list):
            start = None
            for i, value in enumerate([*opacity, 0]):
                if value > 0 and start is None:
                    start = a+i
                elif value <= 0 and start is not None:
                    intervals.append((start, min(a+i,b)))
                    start = None
        elif opacity > 0:
            intervals.append((a,b))
    occupied = []
    for a,b in sorted(intervals):
        if occupied and a <= occupied[-1][1]:
            occupied[-1][1] = max(occupied[-1][1], b)
        else:
            occupied.append([a,b])
    if not occupied:
        return items
    out = []
    for row in items:
        if row.get("role") != "cap" or row.get("k") != "word":
            out.append(row)
            continue
        a,b = row["f"]
        cursor = a
        for lo,hi in occupied:
            if lo >= b:
                break
            if hi <= cursor:
                continue
            if cursor < lo:
                out.append(cut(row, cursor, lo, f"{row['id']}.{cursor}"))
            cursor = max(cursor,hi)
        if cursor < b:
            out.append(row if cursor == a else cut(row, cursor,b, f"{row['id']}.{cursor}"))
    return out
