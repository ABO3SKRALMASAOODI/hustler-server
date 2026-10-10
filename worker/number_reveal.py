"""Numbers land on the word: the write-time payoff guard for number graphics.

Showcase judging (Oct 2026): Peter Thiel's '140' counter counted 0 -> 139
through "they promised us flying cars and all we got was" and read 140 at
31.27 s, 0.2 s before he says "140" at 31.46 s. The payoff was given away
during its own setup, and a count-up made '140 characters' (not a growing
quantity) read like growth.

The rule: a number graphic completes exactly on the spoken number's onset,
at most MAX_LEAD_S early and never sooner. At write time (add_motion_graphic
and set_motion_graphic) the spoken number is found in the program transcript
around the item (digits, or words: "forty", "one hundred and forty",
"1.2 billion", "nineteen eighty-three") and:

- counter: ``land`` (the item second the number completes) is set LEAD_S
  before the onset. A count that would roll for less than MIN_ROLL_S starts
  earlier, on the lead-in; a 'reveal' (the hard cut for punchline numbers)
  starts on the landing itself, so nothing of it is up during the setup.
  A long roll over the setup and a count on an item marked as the payoff are
  pointed out (style='reveal' is the fix the tool does not impose).
- word_slam whose hero shows a figure ('32%', '$1.2B'; not a name such as
  'GPT-4'): the item moves so its entrance lands on the onset (the slam's
  impact 0.2 s in, a ghost's snap 2 frames in).

A moved window that now overlaps another graphic is pointed out; the
neighbour is the editor's to trim.

Nothing changes when there is no transcript, no spoken match, or the graphic
already lands within the window: a stored EDL keeps its exact timing.
"""

import re

LEAD_S = 0.02            # where a moved landing goes: 20 ms before the word
MAX_LEAD_S = 0.04        # the most a landing may anticipate the word
LATE_S = 0.004           # rounding slack after the onset (times are ms-rounded)
MIN_ROLL_S = 0.25        # a count shorter than this reads as a flicker
ROLL_S = 0.45            # the roll a moved count gets (0.3-0.6 s reads well)
LONG_ROLL_S = 0.8        # longer rolls count through the setup: say so
MIN_HOLD_S = 0.5         # the landed number should stay this long
MIN_ITEM_S = 0.3         # a moved item never gets shorter than this
MAX_LAND_S = 30.0        # the counter's 'land' param range (its MG-SPEC max)
SEARCH_BEFORE_S = 1.2    # how far before the item a spoken number may be
SEARCH_AFTER_S = 0.3

# word_slam: when its hero is legible / lands, by entrance (item seconds)
SLAM_LANDING = {"slam": 0.2, "ghost": 0.067}

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100.0, "thousand": 1e3, "million": 1e6, "billion": 1e9,
           "trillion": 1e12}
_SUFFIX = {"k": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9, "t": 1e12}
_DIGITS = re.compile(r"^[$€£¥₹]?([-−]?\d[\d,]*(?:\.\d+)?)(%|x|k|m|mm|mn|bn|b|t)?$", re.I)
_EDGE = re.compile(r"^[\"'“‘(\[]+|[\"'”’)\].,!?;:…]+$")


def _clean(tok):
    t = str(tok or "").strip().lower()
    prev = None
    while t and t != prev:
        prev, t = t, _EDGE.sub("", t)
    return t


def _same(a, b):
    return abs(a - b) <= 1e-6 * max(1.0, abs(b))


def _digit_values(tok, nxt):
    """Values a digit token can mean ('1.2' + 'billion' -> 1.2 and 1.2e9),
    and whether it took the next token."""
    m = _DIGITS.match(tok)
    if not m:
        return None, False
    body = m.group(1).replace("−", "-")
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", body):
        body = body.replace(",", "")
    elif "," in body:
        body = body.replace(",", ".", 1) if body.count(",") == 1 and "." not in body else body.replace(",", "")
    try:
        v = float(body)
    except ValueError:
        return None, False
    vals = {v}
    suf = (m.group(2) or "").lower()
    if suf in _SUFFIX:
        vals.add(v * _SUFFIX[suf])
    took = False
    if nxt in _SCALES and nxt != "hundred":
        vals.add(v * _SCALES[nxt])
        took = True
    return vals, took


