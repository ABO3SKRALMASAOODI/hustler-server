"""Track 4B: the UI scene graph and explainer kit (genre E).

A short explained through interface: cards, a three-state karaoke pill,
comment threads, dashboards with counters and bars, editor windows, media
slots that play real recordings — as PERSISTENT objects across scenes.
An object that is in two consecutive scenes is the same object: it morphs
(FLIP: the plate's rect interpolates, the content scales uniformly from
the layout it was set in) instead of cutting; a new object can open as an
iris from an element of the previous scene (``from``), so the eye follows
the idea from the thing that caused it.

Everything is SOLVED here, in Python (no browser): the layout of every
scene in output pixels inside the frame's safe area, the morph segments,
the iris origins, the counters' landing times (on the spoken number when
it is said), the karaoke pill's word times. The motion template
``ui_scene`` only draws what the graph says at time t, and the ``ui_media``
compiler reads the same graph for the media slots' rects (``rect_at``) —
one geometry, two renderers.

Numbers are never invented: a figure on screen is the user's data (CSV
rows or a {name: number} map, bound with ``bind``), a number the speaker
says, or a number in the user's own request. ``check_numbers`` names the
first one that is none of these.
"""

import csv
import io
import json
import math
import re

GRAPH_VERSION = 1
KINDS = ("card", "pill", "comment", "metric", "bars", "window", "media",
         "text")
LAYOUTS = ("stack", "grid", "focus", "thread", "row")
PILL_STATES = ("idle", "live", "done")
MORPH_S = 0.55
ENTER_S = 0.45
IRIS_S = 0.6
EXIT_S = 0.3
MAX_SCENES = 8
LAND_WINDOW_S = 3.0
MAX_OBJECTS = 12
_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


# ── frame and safe area ──────────────────────────────────────────────────

def safe_area(W, H):
    """(x0, y0, x1, y1) px the scene lays out in: platform UI and the
    caption band stay clear (a 9:16 frame keeps 12% top, 22% bottom)."""
    portrait = H > W * 1.2
    mx = 0.065 * W
    top = (0.12 if portrait else 0.08) * H
    bot = (0.78 if portrait else 0.86) * H
    return (mx, top, W - mx, bot)


def _u(W, H):
    """One design unit: 1/360 of the frame's short side."""
    return min(W, H) / 360.0


# ── text metrics (estimates; the page sets the same fonts) ───────────────

def _lines(text, width_px, font_px, max_lines=4):
    t = str(text or "").strip()
    if not t:
        return 0
    per = max(1, int(width_px / (0.53 * font_px)))
    n, cur = 1, 0
    for w in t.split():
        add = len(w) + (1 if cur else 0)
        if cur + add > per:
            n += 1
            cur = len(w)
        else:
            cur += add
    return min(n, max_lines)


def _natural(obj, width, u):
    """(w, h) px an object wants at ``width`` px (its layout width)."""
    k = obj["kind"]
    if k == "pill":
        txt = str(obj.get("label") or "")
        h = 30 * u
        w = min(width, 0.53 * 15 * u * max(4, len(txt)) + 62 * u)
        return w, h
    if k == "text":
        f = 30 * u
        n = _lines(obj.get("text"), width, f, 3)
        return width, max(1, n) * f * 1.12 + 6 * u
    if k == "card":
        tf, bf = 17 * u, 13.5 * u
        inner = width - 36 * u
        h = 18 * u * 2 + (tf * 1.2 if obj.get("title") else 0)
        if obj.get("value"):
            h += 30 * u * 1.1 + 4 * u
        if obj.get("body"):
            h += 6 * u + _lines(obj["body"], inner, bf, 4) * bf * 1.35
        return width, max(h, 64 * u)
    if k == "comment":
        bf = 13.5 * u
        inner = width - 74 * u
        n = max(1, _lines(obj.get("text"), inner, bf, 4))
        return width, max(56 * u, 16 * u * 2 + 15 * u + n * bf * 1.35)
    if k == "metric":
        return width, 92 * u
    if k == "bars":
        n = max(1, len(obj.get("rows") or []))
        return width, 20 * u * 2 + (17 * u if obj.get("title") else 0) + \
            n * 30 * u
    if k == "window":
        n = max(1, min(10, len(obj.get("lines") or [])))
        return width, 30 * u + 16 * u + n * 13 * u * 1.55
    if k == "media":
        ar = float(obj.get("aspect") or (16 / 9.0))
        return width, width / max(0.3, ar)
    return width, 60 * u


