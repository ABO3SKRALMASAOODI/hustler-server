"""Finding the moments: a model reads each source's transcript for where it speaks of a line's thing,
then a vision check (gpt-6-sol, low) looks at numbered frames from those moments and from the source's
shots and keeps only what plainly shows what the line says."""
import json
import os

from PIL import Image, ImageDraw, ImageFont

from . import ask

CELL_W, CELL_H = 384, 216
COLS, ROWS = 4, 5
PER_SHEET = COLS * ROWS
PER_SOURCE = 36            # frames one source may put on the sheets

MOMENTS_SYSTEM = """You help a film editor find moments in source videos. You get one subject, the film's lines that need it (each with what its picture should show), and the timed transcripts of the videos found for it (each piece is [start-end seconds] text).

Name the places in each video where the picture is most likely to show what a line needs: where the person himself speaks about it on camera, where a report shows the place or the event, where the narration describes what is on screen. Prefer moments where the subject itself would be on screen.

Answer with one JSON object only: {"moments":[{"src":video number,"t":seconds,"lines":[line numbers],"why":"at most eight words"}]}, at most 6 per video, best first."""

LOOK_SYSTEM = """You check frames for a documentary editor before he may cut them into a film. Each numbered cell is one frame from a real source video (its source and time are listed). You get the subject, what a frame must show, and the film's lines that need it.

For every cell decide:
- ok: true only when a viewer would accept at once that the frame shows the subject in one of the allowed forms, large enough to read on a phone, and nothing in it contradicts the subject. You do not recognise faces: judge by the source's title and channel, what is said there, and what the frame plainly shows (a name on screen, a livery, a sign, the setting, a person where that person should be).
- clean: false when the frame carries another editor's captions or subtitles, a burned-in timecode, a stock library's preview mark or web address, a big watermark, a split screen, a reaction face, a channel's title card, or is mostly text. A news channel's small corner logo or lower third is acceptable.
- grade: 1 to 5, 5 for a frame you would lead a film with (sharp, well lit, the subject large and plain).
- lines: the line numbers it would carry best (it may carry none).
- shows: at most eight plain words.
- x: where the subject's centre sits across the frame, 0 left to 1 right (a vertical crop is centred there).
- trim: [top, bottom], the fractions of the frame's height (0 to 0.3) to cut away so that a timecode, a web address or a small logo sitting in a band along the top or bottom edge is gone. When such a band is the frame's only mark, give the trim and judge clean on what is left. The film shows a vertical 9:16 slice around x, so a mark in a corner outside that slice does not count. [0, 0] when nothing needs cutting.

Answer with one JSON object only: {"cells":[{"n":1,"ok":true,"clean":true,"grade":4,"lines":[3],"shows":"...","x":0.5,"trim":[0,0]}, ...]} with one entry per cell."""


BRIEF_CELL_W, BRIEF_CELL_H = 768, 432
BRIEF_PER_SHEET = 8        # with a writer's brief a sheet carries fewer, larger cells: the version and the moment must read

LOOK_BRIEF = """

The film's lines carry the writer's brief: for each line, the things it names ("see"), each with the version or time it means ("as of") and the moment the line tells. For every line a cell carries (each number in its "lines"), also return one entry in "line_checks": {"line": line number, "version_ok": bool, "time_ok": bool, "exact_thing": bool}, meaning:
- "exact_thing": the frame shows the very thing the line is about (that person, that film, that product, that place, or the work itself), not a lookalike, a sequel or another film, a namesake, a toy, sculpture, ride or museum version, a fan remake.
- "time_ok": false only when the frame plainly shows another time than the line means (a later building, a later film's characters, a person decades away from the moment the line tells); true when a viewer cannot tell.
- "version_ok": false only when the line's words set one version against another and the frame plainly is the other one (a test reel under words about the finished film, the finished film under words about an early version, a later model); when the words only name the work, any version of it carries them.
A line belongs in "lines" only when all three are true."""


def _brief_text(ln):
    """A line's brief items after its words: (thing, as of, moment), plain text."""
    out = []
    for x in ln.get("see") or []:
        if isinstance(x, dict) and x.get("thing"):
            bits = [str(x["thing"])] + [f"{k}: {x[v]}" for k, v in (("as of", "as_of"), ("moment", "moment")) if x.get(v)]
            out.append("; ".join(bits))
    return ("  [see: " + " | ".join(out) + "]") if out else ""


def _listing(lines):
    return "\n".join(f"{ln['i']}. {ln['say']}  (show: {ln.get('show') or '-'})" + _brief_text(ln) for ln in lines)