def _word_value(toks, i):
    """(value, tokens used) for a spelled-out number starting at toks[i]
    ("one hundred and forty", "sixty-two thousand", "a million", "four point
    nine", "nineteen eighty-three"), or (None, 0)."""
    parts = []
    for ti in range(i, min(len(toks), i + 10)):
        parts.extend((p, ti) for p in toks[ti].split("-") if p)
    total, cur, year, used, k = 0.0, 0.0, None, 0, 0
    seen = False                      # a number word was read
    last = None                       # 'unit' | 'tens' | 'hundred' | 'scale'
    while k < len(parts):
        p = parts[k][0]
        nxt = parts[k + 1][0] if k + 1 < len(parts) else ""
        if p in _UNITS or p in _TENS:
            n = _UNITS.get(p, _TENS.get(p))
            if not seen or last in ("hundred", "scale") or (last == "tens" and n < 10):
                cur += n
            elif (year is None and total == 0 and last in ("unit", "tens")
                  and (10 <= cur <= 20) and n >= 10):
                year, cur = cur, float(n)        # nineteen | eighty-three
            else:
                break                            # 'thirty, forty': a new number
            last = "tens" if p in _TENS else "unit"
        elif p in _SCALES and (seen or k == 1 and parts[0][0] == "a"):
            if p == "hundred":
                cur = (cur or 1.0) * 100.0
                last = "hundred"
            else:
                total += (cur or 1.0) * _SCALES[p]
                cur, last = 0.0, "scale"
        elif p == "a" and not seen and nxt in _SCALES:
            k += 1
            continue
        elif p == "and" and seen and (nxt in _UNITS or nxt in _TENS):
            k += 1
            continue
        elif p == "point" and seen and nxt in _UNITS and _UNITS[nxt] < 10:
            digits = ""
            k += 1
            while k < len(parts) and parts[k][0] in _UNITS and _UNITS[parts[k][0]] < 10:
                digits += str(_UNITS[parts[k][0]])
                k += 1
            cur += float("0." + digits)
            if k < len(parts) and parts[k][0] in _SCALES and parts[k][0] != "hundred":
                # "one point two billion"
                total += cur * _SCALES[parts[k][0]]
                cur = 0.0
                k += 1
            used = k
            break
        elif p in ("percent", "times") and seen:
            used = k + 1
            break
        else:
            break
        seen = True
        k += 1
        used = k
    if not seen or not used:
        return None, 0
    value = total + cur if year is None else year * 100 + cur
    return value, parts[used - 1][1] - i + 1


def spoken_numbers(words):
    """[{values, t0, t1, said}] for every number spoken in ``words`` (program
    words with w/t0/t1, e.g. captions.heard_words)."""
    toks = [_clean(w.get("w")) for w in words]
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        vals, took = _digit_values(t, nxt)
        n = 2 if took else 1
        if vals is None:
            v, n = _word_value(toks, i)
            vals = {v} if v is not None else None
        if vals:
            j = min(len(words), i + n) - 1
            out.append({"values": vals, "t0": float(words[i]["t0"]), "t1": float(words[j]["t1"]),
                        "said": " ".join(str(words[k].get("w") or "") for k in range(i, j + 1))})
            i += n
        else:
            i += 1
    return out


def target_values(text):
    """The numbers a graphic's figure can be spoken as ('$1.2B' -> {1.2, 1.2e9}),
    or None when it has none."""
    s = str(text or "").strip().replace("−", "-").replace("–", "-")
    m = re.search(r"(-?\d(?:[\d,.   ]*\d)?)\s*([A-Za-z%]*)", s)
    if not m:
        return None
    body = re.sub(r"[   ]", "", m.group(1))
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", body):        # 1.000.000,5
        body = body.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+,\d{1,2}", body):                   # 3,5
        body = body.replace(",", ".")
    else:
        body = body.replace(",", "")
    try:
        v = float(body)
    except ValueError:
        return None
    # a scaled figure ('$1.2B', '62K', '3 million') is matched as the whole
    # amount: the spoken '1.2 billion' / '$1.2B' carry it, a bare 'one' never does
    suf = (m.group(2) or "").lower()
    scale = next((sc for w, sc in _SCALES.items() if w != "hundred" and suf.startswith(w)), None)
    if scale is None and suf in _SUFFIX:
        scale = _SUFFIX[suf]
    v = v * scale if scale else v
    return {v, abs(v)}