# ── data binding (user data only) ────────────────────────────────────────

def parse_data(data):
    """User data as {"rows": [{col: str}], "map": {name: float}}: CSV text
    (header row), a list of row objects, or a {name: number} map."""
    out = {"rows": [], "map": {}}
    if data is None:
        return out
    if isinstance(data, str):
        txt = data.strip()
        if txt.startswith("{") or txt.startswith("["):
            return parse_data(json.loads(txt))
        rd = csv.DictReader(io.StringIO(txt))
        out["rows"] = [{(k or "").strip(): (v or "").strip()
                        for k, v in r.items()} for r in rd]
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            n = to_number(v)
            if n is None:
                raise ValueError(f"data '{k}' is not a number")
            out["map"][str(k)] = n
        return out
    if isinstance(data, list):
        out["rows"] = [{str(k): str(v) for k, v in r.items()}
                       for r in data if isinstance(r, dict)]
        return out
    raise ValueError("data is CSV text, a list of rows or a {name: number} "
                     "map")


def to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def lookup(data, bind):
    """The number ``bind`` names in the user's data: "name" (map), or
    {"row": label, "col": column[, "key": label column]} (rows)."""
    if isinstance(bind, str):
        if bind in data["map"]:
            return data["map"][bind]
        raise ValueError(f"'{bind}' is not in the data")
    if isinstance(bind, dict):
        col = bind.get("col")
        row = bind.get("row")
        rows = data["rows"]
        if not rows:
            raise ValueError("bind names a row but no CSV rows were given")
        key = bind.get("key") or next(iter(rows[0]))
        for r in rows:
            if str(r.get(key, "")).strip().lower() == str(row).strip().lower():
                if col not in r:
                    raise ValueError(f"column '{col}' is not in the data")
                n = to_number(r[col])
                if n is None:
                    raise ValueError(f"{row}/{col} is not a number")
                return n
        raise ValueError(f"row '{row}' is not in the data")
    raise ValueError("bind is a name or {row, col}")


def fmt_number(x, like=None):
    """A number set the way the data wrote it (thousands, decimals)."""
    if like is not None and isinstance(like, str):
        dec = len(like.split(".")[1]) if "." in like else 0
        s = f"{x:,.{dec}f}" if "," in like else f"{x:.{dec}f}"
        pre = re.match(r"^\s*([^\d\-+]*)", like).group(1)
        post = re.search(r"([^\d.,]*)\s*$", like).group(1)
        return f"{pre}{s}{post}"
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def bind_objects(scenes, data):
    """Resolve every ``bind`` to the user's numbers, in place: a metric's
    ``value`` and a bars row's ``value`` come only from the data."""
    d = parse_data(data)
    for sc in scenes:
        for o in sc.get("objects") or []:
            if o.get("bind") is not None:
                n = lookup(d, o["bind"])
                o["value_num"] = n
                o["value"] = fmt_number(n, o.get("format"))
                o["bound"] = True
            if o.get("kind") == "bars":
                rows = []
                for r in o.get("rows") or []:
                    if r.get("bind") is not None:
                        n = lookup(d, r["bind"])
                        rows.append(dict(r, value_num=n,
                                         value=fmt_number(n, r.get("format")),
                                         bound=True))
                    else:
                        rows.append(dict(r))
                if o.get("rows_from"):
                    rf = o["rows_from"]
                    key, col = rf.get("key"), rf.get("col")
                    for r in d["rows"][: int(rf.get("max") or 6)]:
                        n = to_number(r.get(col))
                        if n is None:
                            raise ValueError(f"{col} of '{r.get(key)}' is "
                                             "not a number")
                        rows.append({"label": r.get(key), "value_num": n,
                                     "value": fmt_number(n, r.get(col)),
                                     "bound": True})
                o["rows"] = rows
    return scenes


def _numbers_in(text):
    out = []
    for m in _NUM_RE.finditer(str(text or "")):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


_WORDNUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30,
            "forty": 40, "fifty": 50, "hundred": 100, "thousand": 1000,
            "million": 1e6, "billion": 1e9}


