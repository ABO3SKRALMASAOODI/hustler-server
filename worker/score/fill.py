"""Every checked picture reaches the film (lane K round seven, 14 Nov 2026).

The rated round-six films cut 5.2 times in 10 s against the judge's 16.1, and their talking stretches held the take
for 14.5 s (p5574), 12.5 s (p5565) and 3.5 s twice (p5591) while pictures Supply had found, fetched and checked for
those very words sat in the Bin unused: 6 of p5591's 10 footage offers, a spa clip of p5574. A Score is one model's
draft, written once against a deadline; whether a checked picture of the words being said is on screen while they
are said should not hang on that draft.

One pass, after the Score is written and before the film is lowered. No model is called and nothing is read of what
anyone asked for:

  * each Bin picture the film does not show is cut in on the first word of the moment it was found for (the Bin's
    own `for`: Supply found it for those words and the look kept it for them), over the take, while the voice runs
    on. Two or three pictures of one moment are a run. A still plays STILL_F frames, a clip CLIP_F; score.pace then
    cuts each inside itself, so a picture is two framings.
  * the beat's own `cut` and `pic` lists are rewritten, as film._place_clips does for footage, and every line that
    names one of the beat's shots (`b03.s1`) is renumbered with it.

What it never does: cut in where the film already shows a picture, a view or a page; cover the film's first half
second or its last line (the person closes the film); put two runs closer than a breath of the take; touch a beat
whose pictures it cannot read back from the compiled film; or keep a result that does not compile to the same
length. A picture whose words the film dropped stays out. VALMERA_FILL=0 turns it off.
"""
import copy
import json
import os
import re

STILL_F = 28           # a still cut in plays this long (0.93 s): score.pace cuts it inside itself to two framings
CLIP_F = 36            # a clip this long (1.2 s): one jump inside it
MIN_F = 16             # a picture shorter than this is a flash, not a cutaway
TAKE_F = 12            # the take, between two runs, is on screen at least this long; less and the run starts on the shot's cut
OPEN_F = 15            # nothing is cut in over the film's first half second: the hook is the Score's
CLOSE_F = 30           # nor over its last second: the last line needs the face
RUN_MOST = 3           # pictures of one moment, back to back
REACH_F = 45           # a moment whose first word is under another picture waits this long for the take, at most
_SHOT = re.compile(r"^([A-Za-z][\w\-]*)\.s(\d+)")


def on():
    return os.environ.get("VALMERA_FILL", "1") != "0"


def _bin(work):
    try:
        with open(os.path.join(work, "bin.json"), encoding="utf-8") as f:
            return (json.load(f) or {}).get("items") or []
    except (OSError, ValueError):
        return []


def _shown(l1, text):
    """Bin ids the film shows, or the Score names anywhere (a picture the Score placed and the governor or the last
    look took out was judged: it is not put back)."""
    got = set()
    for line in text.splitlines():
        if '"k":"treat"' in line.replace(" ", ""):
            continue                                   # the hooks it weighed are not pictures it placed
        got |= {int(m) for m in re.findall(r"bin:(\d+)", line)}
    for it in l1.get("items") or []:
        for k in ("pic", "framed"):
            v = it.get(k)
            if isinstance(v, str) and v.startswith("bin:") and v[4:].split("@")[0].isdigit():
                got.add(int(v[4:].split("@")[0]))
    return got


def _moment(item, words):
    """The source second a Bin picture was found for: its first word, or its second of the upload."""
    ref = str(item.get("for") or "")
    if ref.startswith("w"):
        first = ref[1:].split("-")[0].strip()
        if first.isdigit() and int(first) < len(words):
            w = words[int(first)]
            return float(w.get("t0", w.get("start", 0.0)) or 0.0)
        return None
    try:
        return float(ref)
    except ValueError:
        return None


def _simple(ref):
    """A picture reference this pass can copy as it stands."""
    if isinstance(ref, dict):
        return isinstance(ref.get("src"), str) or "plate" in ref
    return isinstance(ref, str) and (ref == "main" or ref.startswith(("main@", "bin:", "view:", "%")))