def _match(spoken, targets):
    return any(_same(a, b) for a in spoken["values"] for b in targets)


def counter_landing(params, span):
    """Item second the counter template completes its number (its CT)."""
    try:
        land = float((params or {}).get("land") or 0.0)
    except (TypeError, ValueError):
        land = 0.0
    land = land if land == land else 0.0
    xd = min(0.22, span * 0.1)
    if land > 0:
        return min(land, max(0.0, span - xd - 0.1))
    if (params or {}).get("style") == "reveal":
        return 0.0
    return min(1.0, max(0.5, span * 0.55))


# a token that IS a figure ('32%', '$1.2B', '62,000+', '10x', '1983'), not a
# name that carries digits ('Web3', 'GPT-4', 'COVID-19', 'F1', '5G'): a slam
# of a name lands on the name, which is the editor's own timing
_FIGURE = re.compile(r"^[+\-−~≈#]?[$€£¥₹]?\d[\d,.\u00a0\u202f]*(?:%|x|×|k|m|mm|mn|bn|b|t|s|\+)?\+?$", re.I)


def _slam_figure(text):
    """The figure a word_slam hero shows ('*32%* / fewer errors' -> '32%'),
    or None when no token is a figure."""
    # (a thin or no-break space inside a figure is a digit-group separator)
    for tok in re.split(r"[ \t\r\n/]+", str(text or "").replace("*", " ")):
        tok = _EDGE.sub("", tok.strip())
        if re.search(r"\d", tok) and _FIGURE.match(tok):
            return tok
    return None


def _words(edl, index, s, e):
    import captions as caplib
    from timeline import Timeline
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return caplib.heard_words(edl, index, tl, max(0.0, s - SEARCH_BEFORE_S), e + SEARCH_AFTER_S)


def _payoff(item):
    return bool(re.search(r"pay-?off|punch ?line|joke|button", str(item.get("purpose") or ""), re.I))


def _f(v):
    return f"{v:.2f}s"


def land(edl, index, item, prog):
    """Make a number graphic complete on its spoken number. Mutates ``item``
    (start/end/params) and returns (changed, note); note is '' when there
    is nothing to say."""
    tpl = item.get("template")
    if tpl not in ("counter", "word_slam") or item.get("phase_s") or item.get("full_duration_s"):
        return False, ""
    if not (index or {}).get("words") or not edl.get("keep"):
        return False, ""
    params = item.get("params") or {}
    figure = params.get("value") if tpl == "counter" else _slam_figure(params.get("text"))
    targets = target_values(figure) if figure else None
    if not targets:
        return False, ""
    s, e = float(item["start"]), float(item["end"])
    try:
        spoken = [n for n in spoken_numbers(_words(edl, index, s, e)) if _match(n, targets)]
    except Exception as err:  # noqa: BLE001  (a lint never blocks the edit)
        print(f"[motion] number landing skipped: {str(err)[:160]}", flush=True)
        return False, ""
    if tpl == "counter":
        changed, note = _land_counter(item, params, spoken, figure, prog)
    else:
        changed, note = _land_slam(item, params, spoken, figure, prog)
    if changed:
        note = "\n".join(n for n in (note, _new_overlaps(edl, item, s, e)) if n)
    return changed, note


def _new_overlaps(edl, item, s0, e0):
    """A moved number may now run into a neighbouring graphic (back-to-back
    stat slams): name the ones it newly overlaps; the editor decides."""
    s, e = float(item["start"]), float(item["end"])
    hits = []
    for m in edl.get("motion") or []:
        if m.get("id") == item.get("id"):
            continue
        try:
            a, b = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a < e - 1e-6 and b > s + 1e-6 and not (a < e0 - 1e-6 and b > s0 + 1e-6):
            hits.append(f"{m.get('id')} ({m.get('template')} {a:g}-{b:g}s)")
    if not hits:
        return ""
    return (f"NOTE (number): the moved window {_f(s)}-{_f(e)} now overlaps {', '.join(hits[:3])}; "
            "trim the neighbour so one graphic hands over to the next.")


def _nearest(spoken, at):
    return min(spoken, key=lambda n: abs(n["t0"] - at)) if spoken else None


def _on_time(landing, onset):
    return onset - MAX_LEAD_S - LATE_S <= landing <= onset + LATE_S