def spoken_numbers(words):
    """[(value, onset)] of the numbers in a word list ({w, t0}): digits,
    and simple spelled numbers ("a thousand", "four", "two hundred")."""
    out = []
    i = 0
    ws = list(words or [])
    while i < len(ws):
        w = re.sub(r"[^a-z0-9.,]", "", str(ws[i].get("w") or "").lower())
        n = to_number(w) if any(c.isdigit() for c in w) else None
        if n is not None:
            out.append((n, float(ws[i]["t0"])))
            i += 1
            continue
        if w in _WORDNUM or (w == "a" and i + 1 < len(ws) and re.sub(
                r"[^a-z]", "", str(ws[i + 1].get("w") or "").lower()) in (
                "hundred", "thousand", "million", "billion")):
            t0 = float(ws[i]["t0"])
            total, cur, j = 0.0, 0.0, i
            while j < len(ws):
                x = re.sub(r"[^a-z]", "", str(ws[j].get("w") or "").lower())
                if x == "a" and j == i:
                    cur = 1.0
                elif x in ("hundred",):
                    cur = max(1.0, cur) * 100
                elif x in ("thousand", "million", "billion"):
                    total += max(1.0, cur) * _WORDNUM[x]
                    cur = 0.0
                elif x in _WORDNUM and x not in ("hundred",):
                    cur += _WORDNUM[x]
                elif x == "and" and j > i:
                    pass
                else:
                    break
                j += 1
            val = total + cur
            if val:
                out.append((val, t0))
            i = max(j, i + 1)
            continue
        i += 1
    return out


def check_numbers(scenes, words=(), request_text=None):
    """None, or the first figure on screen that is neither bound to the
    user's data, nor said, nor in the user's request (figures are never
    invented)."""
    allowed = {round(v, 6) for v, _t in spoken_numbers(words)}
    allowed |= {round(v, 6) for v in _numbers_in(request_text)}

    def ok(v):
        return round(v, 6) in allowed

    for sc in scenes:
        for o in sc.get("objects") or []:
            fields = [o.get(k) for k in ("title", "body", "text", "label",
                                         "delta", "name")]
            fields += list(o.get("lines") or [])
            if not o.get("bound"):
                fields.append(o.get("value"))
            for r in o.get("rows") or []:
                fields.append(r.get("label"))
                if not r.get("bound"):
                    fields.append(r.get("value"))
            for f in fields:
                for v in _numbers_in(f):
                    if not ok(v):
                        return (f"{o.get('id')}: '{f}' shows {v:g}, which is "
                                "not in the user's data, the words or the "
                                "request — bind it to data or leave it out")
    return None


# ── layout ───────────────────────────────────────────────────────────────

def _layout(objs, layout, W, H, focus=None):
    """{id: [x, y, w, h]} for one scene's objects."""
    u = _u(W, H)
    x0, y0, x1, y1 = safe_area(W, H)
    sw, sh = x1 - x0, y1 - y0
    gap = 12 * u
    out = {}
    if not objs:
        return out
    if layout == "row":
        sizes = [_natural(o, sw, u) for o in objs]
        tw = sum(s[0] for s in sizes) + gap * (len(objs) - 1)
        k = min(1.0, sw / max(1.0, tw))
        x = x0 + (sw - tw * k) / 2.0
        hmax = max(s[1] for s in sizes) * k
        yc = y0 + sh * 0.5
        for o, (w, h) in zip(objs, sizes):
            out[o["id"]] = [x, yc - h * k / 2.0, w * k, h * k]
            x += (w + gap) * k
        return out
    if layout == "grid":
        cw = (sw - gap) / 2.0
        wide = ("text", "media", "window", "bars", "pill")
        sizes = [_natural(o, cw if o["kind"] not in wide else sw, u)
                 for o in objs]
        rows, cur = [], []
        for o, s in zip(objs, sizes):
            full = o["kind"] in wide
            if full:
                if cur:
                    rows.append(cur)
                    cur = []
                rows.append([(o, s)])
            else:
                cur.append((o, s))
                if len(cur) == 2:
                    rows.append(cur)
                    cur = []
        if cur:
            rows.append(cur)
        th = sum(max(s[1] for _o, s in r) for r in rows) + gap * (
            len(rows) - 1)
        k = min(1.0, sh / max(1.0, th))
        y = y0 + (sh - th * k) / 2.0
        for r in rows:
            rh = max(s[1] for _o, s in r) * k
            if len(r) == 1 and (r[0][1][0] > cw + 1 or r[0][0]["kind"] in
                                wide):
                o, (w, h) = r[0]
                out[o["id"]] = [x0 + (sw - w * k) / 2.0, y, w * k, h * k]
            else:
                for j, (o, (w, h)) in enumerate(r):
                    out[o["id"]] = [x0 + j * (cw + gap) * 1.0 + (cw - cw * k)
                                    / 2.0 if k < 1 else x0 + j * (cw + gap),
                                    y, w * k, h * k]
            y += rh + gap * k
        return out
    if layout == "focus":
        fid = focus or objs[0]["id"]
        rest = [o for o in objs if o["id"] != fid]
        strip_h = 0.0
        if rest:
            chip_w = (sw - gap * (len(rest) - 1)) / max(1, len(rest))
            chip_h = min(70 * u, chip_w * 0.5)
            for j, o in enumerate(rest):
                out[o["id"]] = [x0 + j * (chip_w + gap), y0, chip_w, chip_h]
            strip_h = chip_h + gap * 1.5
        fo = next(o for o in objs if o["id"] == fid)
        w, h = _natural(fo, sw, u)
        k = min(1.0, (sh - strip_h) / max(1.0, h))
        yy = y0 + strip_h + (sh - strip_h - h * k) / 2.0
        out[fid] = [x0 + (sw - w * k) / 2.0, yy, w * k, h * k]
        return out
    # stack / thread: one column
    sizes = [_natural(o, sw, u) for o in objs]
    th = sum(s[1] for s in sizes) + gap * (len(objs) - 1)
    k = min(1.0, sh / max(1.0, th))
    if layout == "thread":
        y = y1 - th * k                    # the newest sits at the bottom
    else:
        y = y0 + (sh - th * k) / 2.0
    for o, (w, h) in zip(objs, sizes):
        out[o["id"]] = [x0 + (sw - w * k) / 2.0, y, w * k, h * k]
        y += (h + gap) * k
    return out