def has_brief(lines):
    return any(ln.get("see") for ln in lines)


def moments(subject, lines, sources, budget_s=90.0):
    """([{src, t, lines, why}], usd). sources: [{title, channel, transcript: [{t0,t1,text}]}]."""
    blocks = []
    for k, s in enumerate(sources):
        tr = [f"[{p['t0']:.0f}-{p['t1']:.0f}] {p['text']}" for p in s.get("transcript") or [] if p.get("text")]
        if tr:
            blocks.append(f"Video {k}: {s['title']} ({s.get('channel') or '?'})\n" + "\n".join(tr)[:14000])
    if not blocks:
        return [], 0.0
    ls = _listing(lines)
    content = f"Subject: {subject['name']} (must show: {subject['must_show']})\n\nLines:\n{ls}\n\n" + "\n\n".join(blocks)
    txt, usd = ask.ask(MOMENTS_SYSTEM, content, "footage_moments", budget_s=budget_s, max_tokens=3000)
    out = []
    for m in ask.json_obj(txt).get("moments") or []:
        try:
            out.append(dict(src=int(m["src"]), t=float(m["t"]), lines=[int(x) for x in m.get("lines") or []], why=str(m.get("why") or "")[:80]))
        except (KeyError, TypeError, ValueError):
            continue
    return [m for m in out if 0 <= m["src"] < len(sources)], usd


LONG_SHOT_S = 6.0          # a long take (a drive, an interview) is looked at every LONG_STEP_S, not once