def _land_counter(item, params, spoken, figure, prog):
    s, e = float(item["start"]), float(item["end"])
    s0, e0 = s, e
    reveal = params.get("style") == "reveal"
    landing = s + counter_landing(params, e - s)
    hit = _nearest(spoken, landing)
    notes = []
    if hit is None:
        if _payoff(item) and not reveal:
            notes.append(f"NOTE (number): a counting '{figure}' on the payoff shows its climb "
                         "before the line lands; style='reveal' hard-cuts the number on the word.")
        return False, "\n".join(notes)
    onset, said = hit["t0"], hit["said"]
    target = round(onset - LEAD_S, 3)
    changed = False
    if not _on_time(landing, onset):
        before = landing
        if reveal:
            # the hard cut IS the item start: nothing of it shows during the setup
            ns = max(0.0, target)
            ne = e if e >= ns + MIN_HOLD_S else min(prog, ns + max(MIN_HOLD_S, e - s))
            if ne - ns < MIN_ITEM_S:
                # the word is in the program's last instant: no room to cut on
                return False, (f"NOTE (number): \"{said}\" is said at {_f(onset)}, too close to "
                               f"the end of the program for '{figure}' to cut on with it.")
            item["start"], item["end"] = round(ns, 3), round(ne, 3)
            params["land"] = round(max(0.0, target - ns), 3)
        else:
            roll = target - s
            if roll < MIN_ROLL_S:
                # too little time to count: start on the lead-in instead
                ns = max(0.0, target - ROLL_S)
                item["start"] = round(ns, 3)
                roll = target - ns
            params["land"] = round(min(MAX_LAND_S, max(0.0, roll)), 3)
            # the template ends a count no later than its exit: keep room to land and hold
            if e < target + MIN_HOLD_S:
                item["end"] = round(min(prog, target + MIN_HOLD_S), 3)
        item["params"] = params
        changed = True
        s, e = float(item["start"]), float(item["end"])
        moved = f"; it now starts at {_f(s)} (was {_f(s0)})" if abs(s - s0) > 1e-6 else ""
        if abs(e - e0) > 1e-6:
            moved += f"; it now ends at {_f(e)} (was {_f(e0)}) so the landed number holds"
        what = "cuts on" if reveal else "completes"
        notes.append(f"NUMBER LANDED: '{figure}' now {what} at {_f(target)}, 20 ms before "
                     f"\"{said}\" is said at {_f(onset)} (it would have read in full at "
                     f"{_f(before)}){moved}. A number never completes before its word.")
    if not reveal:
        roll = counter_landing(params, e - s)
        if roll > LONG_ROLL_S:
            notes.append(f"NOTE (number): the count rolls for {roll:.2f}s before \"{said}\", so "
                         f"viewers read the climbing figure over the setup. Start it about "
                         f"{ROLL_S:g}s before the word ({_f(max(0.0, target - ROLL_S))}) for a "
                         f"short roll, or use style='reveal' to hard-cut it on the word.")
        elif _payoff(item):
            notes.append(f"NOTE (number): '{figure}' is the payoff; a count shows its climb "
                         "first. style='reveal' hard-cuts the number on the word.")
    if e - onset < MIN_HOLD_S:
        notes.append(f"NOTE (number): it holds only {max(0.0, e - onset):.2f}s after "
                     f"\"{said}\"; end it at {_f(min(prog, onset + 0.8))} or later so the "
                     "landed number reads.")
    return changed, "\n".join(notes)


def _land_slam(item, params, spoken, figure, prog):
    s, e = float(item["start"]), float(item["end"])
    off = SLAM_LANDING.get(params.get("entrance"), 0.0)
    landing = s + off
    hit = _nearest(spoken, landing)
    if hit is None or _on_time(landing, hit["t0"]):
        return False, ""
    onset, said = hit["t0"], hit["said"]
    ns = max(0.0, round(onset - LEAD_S - off, 3))
    span = e - s
    ne = min(prog, ns + span)
    if ne - ns < 0.3:
        return False, ""
    item["start"], item["end"] = ns, round(ne, 3)
    return True, (f"NUMBER LANDED: the slam of '{figure}' moved to {_f(ns)}-{_f(ne)} so it "
                  f"lands 20 ms before \"{said}\" at {_f(onset)} (it landed at {_f(landing)}). "
                  "A number never shows before its word.")