# ── the graph ────────────────────────────────────────────────────────────

def _r(v):
    return [round(float(x), 1) for x in v]


def solve(scenes, duration, W, H, *, words=(), theme=None, accent=None):
    """The graph the template draws and ui_media reads.

    ``scenes``: [{at (item seconds), layout, focus?, objects: [{id, kind,
    ...content, from?, state?, count?}]}] (bound already: bind_objects).
    ``words``: the item-clock words ({w, t0}) for counters and the pill.
    """
    if not scenes:
        raise ValueError("a UI scene needs at least one scene")
    if len(scenes) > MAX_SCENES:
        raise ValueError(f"at most {MAX_SCENES} scenes")
    scenes = sorted(scenes, key=lambda s: float(s.get("at") or 0.0))
    if float(scenes[0].get("at") or 0.0) > 0.01:
        scenes[0] = dict(scenes[0], at=0.0)
    times = [float(s.get("at") or 0.0) for s in scenes] + [float(duration)]
    for a, b in zip(times, times[1:]):
        if b - a < MORPH_S + 0.15:
            raise ValueError(f"scenes at {a:g}s and {b:g}s are too close "
                             f"(each holds at least {MORPH_S + 0.15:g}s)")
    ids = []
    content = {}
    rects, present = [], []
    for k, sc in enumerate(scenes):
        objs = sc.get("objects") or []
        if not objs:
            raise ValueError(f"scene {k + 1} has no objects")
        if len(objs) > MAX_OBJECTS:
            raise ValueError(f"scene {k + 1}: at most {MAX_OBJECTS} objects")
        layout = sc.get("layout") or "stack"
        if layout not in LAYOUTS:
            raise ValueError(f"layout is one of {LAYOUTS}")
        for o in objs:
            if o.get("kind") not in KINDS:
                raise ValueError(f"{o.get('id')}: kind is one of {KINDS}")
            if not o.get("id"):
                raise ValueError("every object needs an id (the same id in "
                                 "two scenes is the same object)")
            if o["id"] not in content:
                ids.append(o["id"])
                content[o["id"]] = {}
            # later scenes may update content (a value, a state)
            content[o["id"]].setdefault("_by_scene", {})[k] = o
        rects.append(_layout(objs, layout, W, H, sc.get("focus")))
        present.append({o["id"] for o in objs})
    u = _u(W, H)
    out_objs = []
    irises = []
    for oid in ids:
        by = content[oid]["_by_scene"]
        first_k = min(by)
        base = dict(by[first_k])
        kind = base["kind"]
        segs = []
        states = []
        iris = None
        enter = None
        exits = []
        design = max((rects[k][oid] for k in by), key=lambda r: r[2] * r[3])
        for k in range(len(scenes)):
            t = times[k]
            if oid not in present[k]:
                if k > 0 and oid in present[k - 1]:
                    r = rects[k - 1][oid]
                    segs.append([t, t + EXIT_S] + _r(r) + _r(r) + [1, 0,
                                                                  "inCubic"])
                    exits.append(t)
                continue
            r = rects[k][oid]
            if k > 0 and oid in present[k - 1]:
                r0 = rects[k - 1][oid]
                segs.append([t, t + MORPH_S] + _r(r0) + _r(r) + [1, 1,
                                                                  "morph"])
            else:
                src = by[k].get("from")
                if src and k > 0 and src in present[k - 1]:
                    # the layout makes room first; then the new object
                    # opens out of its cause where the cause now rests
                    if src in present[k]:
                        o_r, ti = rects[k][src], t + MORPH_S
                    else:
                        o_r, ti = rects[k - 1][src], t
                    ox = round(o_r[0] + o_r[2] / 2.0)
                    oy = round(o_r[1] + o_r[3] / 2.0)
                    iris = {"t": round(ti, 3), "dur": IRIS_S, "ox": ox,
                            "oy": oy, "from": src}
                    irises.append(dict(iris, id=oid))
                    segs.append([t, ti + IRIS_S] + _r(r) + _r(r)
                                + [1, 1, "linear"])
                else:
                    enter = round(t, 3)
                    segs.append([t, t + ENTER_S] + _r(r) + _r(r) + [0, 1,
                                                                    "snap"])
            if kind == "pill":
                states.append([round(t, 3), by[k].get("state") or "idle"])
            hold_end = times[k + 1] if k + 1 < len(times) else times[-1]
            settle = segs[-1][1]
            if settle < hold_end:
                segs.append([settle, hold_end] + _r(r) + _r(r)
                            + [1, 1, "linear"])
        o = {"id": oid, "kind": kind, "design": _r(design), "segs": segs,
             "iris": iris, "enter": enter}
        for key in ("title", "body", "text", "label", "name", "value",
                    "delta", "lines", "rows", "aspect", "icon", "lang",
                    "accent", "style", "caption"):
            if key in base and base[key] is not None:
                o[key] = base[key]
        # content updates by scene (a value that changes, rows that grow)
        upd = {}
        for k, ob in by.items():
            if k == first_k:
                continue
            ch = {key: ob[key] for key in ("value", "body", "title", "rows",
                                           "label", "lines", "text")
                  if key in ob and ob[key] != base.get(key)}
            if ch:
                upd[str(round(times[k], 3))] = ch
        if upd:
            o["updates"] = upd
        if states:
            o["states"] = states
        if kind in ("metric",) and o.get("value") is not None:
            seen_at = iris["t"] if iris else times[first_k]
            o["count"] = _counter(by[first_k], seen_at, words)
        if kind == "pill" and o.get("label"):
            o["karaoke"] = _karaoke(o["label"], times[first_k],
                                    times[-1], words)
        out_objs.append(o)
    return {"v": GRAPH_VERSION, "W": int(W), "H": int(H),
            "dur": round(float(duration), 3),
            "scenes": [round(t, 3) for t in times[:-1]],
            "objects": out_objs, "irises": irises,
            "theme": theme or "dark", "accent": accent or "#8C7CFF",
            "u": round(u, 4)}