def _renumber(lines, bid, new_of):
    """Every `bid.sK` a line names follows its shot to its new number."""
    pat = re.compile(r"(?<![\w.])" + re.escape(bid) + r"\.s(\d+)(?!\d)")

    def fix(v):
        if isinstance(v, str):
            return pat.sub(lambda m: f"{bid}.s{new_of.get(int(m.group(1)), int(m.group(1)))}", v)
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        return v
    for o in lines:
        for k in list(o):
            if k not in ("id", "k"):
                o[k] = fix(o[k])


def fill(work, text):
    """(score text, report, the filled film compiled or None). The text comes back as it was when nothing was cut in."""
    text2, rep, comp = _fill(work, text)
    return text2, rep, comp


def _fill(work, text):
    rep = {"unused": 0, "placed": 0, "beats": 0}
    if not on():
        return text, rep, None
    from . import compiler, direct
    items = [it for it in _bin(work) if isinstance(it.get("id"), int)]
    if not items:
        return text, rep, None
    try:
        with open(os.path.join(work, "index.json"), encoding="utf-8") as f:
            words = (json.load(f) or {}).get("words") or []
    except (OSError, ValueError):
        words = []
    lines, _bad = compiler.parse_score(text)
    lines = [direct.clean(o) for o in lines]
    text0 = direct.dump(lines)
    comp = compiler.compile_score(text0, work_dir=work)
    l1 = comp.l1
    fps, D = float(l1["fps"]), int(l1["dur_f"])
    shown = _shown(l1, text0)
    spare = [it for it in items if it["id"] not in shown]
    rep["unused"] = len(spare)
    if not spare:
        return text, rep, None
    shots = sorted((i for i in l1["items"] if i.get("k") == "shot"), key=lambda i: i["f"][0])

    def prog_f(t):
        for sp in l1.get("spine") or []:
            if sp["src"][0] <= t < sp["src"][1]:
                return sp["f"][0] + int(round((t - sp["src"][0]) * fps))
        return None

    def take_at(f):
        s = next((x for x in shots if x["f"][0] <= f < x["f"][1]), None)
        ok = s is not None and s.get("pic") == "main" and s.get("view") is None and not s.get("treat") and not s.get("framed") and _SHOT.match(str(s.get("id") or ""))
        return s if ok else None

    # the moments: each spare picture on the frame its words start, runs kept together
    wants, why = {}, {}
    rep["why"] = why
    for it in spare:
        t = _moment(it, words)
        f = prog_f(t) if t is not None else None
        if f is None:
            why[it["id"]] = "its words are not in the film"
            continue
        if take_at(f) is None:                         # another picture is on screen on that word: the next frame of the take, if near
            nxt = next((x["f"][0] for x in shots if x["f"][0] > f and x["f"][0] - f <= REACH_F and take_at(x["f"][0]) is not None), None)
            if nxt is None:
                why[it["id"]] = "another picture holds its words"
                continue
            f = nxt
        key = (str(it.get("for")), it.get("wish"))
        wants.setdefault(key, {"f": f, "pics": []})["pics"].append(it)
    by_beat = {}
    for w in sorted(wants.values(), key=lambda w: w["f"]):
        s = take_at(w["f"])
        if s is None or w["f"] < OPEN_F or w["f"] > D - CLOSE_F - MIN_F:
            why.update({it["id"]: "its words open or close the film" for it in w["pics"]})
            continue
        m = _SHOT.match(str(s["id"]))
        by_beat.setdefault(m.group(1), []).append(dict(w, k=int(m.group(2))))
    if not by_beat:
        return text, rep, None
    snapshot = copy.deepcopy(lines)
    by_id = {o.get("id"): o for o in lines if o.get("k") == "b"}
    placed = 0
    for bid, runs in by_beat.items():
        o = by_id.get(bid)
        if o is None or o.get("f") is not None or (isinstance(o.get("cut"), str) and "lead" in o["cut"]):
            why.update({it["id"]: "its beat cannot be rewritten" for r in runs for it in r["pics"]})
            continue
        groups = {}
        for x in shots:
            mm = _SHOT.match(str(x.get("id") or ""))
            if mm and mm.group(1) == bid:
                g = groups.setdefault(int(mm.group(2)), [x["f"][0], x["f"][1]])
                g[0], g[1] = min(g[0], x["f"][0]), max(g[1], x["f"][1])
        order = sorted(groups)
        if not order or order != list(range(len(order))) or any(groups[a][1] != groups[b][0] for a, b in zip(order, order[1:])):
            why.update({it["id"]: "its beat's shots do not read back in order" for r in runs for it in r["pics"]})
            continue
        pics = o.get("pic")
        pics = pics if isinstance(pics, list) else [pics if pics is not None else "main"]
        if not all(_simple(r) for r in pics):
            why.update({it["id"]: "its beat names a picture set" for r in runs for it in r["pics"]})
            continue
        cut, pic, new_of, n_here = [], [], {}, 0
        for k in order:
            ref, (a, z) = pics[k % len(pics)], groups[k]
            mine = sorted((r for r in runs if r["k"] == k), key=lambda r: r["f"])
            if not mine or ref != "main":
                why.update({it["id"]: "its shot is a framing the Score wrote" for r in mine for it in r["pics"]})
                new_of[k] = len(cut)
                cut.append(z - a)
                pic.append(ref)
                continue
            pos, first_take = a, None
            for r in mine:
                f = max(r["f"], pos)
                before = f - pos
                if before < TAKE_F:
                    before, f = 0, pos
                room = min(z, D - CLOSE_F) - f
                todo = []
                for it in r["pics"][:RUN_MOST]:
                    want = CLIP_F if it.get("kind") == "video" else STILL_F
                    if it.get("kind") == "video":
                        want = min(want, int(float(it.get("dur") or 0.0) * fps) - 2)
                    ln = want if room >= want else room if room >= MIN_F else 0
                    if ln < MIN_F:
                        break
                    todo.append((ln, f"bin:{it['id']}"))
                    room -= ln
                if not todo:
                    why.update({it["id"]: "no room for it in its shot" for it in r["pics"]})
                    continue
                why.update({it["id"]: "a run holds three pictures" for it in r["pics"][len(todo):]})
                if before:
                    if first_take is None:
                        first_take = len(cut)
                    cut.append(before)
                    pic.append(ref)
                for ln, new in todo:
                    cut.append(ln)
                    pic.append(new)
                    n_here += 1
                pos = f + sum(ln for ln, _ in todo)
            tail = z - pos
            if 0 < tail < TAKE_F and pic and pic[-1] != ref and n_here:
                cut[-1] += tail                        # too little of the take left to read: the picture holds to the cut
            elif tail > 0:
                if first_take is None:
                    first_take = len(cut)
                cut.append(tail)
                pic.append(ref)
            new_of[k] = first_take if first_take is not None else len(cut) - 1
        if not n_here:
            continue
        cut[-1] = 999                                  # the last picture takes what is left, as the Score's own lists do
        o["cut"], o["pic"] = cut, pic
        if any(a != b for a, b in new_of.items()):
            _renumber(lines, bid, new_of)
        placed += n_here
        rep["beats"] += 1
    if not placed:
        return text, rep, None
    text2 = direct.dump(lines)
    try:
        comp2 = compiler.compile_score(text2, work_dir=work)
        ok = comp2.l1["dur_f"] == D and not compiler.validate_l1(comp2.l1)
        now = _shown(comp2.l1, "")
    except Exception as exc:  # noqa: BLE001 - the film as it was written
        ok, now = False, set()
        rep["fault"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    if not ok:
        lines[:] = snapshot
        rep.setdefault("fault", "the filled film did not compile to the same length")
        return text, rep, None
    rep["placed"] = len([it for it in spare if it["id"] in now])
    rep["ids"] = sorted(it["id"] for it in spare if it["id"] in now)
    if not rep["placed"]:
        return text, rep, None
    return text2, rep, comp2