def points(shots, long_step=4.0, min_s=0.8):
    """A look point in every shot's middle, and every long_step seconds along a long take (shots of min_s and more)."""
    out = []
    for a, b in shots:
        if b - a < min_s:
            continue
        if b - a <= LONG_SHOT_S:
            out.append((round(a + (b - a) * 0.5, 2), (a, b)))
        else:
            n = int((b - a) // long_step)
            out += [(round(a + (b - a) * (k + 0.5) / n, 2), (a, b)) for k in range(n)]
    return out


def pick_times(shots, said, cap=PER_SOURCE, around_s=8.0, moving=None, min_s=0.8):
    """Frame times for one source: every look point inside the moments the transcript pointed at, then an
    even spread of the others (a source's best picture is often under words about something else).
    moving (a set of shots measured as moving, footage.motion; a caller asking for motion first): shots of min_s and more
    are looked at; the moments' points are taken moving first, still ones at most a third of the cap; the room left is
    filled with moving points first, spread evenly over the moving set, then still points. Absent, exactly as before."""
    if moving is not None:
        return _pick_moving(shots, said, cap, around_s, moving, min_s)
    pts = points(shots, min_s=min_s)
    near = []
    for t in said:
        for p, _ in pts:
            if abs(p - t) < around_s and p not in near:
                near.append(p)
    near = near[:max(cap * 2 // 3, cap - 8)]
    rest = [p for p, _ in pts if p not in near]
    room = cap - len(near)
    if room > 0 and rest:
        step = max(1, len(rest) // room)
        near += rest[::step][:room]
    return sorted(near)


def _spread(xs, n):
    """n items spread evenly over xs (all of them when n covers it)."""
    if n <= 0 or not xs:
        return []
    if n >= len(xs):
        return list(xs)
    return [xs[int(k * len(xs) / n)] for k in range(n)]


def _pick_moving(shots, said, cap, around_s, moving, min_s):
    pts = points(shots, min_s=min_s)
    mv = {p for p, sh in pts if tuple(sh) in moving}
    near_m, near_s = [], []
    for t in said:
        for p, _ in pts:
            if abs(p - t) < around_s and p not in near_m and p not in near_s:
                (near_m if p in mv else near_s).append(p)
    lim = max(cap * 2 // 3, cap - 8)
    near = near_m[:lim]
    near += near_s[:max(0, min(cap // 3, lim - len(near)))]
    room = cap - len(near)
    rest_m = [p for p, _ in pts if p in mv and p not in near]
    rest_s = [p for p, _ in pts if p not in mv and p not in near]
    take_m = _spread(rest_m, room)
    near += take_m
    near += _spread(rest_s, cap - len(near))
    return sorted(near)


def sheet(cells, path, cell=(CELL_W, CELL_H)):
    """cells: [(n, jpg)] -> one numbered sheet."""
    CELL_W, CELL_H = cell
    rows = (len(cells) + COLS - 1) // COLS
    im = Image.new("RGB", (COLS * (CELL_W + 6), rows * (CELL_H + 6)), (16, 16, 16))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
    for i, (n, p) in enumerate(cells):
        x, y = (i % COLS) * (CELL_W + 6), (i // COLS) * (CELL_H + 6)
        with Image.open(p) as f:
            f = f.convert("RGB")
            f.thumbnail((CELL_W, CELL_H))
            im.paste(f, (x + (CELL_W - f.width) // 2, y + (CELL_H - f.height) // 2))
        d.rectangle([x, y, x + 44, y + 28], fill=(255, 220, 0))
        d.text((x + 5, y + 2), str(n), fill=(0, 0, 0), font=font)
    im.save(path, quality=85)
    return path


def check(subject, lines, cands, sources, work, budget_s=120.0):
    """cands: [{src, t, jpg, said}] -> (the same with ok, clean, grade, lines, shows, x; usd).
    Lines carrying the writer's brief (see: thing, as_of, moment; only the stories skill sends it): the listing names each
    line's brief, the model also answers per line it gives a cell (version_ok, time_ok, exact_thing, LOOK_BRIEF), a
    cell keeps only the lines where all three are true, and a sheet holds BRIEF_PER_SHEET cells at BRIEF_CELL_W x
    BRIEF_CELL_H. Without a brief, exactly as before."""
    import concurrent.futures as cf
    brief = has_brief(lines)
    ls = _listing(lines) if brief else "\n".join(f"{ln['i']}. {ln['say']}  (show: {ln.get('show') or '-'})" for ln in lines)
    per = BRIEF_PER_SHEET if brief else PER_SHEET
    system = LOOK_SYSTEM + LOOK_BRIEF if brief else LOOK_SYSTEM
    groups = [cands[i:i + per] for i in range(0, len(cands), per)]

    def one(gi):
        g = groups[gi]
        sp = sheet([(j + 1, c["jpg"]) for j, c in enumerate(g)], os.path.join(work, f"sheet_{_slug(subject['name'])}_{gi}.jpg"),
                   **({"cell": (BRIEF_CELL_W, BRIEF_CELL_H)} if brief else {}))
        listing = "\n".join(f"cell {j + 1}: video {c['src']} \"{sources[c['src']]['title'][:90]}\" ({sources[c['src']].get('channel') or '?'}), "
                            f"at {c['t']:.1f} s" + (f"; said there: \"{c['said'][:140]}\"" if c.get("said") else "") for j, c in enumerate(g))
        content = [{"type": "text", "text": f"Subject: {subject['name']}\nA frame must show: {subject['must_show']}\n\nLines:\n{ls}\n\n{listing}"},
                   ask.image_block(sp)]
        txt, usd = ask.ask(system, content, "footage_look", budget_s=budget_s, max_tokens=4000)
        got = {}
        for c in ask.json_obj(txt).get("cells") or []:
            if isinstance(c, dict) and isinstance(c.get("n"), int):
                got[c["n"]] = c
        out = []
        for j, c in enumerate(g):
            v = got.get(j + 1) or {}
            try:
                x = min(max(float(v.get("x", 0.5)), 0.0), 1.0)
            except (TypeError, ValueError):
                x = 0.5
            try:
                trim = [min(max(float(t), 0.0), 0.3) for t in (v.get("trim") or [0, 0])[:2]]
                trim += [0.0] * (2 - len(trim))
            except (TypeError, ValueError):
                trim = [0.0, 0.0]
            got_lines = [int(n) for n in v.get("lines") or [] if isinstance(n, int)]
            extra = {}
            if brief:
                checks = {}
                for lc in v.get("line_checks") or []:
                    if isinstance(lc, dict) and isinstance(lc.get("line"), int):
                        checks[lc["line"]] = all(lc.get(k) is True for k in ("version_ok", "time_ok", "exact_thing"))
                extra = dict(lines_offered=got_lines)
                got_lines = [n for n in got_lines if checks.get(n) is True]
            out.append(dict(c, ok=v.get("ok") is True, clean=v.get("clean") is not False, trim=[round(t, 3) for t in trim], grade=int(v.get("grade") or 0) if str(v.get("grade") or "0").isdigit() else 0,
                            lines=got_lines, shows=str(v.get("shows") or "")[:80], x=round(x, 3), sheet=os.path.basename(sp), **extra))
        return out, usd
    res, usd = [], 0.0
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for out, u in pool.map(one, range(len(groups))):
            res += out
            usd += u
    return res, usd


def said_at(transcript, t):
    for p in transcript or []:
        if p["t0"] <= t < p["t1"]:
            return p["text"]
    return ""


def _slug(s):
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))[:32]


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