def _counter(o, t_enter, words):
    """Counter timing: count to the value, landing ON the spoken number
    when the speaker says it inside the item, else 0.9 s after entering."""
    n = o.get("value_num")
    if n is None:
        n = to_number(o.get("value"))
    if n is None or o.get("count") is False:
        return None
    land = None
    for v, t in spoken_numbers(words):
        # the number said while the tile is new (from just before it shows
        # to 3 s after): the same figure said much later is another
        # sentence (maybe another quantity), never a reason to hold a tile
        # at zero
        if abs(v - n) < 1e-6 and t_enter - 0.4 <= t <= \
                t_enter + LAND_WINDOW_S:
            land = t
            break
    # a count needs a beat to read: it lands on the word, or as soon after
    # the tile shows as a count can (0.5 s) when the word came first
    t1 = max(land, t_enter + 0.5) if land is not None else t_enter + 0.9
    t0 = max(t_enter, t1 - 1.2)
    return {"t0": round(t0, 3), "t1": round(t1, 3), "to": n,
            "fmt": o.get("value"),
            "landed_on_word": land is not None and t1 - land <= 0.1,
            "said": land is not None}


def _karaoke(label, t0, t1, words):
    """Word onsets for the pill's label (item clock): each label word lit
    when it is said; None when the label is not spoken in the window."""
    toks = [re.sub(r"[^a-z0-9']", "", w.lower()) for w in str(label).split()]
    ws = [(re.sub(r"[^a-z0-9']", "", str(w.get("w") or "").lower()),
           float(w["t0"])) for w in words or [] if t0 - 0.05 <= float(
               w["t0"]) <= t1]
    out, j = [], 0
    for tok in toks:
        hit = None
        while j < len(ws):
            if ws[j][0] == tok:
                hit = ws[j][1]
                j += 1
                break
            j += 1
        out.append(None if hit is None else round(hit, 3))
    return out if any(x is not None for x in out) else None


# ── reading the graph (the ui_media compiler, tests, acceptance) ─────────

_EASE = {
    "linear": lambda x: x,
    # a FLIP morph: eased in and out, so a persistent object travels and
    # settles without a first-frame jump
    "morph": lambda x: 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2,
    "inCubic": lambda x: x ** 3,
    "outCubic": lambda x: 1 - (1 - x) ** 3,
    "snap": lambda x: 1 - (1 - x) ** 6,
}


def state_at(obj, t):
    """(rect [x, y, w, h], opacity) of a graph object at item second t, or
    (None, 0) when it is not on screen — the template's own reading."""
    segs = obj.get("segs") or []
    cur = None
    for s in segs:
        if s[0] <= t + 1e-9:
            cur = s
        else:
            break
    if cur is None:
        return None, 0.0
    t0, t1 = cur[0], cur[1]
    p = 1.0 if t1 <= t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
    e = _EASE.get(cur[12], _EASE["linear"])(p)
    a, b = cur[2:6], cur[6:10]
    rect = [a[i] + (b[i] - a[i]) * e for i in range(4)]
    op = cur[10] + (cur[11] - cur[10]) * e
    if t > t1 and cur[11] <= 0:
        return None, 0.0
    return rect, op


def rect_at(graph, oid, t):
    for o in graph.get("objects") or []:
        if o["id"] == oid:
            return state_at(o, t)[0]
    return None


def iris_radius(obj, t):
    """The iris clip radius at t (None = no clip), from the object's own
    rect corners' farthest distance to the origin."""
    ir = obj.get("iris")
    if not ir or t < ir["t"]:
        return None if not ir else 0.0
    p = min(1.0, (t - ir["t"]) / max(1e-6, ir["dur"]))
    if p >= 1.0:
        return None
    r, _op = state_at(obj, t)
    if r is None:
        return 0.0
    far = max(math.hypot(cx - ir["ox"], cy - ir["oy"])
              for cx in (r[0], r[0] + r[2]) for cy in (r[1], r[1] + r[3]))
    return _EASE["outCubic"](p) * far


def media_slots(graph):
    return [o["id"] for o in graph.get("objects") or []
            if o.get("kind") == "media"]


def shown_texts(graph):
    """Every string the scene prints (the captions' de-duplication)."""
    out = []
    for o in graph.get("objects") or []:
        for key in ("title", "body", "text", "label", "name", "value",
                    "delta", "caption"):
            if isinstance(o.get(key), str) and o[key].strip():
                out.append(o[key].strip())
        for ln in o.get("lines") or []:
            if isinstance(ln, str) and ln.strip():
                out.append(ln.strip())
        for r in o.get("rows") or []:
            for key in ("label", "value"):
                if isinstance(r.get(key), str) and r[key].strip():
                    out.append(r[key].strip())
        for ch in (o.get("updates") or {}).values():
            for key in ("value", "body", "title", "label", "text"):
                if isinstance(ch.get(key), str) and ch[key].strip():
                    out.append(ch[key].strip())
    seen, uniq = set(), []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s[:120])
    return uniq[:40]


def continuity(graph, oid, fps=30.0):
    """Max per-frame displacement (px) of an object's rect centre over its
    whole life: a persistent object never jumps."""
    o = next((x for x in graph["objects"] if x["id"] == oid), None)
    if o is None:
        return None
    prev, worst, n = None, 0.0, int(graph["dur"] * fps)
    for i in range(n):
        r, op = state_at(o, i / fps)
        if r is None or op <= 0:
            prev = None
            continue
        c = (r[0] + r[2] / 2.0, r[1] + r[3] / 2.0)
        if prev is not None:
            worst = max(worst, math.hypot(c[0] - prev[0], c[1] - prev[1]))
        prev = c
    return worst
