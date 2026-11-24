"""Type that performs: figures that count, names in two sizes, words that whisper and shout, a marker that sweeps a
word on its hit, text typed or unredacted on the voice, a chapter page, a run of one word a panel, a marquee larger
than the frame, per-word verbs, and a caption line read along.

Every device here gives back plain scene items (words, flat rectangles, plate shots) that the engine already draws.
Which words, which figure (from, to, its format), which name and title come from the caller: a model that read the
script. The machine's part is signal: the frame each word is said (the film's own word clock), the size a line
stands at, the seat off the faces, the captions and the watermark, and the ink the measured ground reads.
"""
import math

from . import kit as K

# --------------------------------------------------------------------------- shared


def _base(l1, work, ask, sense):
    o = K.opts(ask)
    W, H, fps, dur = K.size(l1)
    K.scaled(l1)
    return o, W, H, fps, dur, K.Sense(l1, work, sense), K.palette_of(l1, o)


def text_of(o):
    t = o.get("t") or o.get("text") or o.get("name") or o.get("word")
    if not t and o.get("lines"):
        t = " ".join(str(x.get("t") if isinstance(x, dict) else x) for x in o["lines"])
    if not t and o.get("words"):
        t = " ".join(str(x.get("t") if isinstance(x, dict) else x) for x in o["words"])
    if not t and o.get("to") is not None:
        try:
            t = fmt_value(float(o["to"]), o, final=True)
        except (TypeError, ValueError):
            t = None
    return " ".join(str(t).split()) if t else ""


def cased(t, o, default=None):
    c = o.get("case", default)
    return t.upper() if c == "upper" else t.lower() if c == "lower" else t


def vname(ask, k):
    return f"pal.{ask['id']}.{k}"


def _ink(S, box, a, z, pal, prefer=None):
    tone, _ = S.ground(box, a, z)
    want = prefer or pal["ink"]
    if tone > 0.62:
        return K.DARK, tone
    if prefer and K.lum(prefer) < 0.3 and tone < 0.35:
        return pal["ink"], tone
    return want, tone


def _marks(f, what, strength=1.0):
    return [{"f": int(f), "what": what, "kind": "type", "strength": round(float(strength), 2)}]


# --------------------------------------------------------------------------- counter

UNITS = ((1e12, "T", " TRILLION"), (1e9, "B", " BILLION"), (1e6, "M", " MILLION"), (1e3, "K", " THOUSAND"))


def fmt_value(v, o, final=False):
    """A figure through the caller's format. ``unit``: "short" (285M, 4.3B), "words" (4.3 BILLION) or none (1,200);
    ``fmt`` puts the number in its sentence ("${n}", "{n} MILES"); ``dec`` fixes the decimals."""
    unit = o.get("unit")
    dec = o.get("dec")
    neg = v < 0
    a = abs(v)
    suffix = ""
    if unit in ("short", "auto", "words"):
        for s, short, word in UNITS:
            if a >= s * 0.9995:
                a /= s
                suffix = short if unit != "words" else word
                break
    if dec is None:
        whole = abs(a - round(a)) < 1e-6
        d = 0 if (whole and (final or a >= 100)) or a >= 100 else (1 if a < 100 and (suffix or not whole) else 0)
        if not suffix and not unit and all(float(o.get(k, 0) or 0) == int(float(o.get(k, 0) or 0)) for k in ("from", "to")):
            d = 0
    else:
        d = int(dec)
    sep = o.get("sep")
    if sep is None:
        sep = not suffix and max(abs(float(o.get("from", 0) or 0)), abs(float(o.get("to", 0) or 0))) >= 10000
    num = f"{a:,.{d}f}" if sep else f"{a:.{d}f}"
    num = ("-" if neg else "") + num + suffix
    f = str(o.get("fmt") or "{n}")
    return f.replace("{n}", num) if "{n}" in f else f + num


def counter(l1, work, ask, sense=None):
    """Digits that step hard and slow into the spoken figure, on one seat across every cut, then hold and hit."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    try:
        v0, v1 = float(o["from"]), float(o["to"])
    except (KeyError, TypeError, ValueError):
        return {"skip": "counter needs from and to"}
    hold = int(o.get("hold", 30))
    say = o.get("t") or o.get("lands")
    a, z, hit = K.span(l1, ask, need=hold, text=say if isinstance(say, str) else None)
    land = K.frame_of(l1, o.get("lands"), near=a) if o.get("lands") is not None and not isinstance(o.get("lands"), str) else None
    run = int(o.get("run", 18))
    if land is not None and (land < a - run or land > z + 3 * hold):
        land = None                # a figure word far outside its own place (a word number read as a frame): the place's own hit
    if land is None:
        land = hit[0][1] if hit else a + max(min(int(0.6 * (z - a)), 28), 8)
    if land - a < 8:
        a = max(0, land - run)
    z = max(z, min(land + hold, dur))
    z = min(K.bounded(o, z, land + 8), dur)                # the row's window ends it; the figure still lands and holds a beat
    if z <= land:
        land = max(a + 2, z - 4)
    span_f = land - a
    k = int(min(max(span_f // 2.2, 4), 28))
    steps = [a + s for s in K.hard_steps(span_f, k)] + [land]
    steps = sorted(set(steps))
    geo = v0 > 0 and v1 > 0 and max(v0, v1) / min(v0, v1) >= 8
    vals = []
    for j in range(len(steps)):
        p = j / max(len(steps) - 1, 1)
        vals.append(v0 * (v1 / v0) ** p if geo else v0 + (v1 - v0) * p)
    texts = [cased(fmt_value(v, o, final=(j == len(vals) - 1)), o) for j, v in enumerate(vals)]
    font = pal["font"]      # palette_of resolves the ask's font word
    v = K.voice(font, 100, track=int(o.get("track", -1)), shadow=0.5)
    wide = max(texts, key=lambda t: K.ink_box(v, t)[1][2] - K.ink_box(v, t)[1][0])
    v = K.fit(v, wide, float(o.get("width", 0.80)) * W, cap_px=float(o.get("cap", 0.17)) * H)
    _, b = K.ink_box(v, wide)
    bw, bh = (b[2] - b[0]) / W, (b[3] - b[1]) / H
    label = cased(str(o.get("label") or ""), o, "upper").strip()
    lv = None
    lh = 0.0
    if label:
        lv = K.fit(K.mono(10), label, min(0.62 * bw * W, K.ink_box(K.mono(46), label)[1][2] * 1.0 + 1), cap_px=K.px(48))
        lv = dict(lv, px=max(lv["px"], K.px(40)))
        lh = (K.ink_box(lv, label)[1][3] - K.ink_box(lv, label)[1][1]) / H + 0.018
    if o.get("seat"):
        cx, base = [float(q) for q in o["seat"]]
    else:
        x0, base = K.seat(l1, S, a, z, bw, bh + lh, prefer=float(o.get("prefer", 0.36)))
        cx = x0 + bw / 2
        base = base
    box = [cx - bw / 2, base - bh, cx + bw / 2, base]
    ink, tone = _ink(S, box, a, z, pal)
    hit_ink = pal["accent"] if tone < 0.55 and o.get("land_ink", "accent") == "accent" else ink
    vn = vname(ask, "n")
    items = []
    for j, t in enumerate(texts):
        f0 = steps[j]
        f1 = steps[j + 1] if j + 1 < len(steps) else z
        if f1 <= f0:
            continue
        x, y = K.place(v, t, cx, base, W, H)
        it = K.word(f"{ask['id']}.c{j:02d}", (f0, f1), t, vn, x, y, ink if j < len(texts) - 1 else hit_ink, z=8, role="big", on=f0)
        if j == len(texts) - 1:
            it["s"] = K.punch(f1 - f0, size=float(o.get("punch", 0.14)))
        items.append(it)
    voices = {vn: v}
    if label:
        ln = vname(ask, "l")
        voices[ln] = lv
        lx, ly = K.place(lv, label, cx, base - bh - 0.016, W, H)
        items.append(K.word(f"{ask['id']}.lab", (a, z), label, ln, lx, ly, ink, z=8, role="big", on=a))
    if o.get("underline", True):
        x, y = K.place(v, texts[-1], cx, base, W, H)
        fb = K.block_of(v, texts[-1], x, y, W, H)
        n = z - land
        grow = K.ease_out(n, 0.0, 1.0, frames=6)
        th = max(0.0045, 0.06 * bh)
        r = [[round(fb[0], 5), round(base + 0.012, 5), round(fb[0] + (fb[2] - fb[0]) * g, 5), round(base + 0.012 + th, 5)] for g in grow]
        items.append(K.rect(f"{ask['id']}.u", (land, z), r if n > 1 else r[0], hit_ink, a=1.0))
    return {"items": items, "voices": voices, "marks": _marks(land, "counter lands", 1.0),
            "seat": {"x": cx, "y": base, "w": bw, "h": bh, "f": [a, z]},
            "log": {"steps": len(texts), "from": texts[0], "to": texts[-1], "lands": land, "seat": [round(cx, 4), round(base, 4)], "px": v["px"]}}


# --------------------------------------------------------------------------- staircase and the two-size stack

def handoff_route(group, span, steps, *, width, height, voice, ink, accent):
    """Lay out a connected route once. Move the signal to the receiving station on its cue."""
    start,end = span
    name = group + ".label"
    face = dict(voice, px=round(width*.043), track=0)
    items, voices = [], {name:face}
    # Wrap whole stations at their shaped widths. A long route continues on the next
    # page at a spoken cue; it never shrinks the earlier steps into unreadable type.
    fitted = []
    for cue,label in steps:
        v = K.fit(face,label,.83*width,cap_px=face["px"],floor_px=1)
        fitted.append((cue,label,K.ink_box(v,label)[0]/width,v))
    room = .83-sum(w for _,_,w,_ in fitted)
    gap = max(.042,min(.085,room/max(1,len(fitted)-1)))
    lines = [[]]
    x = .085
    for cue,label,w,v in fitted:
        if lines[-1] and x+w > .92:
            lines.append([])
            x = .085
        lines[-1].append((cue,label,x,w,v))
        x += w+gap
    for i in range(0,len(lines),2):
        page = lines[i:i+2]
        a = page[0][0][0] if i else start
        b = lines[i+2][0][0] if i+2 < len(lines) else end
        previous = None
        stations = [(cue,label,x if row_index == 0 else 1-x-w,w,v,.63+.083*row_index)
                    for row_index,row in enumerate(page) for cue,label,x,w,v in row]
        for j,(cue,label,x,w,v,y) in enumerate(stations):
            ident = f"{group}.{i}.{j}"
            font = name
            if v["px"] != face["px"]:
                font = ident+".font"
                voices[font] = v
            attrs = dict(text_group=group,fact_type=True)
            line_y = y+.013
            after = stations[j+1][0] if j+1 < len(stations) else b
            opacity = [.58]*(cue-a) + [1.]*(after-cue) + [.82]*(b-after)
            items.append(K.word(ident,[a,b],label,font,x,y,ink,op=opacity,**attrs))
            items.append(K.rect(ident+".rail",[a,b],[x,line_y,x+w,line_y+.0035],ink,.5,z=6))
            if cue < b:
                items.append(K.rect(ident+".traversed",[cue,b],[x,line_y,x+w,line_y+.0035],accent,.9,z=6))

            if previous:
                old_x,old_y,old_edge = previous
                if old_y == line_y:
                    left,right = sorted((old_edge,x if old_x < x else x+w))
                    items.append(K.rect(ident+".link",[a,b],[left,line_y,right,line_y+.0025],ink,.65,z=6))
                else:
                    # The return bends outside the labels, from the outgoing row to the incoming one.
                    bend = .948
                    items.extend([K.rect(ident+".exit",[a,b],[old_edge,old_y,bend,old_y+.0025],ink,.65,z=6),
                                  K.rect(ident+".bend",[a,b],[bend-.0025*height/width,old_y,bend,line_y],ink,.65,z=6),
                                  K.rect(ident+".entry",[a,b],[x+w,line_y,bend,line_y+.0025],ink,.65,z=6)])

            cx = x+w/2
            if cue < after:
                n = min(8,after-cue)
                ox,oy = previous[:2] if previous else (cx,line_y)
                def travel(old,new):
                    return [round(old+(new-old)*t/n,5) for t in range(1,n+1)]+[new]*(after-cue-n)
                items.append(dict(id=ident+".signal",k="card",pic="plate",f=[cue,after],z=7,
                                  plate=dict(c0=accent,c1=accent,grain=0),w=.024,h=.014,
                                  x=travel(ox,cx),y=travel(oy,line_y),handoff_marker=True))
            previous = cx,line_y,x+w if y == .63 else x
    return dict(items=items,voices=voices)


def staircase(l1, work, ask, sense=None):
    """A figure as a two-size stack: each line lands on the word that says it, small lines carrying the big one,
    stepping across like stairs when there are three or more."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    lines = o.get("lines")
    if not lines:
        t = text_of(o)
        if not t:
            return {"skip": "staircase needs lines"}
        key = str(o.get("key") or "").strip()
        ws = t.split()
        if key and K.norm(key) in [K.norm(w) for w in ws]:
            i = [K.norm(w) for w in ws].index(K.norm(key))
            lines = [x for x in ([" ".join(ws[:i]), "small"], [ws[i], "big"], [" ".join(ws[i + 1:]), "small"]) if x[0]]
            lines = [{"t": x[0], "size": x[1]} for x in lines]
        else:
            lines = [{"t": t, "size": "big"}]
    lines = [x if isinstance(x, dict) else {"t": str(x)} for x in lines]
    lines = [dict(x, t=cased(" ".join(str(x.get("t") or "").split()), o, "upper")) for x in lines if str(x.get("t") or "").strip()]
    if not lines:
        return {"skip": "staircase needs lines"}
    full = " ".join(x["t"] for x in lines)
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 36)), text=full)
    font = pal["font"]      # palette_of resolves the ask's font word
    big = [i for i, x in enumerate(lines) if x.get("size", "big") == "big"] or [0]
    vb = K.voice(font, 100, track=-1, shadow=0.5)
    wid = max((lines[i]["t"] for i in big), key=lambda t: K.ink_box(vb, t)[1][2])
    vb = K.fit(vb, wid, float(o.get("width", 0.80)) * W, cap_px=0.15 * H)
    vs = K.mono(max(K.FLOOR_PX + 6, round(vb["px"] * 0.30 / K.SCALE, 1)))
    supporting = [line["t"] for i,line in enumerate(lines) if i not in big]
    if supporting and any(it.get("panel_type") == ask["id"] for it in l1["items"]):
        # In the dedicated panel both sides carry the argument. The supporting
        # line gets the display face and enough ink to read alongside its partner.
        longest = max(supporting, key=lambda text: K.ink_box(vb, text)[0])
        vs = K.fit(vb, longest, .80*W, cap_px=max(58*K.SCALE, .58*vb["px"]))
    stairs = bool(o.get("stairs", len(lines) >= 3))
    rows, y, wmax = [], 0.0, 0.0
    for i, x in enumerate(lines):
        vv = vb if i in big else vs
        _, b = K.ink_box(vv, x["t"])
        hpx = (b[3] - b[1]) / H
        y += hpx + (0.012 if rows else 0.0)
        off = (0.05 * i) if stairs else 0.0
        rows.append((x, vv, y, off, (b[2] - b[0]) / W))
        wmax = max(wmax, off + (b[2] - b[0]) / W)
    bh = y
    x0, base = K.seat(l1, S, a, z, wmax, bh, prefer=float(o.get("prefer", 0.36)), align="l" if stairs else "c")
    top = base - bh
    land = K.times(l1, " ".join(r[0]["t"].split()[0] for r in rows), a, z, [(h[0], h[1]) for h in hit]) if hit else []
    items, voices = [], {}
    box = [x0, top, x0 + wmax, base]
    ink, tone = _ink(S, box, a, z, pal)
    for i, (x, vv, ybot, off, wd) in enumerate(rows):
        f0 = K.frame_of(l1, x.get("at"), near=a) if x.get("at") is not None else None
        if f0 is None:
            sub = K.find(l1, x["t"], near=a)
            f0 = sub[0][1] if sub else (a + 3 * i)
        f0 = min(max(f0, a), z - 2)
        vn = vname(ask, f"r{i}")
        voices[vn] = vv
        if stairs:
            px, py = K.place(vv, x["t"], x0 + off, top + ybot, W, H, align="l")
        else:
            px, py = K.place(vv, x["t"], x0 + wmax / 2, top + ybot, W, H)
        n = z - f0
        ylist = [round(py + d, 6) for d in K.ease_out(n, 0.014, 0.0, frames=5)]
        ink_i = pal["accent"] if (i in big and tone < 0.55) else ink
        items.append(K.word(f"{ask['id']}.r{i}", (f0, z), x["t"], vn, px, ylist, ink_i, z=8, role="big", on=f0,
                            op=[round(min(1.0, (q + 1) / 2), 3) for q in range(n)]))
    lands = sorted(it["f"][0] for it in items)
    return {"items": items, "voices": voices, "marks": [m for f in lands for m in _marks(f, "stair lands", 0.6)],
            "seat": {"x": x0, "y": base, "w": wmax, "h": bh, "f": [a, z]}, "log": {"lines": [r[0]["t"] for r in rows], "stairs": stairs}}


# --------------------------------------------------------------------------- the name super, in two sizes

def name(l1, work, ask, sense=None):
    """A name lands in two sizes: a small line (who they are) types on, the name slams on the word that says it."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    nm = " ".join(str(o.get("name") or o.get("t") or "").split())
    title = o.get("title") or ""
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 48)), text=nm)
    land = hit[0][1] if hit else a
    evidence = None
    if "documentary_cuts" in l1:
        # Use the selected excerpt's reading for both lines of this super.
        # The Director supplies typography and timing; its guessed name is never a credit.
        for row in [*l1["documentary_cuts"], *l1.get("interview_credits", [])]:
            if row.get("name_super") and (row.get("source_evidence") or {}).get("url") and row["f"][0] <= a < row["f"][1]:
                spans = K.picture_spans(l1, row["pic"], a, min(z, row["f"][1]))
                if spans and spans[0][0] == a and spans[0][1] - a >= 2:
                    evidence = row
                    a, z = spans[0]
                    break
        land = a
        if evidence:
            nm, _, title = evidence["name_super"].partition(",")
        else:
            # Only perform lettering the Director authored and the speaker actually says here.
            # Otherwise carry the picture through this cue. Captions already have the speech.
            words = [w for w in K.said(l1) if w[1] < z and w[2] > a]
            for phrase in (nm, title):
                hit = K.find(l1, phrase, near=a, words=words)
                if hit and [K.norm(w) for w, _ in hit] == [K.norm(w) for w in phrase.split()]:
                    nm = " ".join(w for w, _ in hit)
                    a = land = max(a, hit[0][1])
                    z = min(z, next(w[2] for w in words if w[1] == hit[-1][1]))
                    if z > a:
                        break
            else:
                from .rhythm import settle
                return settle(l1, work, ask, sense)
            title = ""
    else:
        a = max(0, min(a, land - 4))
    if not nm:
        return {"skip": "name needs a name"}
    nm = cased(nm.strip(), o, "upper")
    title = cased(" ".join(str(title).split()), o, "upper")
    rng = K.rng_of(l1, ask)
    layout = o.get("layout") or rng.choice(["stack", "left", "split"])
    ws = nm.split()
    if layout == "split" and len(ws) < 2:
        layout = "stack"
    font = pal["font"]      # palette_of resolves the ask's font word
    rows = []
    if layout == "split":
        rows.append((" ".join(ws[:-1]), "small"))
        rows.append((ws[-1], "big"))
    else:
        rows.append((nm, "big"))
    if title:
        rows.insert(0, (title, "title"))
    vb = K.voice(font, 100, track=-1, shadow=0.5)
    bigt = next(t for t, k in rows if k == "big")
    cap_px = float(o.get("cap", 0.13)) * H
    if evidence and evidence in l1.get("interview_credits", []):
        caption_px = (l1["voices"].get("cap") or {}).get("px", .035 * H)
        cap_px = min(cap_px, caption_px * 1.2)
    vb = K.fit(vb, bigt, float(o.get("width", 0.84 if layout != "left" else 0.76)) * W, cap_px=cap_px)
    vt = K.mono(max(K.FLOOR_PX + 2, min(46.0, round(vb["px"] * 0.30 / K.SCALE, 1))))
    vs = K.voice(font, max(K.px(K.FLOOR_PX + 10), round(vb["px"] * 0.42, 1)), track=4, shadow=0.5)
    vv = {"big": vb, "title": vt, "small": vs}
    hs, ws_ = [], []
    for t, kind in rows:
        _, b = K.ink_box(vv[kind], t)
        hs.append((b[3] - b[1]) / H)
        ws_.append((b[2] - b[0]) / W)
    gap = 0.014
    bh = sum(hs) + gap * (len(rows) - 1)
    bw = max(ws_) + (0.03 if layout == "left" else 0.0)
    x0, base = K.seat(l1, S, a, z, bw, bh, prefer=float(o.get("prefer", 0.62 if layout == "left" else 0.40)),
                      align="l" if layout == "left" else "c")
    top = base - bh
    ink, tone = _ink(S, [x0, top, x0 + bw, base], a, z, pal)
    acc = pal["accent"] if tone < 0.55 else K.DARK
    items, voices, y = [], {}, top
    for i, (t, kind) in enumerate(rows):
        y += hs[i]
        vn = vname(ask, f"{kind}{i}")
        voices[vn] = vv[kind]
        if layout == "left":
            px, py = K.place(vv[kind], t, x0 + 0.03, y, W, H, align="l")
        else:
            px, py = K.place(vv[kind], t, x0 + bw / 2, y, W, H)
        if kind == "title":
            n = max(min(len(t), 10, z - a), 1)                          # the small line types on, a letter or two a frame
            t0 = max(a, land - n - 2)
            for q in range(n):
                k = max(1, round(len(t) * (q + 1) / n))
                f1 = t0 + q + 1 if q < n - 1 else z
                items.append(K.word(f"{ask['id']}.t{q:02d}", (t0 + q, f1), t[:k], vn, px, py, acc, z=8, role="big", on=t0))
        else:
            f0 = land if kind == "big" else max(a, land - 2)
            n = z - f0
            it = K.word(f"{ask['id']}.{kind}{i}", (f0, z), t, vn, px, py, ink, z=8, role="big", on=f0)
            if kind == "big":
                it["s"] = K.punch(n, size=0.10, tau=2.8)
            items.append(it)
        y += gap
    if layout == "left":
        bar_start = max(a, land - 2)
        n = z - bar_start
        grow = K.ease_out(n, 0.0, 1.0, frames=6)
        items.append(K.rect(f"{ask['id']}.bar", (bar_start, z), [[round(x0, 5), round(top, 5), round(x0 + 0.009, 5), round(top + bh * g, 5)] for g in grow],
                            acc, a=1.0))
    receipt = {}
    if evidence:
        receipt = dict(pic=evidence["pic"], source=evidence["source_evidence"],
                       time_range=evidence.get("time_range"), source_slice=evidence.get("source_slice"))
        for item in items:
            item["name_picture"] = evidence["pic"]
    return {"items": items, "voices": voices, "marks": _marks(land, "name lands", 0.9),
            "seat": {"x": x0, "y": base, "w": bw, "h": bh, "f": [a, z]}, "log": {"layout": layout, "rows": [r[0] for r in rows], "lands": land, **receipt}}


# --------------------------------------------------------------------------- the word ladder: whisper and shout

def ladder(l1, work, ask, sense=None):
    """Whisper/shout pairs: the small words come in as he says them, the shout lands huge on its word, one accent,
    brackets when asked."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    rows = o.get("words") or o.get("lines")
    if not rows:
        t = text_of(o)
        if not t:
            return {"skip": "ladder needs words"}
        key = str(o.get("key") or t.split()[-1])
        ws = t.split()
        kn = [K.norm(w) for w in ws]
        i = kn.index(K.norm(key)) if K.norm(key) in kn else len(ws) - 1
        rows = [x for x in ({"t": " ".join(ws[:i]), "w": "whisper"}, {"t": ws[i], "w": "shout"}, {"t": " ".join(ws[i + 1:]), "w": "whisper"}) if x["t"]]
    rows = [r if isinstance(r, dict) else {"t": str(r)} for r in rows]
    rows = [dict(r, t=" ".join(str(r.get("t") or "").split())) for r in rows if str(r.get("t") or "").strip()]
    if not rows:
        return {"skip": "ladder needs words"}
    for r in rows:
        r.setdefault("w", "shout" if len(r["t"]) <= 10 and r is rows[-1] else "whisper")
    if not any(r["w"] == "shout" for r in rows):
        max(rows, key=lambda r: len(r["t"]) <= 12)["w"] = "shout"
    full = " ".join(r["t"] for r in rows)
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 30)), text=full)
    font = pal["font"]      # palette_of resolves the ask's font word
    vsh = K.voice(font, 100, track=-2, shadow=0.55)
    loud = [r for r in rows if r["w"] == "shout"]
    vsh = K.fit(vsh, max((cased(r["t"], o, "upper") for r in loud), key=lambda t: K.ink_box(vsh, t)[1][2]),
                float(o.get("width", 0.86)) * W, cap_px=0.20 * H)
    vwh = K.voice(o.get("whisper_font") or "InterDisplay-Bold.ttf", max(K.px(K.FLOOR_PX + 8), min(K.px(60.0), vsh["px"] * 0.26)), track=6, shadow=0.6)
    brackets = bool(o.get("brackets"))
    vbr = {"font": "Inter-Variable.ttf", "var": {"wght": 200}, "px": round(vsh["px"] * 1.05, 2), "track": 0}
    lay, y = [], 0.0
    for r in rows:
        t = cased(r["t"], o, "upper") if r["w"] == "shout" else cased(r["t"], o, "lower" if o.get("whisper_case") == "lower" else None)
        vv = vsh if r["w"] == "shout" else vwh
        _, b = K.ink_box(vv, t)
        y += (b[3] - b[1]) / H + (0.014 if lay else 0.0)
        lay.append((r, t, vv, y, (b[2] - b[0]) / W))
    bh = y
    bw = max(L[4] for L in lay) + (0.12 if brackets else 0.0)
    x0, base = K.seat(l1, S, a, z, bw, bh, prefer=float(o.get("prefer", 0.36)))
    top = base - bh
    cx = x0 + bw / 2
    ink, tone = _ink(S, [x0, top, x0 + bw, base], a, z, pal)
    acc_i = next((i for i, L in enumerate(lay) if L[0].get("accent")), next(i for i, L in enumerate(lay) if L[0]["w"] == "shout"))
    items, voices, marks = [], {}, []
    for i, (r, t, vv, ybot, wd) in enumerate(lay):
        vn = vname(ask, f"w{i}")
        voices[vn] = vv
        sub = K.find(l1, r["t"], near=a)
        f0 = min(max(sub[0][1] if sub else a + 4 * i, a), z - 2)
        px, py = K.place(vv, t, cx, top + ybot, W, H)
        ink_r = (pal["accent"] if tone < 0.55 else ink) if i == acc_i else ink
        if r["w"] == "shout":
            it = K.word(f"{ask['id']}.w{i}", (f0, z), t, vn, px, py, ink_r, z=8, role="big", on=f0)
            it["s"] = K.punch(z - f0, size=0.15, tau=2.2)
            items.append(it)
            marks += _marks(f0, "shout lands", 1.0)
            if brackets:
                voices[vn + "b"] = vbr
                fb = K.block_of(vv, t, px, py, W, H)
                n = z - f0
                for side, ch in (("l", "["), ("r", "]")):
                    _, bb = K.ink_box(vbr, ch)
                    gx = fb[0] - 0.02 - (bb[2] - bb[0]) / W if side == "l" else fb[2] + 0.02
                    bx0 = gx - bb[0] / W
                    by = fb[3] + 0.08 * (fb[3] - fb[1])
                    xs = K.ease_out(n, bx0 + (-0.08 if side == "l" else 0.08), bx0, frames=5)
                    items.append(K.word(f"{ask['id']}.b{side}{i}", (f0, z), ch, vn + "b", xs, round(by, 6), ink_r, z=8, role="big", on=f0))
        else:
            ws = t.split()
            sub_t = K.times(l1, r["t"], f0, z, sub or [])
            for q in range(len(ws)):
                g0 = max(sub_t[q] if q < len(sub_t) else f0 + q * 3, f0)
                g1 = sub_t[q + 1] if q + 1 < len(ws) and q + 1 < len(sub_t) else z
                if g1 <= g0:
                    continue
                items.append(K.word(f"{ask['id']}.w{i}.{q}", (g0, g1), " ".join(ws[:q + 1]), vn, px, py, ink_r, z=8, role="big", on=g0, op=0.92))
    return {"items": items, "voices": voices, "marks": marks, "seat": {"x": x0, "y": base, "w": bw, "h": bh, "f": [a, z]},
            "log": {"rows": [[L[1], L[0]["w"]] for L in lay], "brackets": brackets}}


def brackets(l1, work, ask, sense=None):
    """One word, shouted, with brackets that snap in to hold it."""
    o = K.opts(ask)
    t = text_of(o)
    if not t:
        return {"skip": "brackets needs a word"}
    return ladder(l1, work, dict(ask, args=dict(o, words=[{"t": t, "w": "shout"}], brackets=True)), sense)


# --------------------------------------------------------------------------- a word on screen, marked on its hit

def target(l1, text, a, z, prefer_big=True, role=None):
    """(item, voice, lo, hi): the word item on screen between a and z whose text holds ``text`` (display type first),
    and the phrase's characters in it [lo, hi), widened to whole words (a phrase is marked word by word)."""
    want = K.norm(text)
    if not want:
        return None
    cands = []
    for it in l1.get("items") or []:
        if it.get("k") != "word" or it["f"][1] <= a or it["f"][0] >= z:
            continue
        if role and it.get("role") != role:
            continue
        if want in K.norm(it.get("t")):
            cands.append(it)
    if not cands:
        return None
    cands.sort(key=lambda it: (it.get("role") == "cap" if prefer_big else 0, abs(it["f"][0] - a)))
    it = cands[0]
    v = (l1.get("voices") or {}).get(it.get("v"))
    if not v:
        return None
    full = str(it["t"])
    pos = [i for i, ch in enumerate(full) if ch.isalnum()]
    k = "".join(full[i].lower() for i in pos).find(want)
    lo, hi = (pos[k], pos[k + len(want) - 1] + 1) if k >= 0 else (0, len(full))
    while lo > 0 and not full[lo - 1].isspace():
        lo -= 1
    while hi < len(full) and not full[hi].isspace():
        hi += 1
    return it, v, lo, hi


def _rest(x):
    return float(x[-1] if isinstance(x, list) else x)


def _glyph_span(v, full, lo, hi):
    """The glyphs of characters [lo, hi) of a line, as indices into its shaped glyphs (spaces carry none)."""
    n = lambda t: len(K.metrics(v, t)["glyphs"]) if t else 0      # noqa: E731
    return n(full[:lo]), n(full[:hi])


def _geo(it, v, f, W, H, part=None):
    """(ink box of the line, or of its glyphs ``part``, as drawn on frame f; its scale there) in frame fractions."""
    lo = it["f"][0]
    s = float(K.A(it.get("s", 1), f, lo))
    return K.drawn(v, str(it["t"]), K.A(it.get("x", 0), f, lo), K.A(it.get("y", 0), f, lo), W, H, s=s, part=part), s



def recolour(it, v, lo, hi, ink, f_from, W, H, tag="rc"):
    """The item from ``f_from`` on with only characters [lo, hi) in ``ink``: the line is cut into its words before,
    the phrase and its words after, each laid where the renderer draws it inside the whole line (its pen moved on the
    line's own measured glyphs, and through the line's scale about the line's centre), so nothing moves and only the
    phrase changes ink. Before ``f_from`` the item stands as it was. Returns (items, drop)."""
    full = str(it["t"])
    f0, f1 = it["f"]
    f_from = min(max(int(f_from), f0), f1)
    items, drop = [], []
    if f_from > f0:
        items.append(K.cut(it, f0, f_from))
    else:
        drop.append(it["id"])
    if f_from >= f1:
        return items, drop
    line = K.metrics(v, full)
    gl = line["glyphs"]
    pieces = []
    pre, mid, post = full[:lo], full[lo:hi], full[hi:]
    g0, g1 = _glyph_span(v, full, lo, hi)
    if pre.strip():
        pieces.append((pre.rstrip(), it.get("ink"), 0, "a"))
    pieces.append((mid, ink, g0, "p"))
    if post.strip():
        pieces.append((post.lstrip(), it.get("ink"), g1, "b"))
    rest = K.cut(it, f_from, f1)
    n = f1 - f_from
    for text, col, g, key in pieces:
        pm = K.metrics(v, text)
        if not pm["glyphs"] or g >= len(gl):
            off = K.ink_box(v, full[:max(full.find(text), 0)])[0] if text in full else 0.0
        else:
            off = gl[g][0] - pm["glyphs"][0][0]
        cl, cp = line["c"], pm["c"]
        xs, ys = [], []
        for q in range(n):
            f = f_from + q
            s = float(K.A(it.get("s", 1), f, f0))
            X, Y = float(K.A(it.get("x", 0), f, f0)) * W, float(K.A(it.get("y", 0), f, f0)) * H
            xs.append(round((X + cl[0] - cp[0] + (off - cl[0] + cp[0]) * s) / W, 6))
            ys.append(round((Y + (cl[1] - cp[1]) * (1 - s)) / H, 6))
        pc = dict(rest, id=f"{it['id']}~{tag}{key}", t=text, x=xs if len(set(xs)) > 1 else xs[0], y=ys if len(set(ys)) > 1 else ys[0], ink=col)
        if key == "p" and col != it.get("ink"):
            pc.pop("stroke", None)
        items.append(pc)
    return items, drop


def highlight(l1, work, ask, sense=None):
    """A marker sweeps behind a phrase on its hit, left to right in five frames, measured on the line as it is drawn
    on every frame (its pen, its scale, its voice); only the phrase's ink turns to read on it."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    t = text_of(o)
    if not t:
        return {"skip": "highlight needs a word"}
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 24)), text=t)
    f_hit = hit[0][1] if hit else a
    got = target(l1, t, f_hit, f_hit + 2, role=o.get("target_role"))
    if got is None:
        # Captions can type on a few frames after the spoken word. Seat the
        # marker on the same word's actual entrance.
        got = target(l1, t, f_hit, f_hit + max(3, round(0.22 * fps)), role=o.get("target_role"))
    if got is None:
        return {"skip": f"'{t}' is not on screen there"}
    it, v, lo, hi = got
    f_hit = max(f_hit, it["f"][0])
    part = _glyph_span(v, str(it["t"]), lo, hi)
    style = o.get("style") or K.rng_of(l1, ask).choice(["marker", "box", "under"])
    end = min(it["f"][1], max(z, f_hit + 10))
    n = end - f_hit
    if n < 2:
        return {"skip": "the word leaves before its hit"}
    grow = K.ease_out(n, 0.0, 1.0, frames=5)
    rects = []
    for q in range(n):
        f = f_hit + q
        (X0, top, X1, bot), s = _geo(it, v, f, W, H, part)
        line, _ = _geo(it, v, f, W, H)
        px = float(v["px"]) * s
        pad = 0.12 * px / W
        base = line[3] - (K.metrics(v, str(it["t"]))["ink"][3]) * s / H        # the baseline as drawn
        if style == "under":
            r = [X0 - pad / 2, base + 0.06 * px / H, X1 + pad / 2, base + 0.16 * px / H]
        elif style == "marker":
            r = [X0 - pad, top + 0.38 * (bot - top), X1 + pad, bot + 0.08 * px / H]
        else:
            r = [X0 - pad, top - 0.12 * px / H, X1 + pad, bot + 0.14 * px / H]
        rects.append([round(r[0], 5), round(r[1], 5), round(r[0] + (r[2] - r[0]) * grow[q], 5), round(r[3], 5)])
    col = o.get("c") or pal["accent"]
    items = [K.rect(f"{ask['id']}.hl", (f_hit, end), rects, col, a=float(o.get("a", 1.0)), z=2)]
    if style == "under":
        items[0]["underline_for"] = it["id"]
    drop = []
    turned = False
    if style != "under" and K.lum(col) > 0.45 and K.lum(it.get("ink") or "#FFFFFF") > 0.45:
        # the phrase turns dark once the marker is under all of it (its fourth frame), never dark on the picture
        got_i, drop = recolour(it, v, lo, hi, K.DARK, min(f_hit + 4, end - 1), W, H, tag="hl")
        items += got_i
        turned = True
    return {"items": items, "drop": drop, "marks": _marks(f_hit, "marker sweeps", 0.5),
            "log": {"word": it["t"], "phrase": str(it["t"])[lo:hi], "style": style, "hit": f_hit, "on": it["id"], "ink_turned": turned}}


def flip(l1, work, ask, sense=None):
    """A phrase on screen flips on its hit: two negative frames over it, then it stands in the accent (the rest of its
    line keeps its ink)."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    t = text_of(o)
    if not t:
        return {"skip": "flip needs a word"}
    a, z, hit = K.span(l1, ask, need=20, text=t)
    f_hit = hit[0][1] if hit else a
    got = target(l1, t, f_hit, f_hit + 2)
    if got is None:
        return {"skip": f"'{t}' is not on screen there"}
    it, v, lo, hi = got
    f_hit = max(f_hit, it["f"][0])
    part = _glyph_span(v, str(it["t"]), lo, hi)
    (X0, top, X1, bot), s = _geo(it, v, f_hit, W, H, part)
    px = float(v["px"]) * s
    box = [X0 - 0.1 * px / W, top - 0.12 * px / H, X1 + 0.1 * px / W, bot + 0.1 * px / H]
    items = [{"id": f"{ask['id']}.neg", "k": "flash", "f": [f_hit, min(f_hit + 2, dur)], "mode": "neg", "rect": [round(q, 5) for q in box], "z": 3}]
    drop = []
    if it["f"][1] > f_hit + 2:
        got_i, drop = recolour(it, v, lo, hi, o.get("c") or pal["accent"], f_hit + 2, W, H, tag="fl")
        items += got_i
    return {"items": items, "drop": drop, "marks": _marks(f_hit, "word flips", 0.7), "log": {"word": it["t"], "phrase": str(it["t"])[lo:hi], "hit": f_hit}}


# --------------------------------------------------------------------------- typed and redacted on the voice

def _letter_times(l1, text, a, z, hit, per=1.3):
    """The frame each character of ``text`` appears: a said word's letters within its own breath (before the next
    word is said), the rest at ``per`` frames a letter."""
    ws = text.split(" ")
    starts = K.times(l1, text, a, z, hit) if hit else []
    out, f = [], float(a)
    for i, w in enumerate(ws):
        n = len(w) + (1 if i + 1 < len(ws) else 0)
        if i < len(starts) and hit:
            f0 = float(max(starts[i], f))
            f1 = float(starts[i + 1]) if i + 1 < len(starts) else f0 + max(n * per, 2)
            step = max(min((f1 - f0) / max(n, 1), per * 1.6), 0.5)
        else:
            f0, step = f, per
        for q in range(n):
            out.append(int(f0 + q * step))
        f = f0 + n * step
    m = 0
    for i in range(len(out)):                           # never back in time: the letters only ever arrive
        m = max(m, out[i])
        out[i] = min(m, z - 1)
    return out


def typewriter(l1, work, ask, sense=None):
    """Text typed a letter at a time on the voice's words, a cursor at its end, blinking when it is done."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    t = text_of(o)
    if not t:
        return {"skip": "typewriter needs text"}
    t = cased(t, o)
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 30)), text=t)
    mono = o.get("font") in (None, "mono")
    v = dict(K.MONO) if mono else K.voice(pal["font"], 100, shadow=0.5)
    v["px"] = K.px(o.get("px") or 64)
    lines = K.wrap(v, t, 0.84 * W)
    while len(lines) > 4 and v["px"] > K.px(K.FLOOR_PX + 4):
        v["px"] = round(v["px"] * 0.88, 2)
        lines = K.wrap(v, t, 0.84 * W)
    lh = 1.28 * v["px"] / H
    bw = max(K.ink_box(v, ln)[1][2] for ln in lines) / W
    bh = lh * len(lines)
    x0, base = K.seat(l1, S, a, z, bw, bh, prefer=float(o.get("prefer", 0.30)), align="l", x=o.get("x"))
    top = base - bh
    ink, tone = _ink(S, [x0, top, x0 + bw, base], a, z, pal)
    vn = vname(ask, "tw")
    times_ = _letter_times(l1, " ".join(lines), a, z, hit, per=float(o.get("per", 1.3)))
    items, k = [], 0
    cur = o.get("cursor", True)
    col = pal["accent"] if tone < 0.55 else K.DARK
    done_at = times_[-1] if times_ else a
    for li, ln in enumerate(lines):
        y = round(top + lh * (li + 1) - 0.28 * v["px"] / H, 6)
        n = len(ln)
        ts = times_[k:k + n]
        k += n + 1
        if not ts:
            continue
        for q in range(n):
            f0 = ts[q]
            f1 = ts[q + 1] if q + 1 < n else z
            while q + 1 < n and f1 <= f0 and q + 2 <= n:
                break
            if f1 <= f0:
                continue
            items.append(K.word(f"{ask['id']}.l{li}.{q:03d}", (f0, f1), ln[:q + 1], vn, round(x0, 6), y, ink, z=8, role="big", on=ts[0]))
            if cur and (li == len(lines) - 1 or f0 < (times_[k] if k < len(times_) else z)):
                adv = K.ink_box(v, ln[:q + 1])[0] / W
                cw, ch = 0.55 * v["px"] / W, 0.78 * v["px"] / H
                stop = min(f1, done_at) if li < len(lines) - 1 or q + 1 < n else f1
                if stop > f0:
                    items.append(K.rect(f"{ask['id']}.k{li}.{q:03d}", (f0, stop), [round(x0 + adv + 0.004, 5), round(y - ch, 5), round(x0 + adv + 0.004 + cw, 5), round(y + 0.004, 5)], col))
    if cur:
        last = lines[-1]
        adv = K.ink_box(v, last)[0] / W
        y = round(top + lh * len(lines) - 0.28 * v["px"] / H, 6)
        cw, ch = 0.55 * v["px"] / W, 0.78 * v["px"] / H
        f = done_at + 1
        on = True
        while f < z:
            g = min(f + 8, z)
            if on:
                items.append(K.rect(f"{ask['id']}.blink{f}", (f, g), [round(x0 + adv + 0.004, 5), round(y - ch, 5), round(x0 + adv + 0.004 + cw, 5), round(y + 0.004, 5)], col))
            on = not on
            f = g
        items = [i for i in items if not (i["id"].startswith(f"{ask['id']}.k") and i["f"][1] > done_at + 1 and i["f"][0] > done_at)]
    return {"items": items, "voices": {vn: v}, "marks": _marks(a, "typing starts", 0.3) + _marks(done_at, "typed", 0.4),
            "seat": {"x": x0, "y": base, "w": bw, "h": bh, "f": [a, z]}, "log": {"lines": lines, "letters": len(times_), "done": done_at}}


def redact(l1, work, ask, sense=None):
    """A line under black bars that come off each word as he says it (``reveal``), or words that are said and then
    struck out under a bar (``strike``, the words the caller names, else all)."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    t = text_of(o)
    if not t:
        return {"skip": "redact needs text"}
    t = cased(t, o, "upper")
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 30)), text=t)
    mode = o.get("mode") or K.rng_of(l1, ask).choice(["reveal", "strike"])
    v = K.mono(o.get("px") or 66) if o.get("font") in (None, "mono") else K.voice(pal["font"], K.px(o.get("px") or 80), shadow=0.4)
    lines = K.wrap(v, t, 0.84 * W)
    while len(lines) > 4 and v["px"] > K.px(K.FLOOR_PX + 4):
        v["px"] = round(v["px"] * 0.88, 2)
        lines = K.wrap(v, t, 0.84 * W)
    lh = 1.34 * v["px"] / H
    bw = max(K.ink_box(v, ln)[1][2] for ln in lines) / W
    bh = lh * len(lines)
    x0, base = K.seat(l1, S, a, z, bw, bh, prefer=float(o.get("prefer", 0.32)), align="l")
    top = base - bh
    ink, tone = _ink(S, [x0, top, x0 + bw, base], a, z, pal)
    bar = o.get("c") or ("#050505" if tone > 0.25 else pal["accent"])
    struck = {K.norm(w) for w in (o.get("struck") or [])}
    allw = " ".join(lines).split()
    ts = K.times(l1, " ".join(allw), a, z, hit) if hit else [a + 4 * i for i in range(len(allw))]
    vn = vname(ask, "rd")
    items, wi, marks = [], 0, []
    for li, ln in enumerate(lines):
        y = round(top + lh * (li + 1) - 0.3 * v["px"] / H, 6)
        pos = 0
        for w in ln.split(" "):
            pre = ln[:ln.index(w, pos)]
            pos = len(pre) + len(w)
            adv = K.ink_box(v, pre)[0] / W if pre else 0.0
            _, b = K.ink_box(v, w)
            wx0, wx1 = x0 + adv + b[0] / W - 0.004, x0 + adv + b[2] / W + 0.004
            wy0, wy1 = y - 0.80 * v["px"] / H, y + 0.12 * v["px"] / H
            f_w = min(max(ts[wi] if wi < len(ts) else a, a), z - 3)
            if mode == "reveal":
                items.append(K.word(f"{ask['id']}.w{wi:02d}", (f_w, z), w, vn, round(x0 + adv, 6), y, ink, z=8, role="big", on=f_w))
                n = f_w + 3 - a
                rr = [[round(wx0 + (wx1 - wx0) * max(0.0, (q - (n - 3)) / 3.0), 5), round(wy0, 5), round(wx1, 5), round(wy1, 5)] for q in range(max(n, 1))]
                items.append(K.rect(f"{ask['id']}.bar{wi:02d}", (a, a + max(n, 1)), rr, bar))
            else:
                hit_w = not struck or K.norm(w) in struck
                word = K.word(f"{ask['id']}.w{wi:02d}", (f_w, z), w, vn, round(x0 + adv, 6), y, ink, z=8, role="big", on=f_w)
                if hit_w:
                    at = min(f_w + int(o.get("after", 8)), z - 1)
                    word["strike"] = {"at": at, "ink": bar}
                    marks += _marks(at, "struck", 0.5)
                items.append(word)
            wi += 1
    return {"items": items, "voices": {vn: v}, "marks": marks or _marks(a, "redaction", 0.4),
            "seat": {"x": x0, "y": base, "w": bw, "h": bh, "f": [a, z]}, "log": {"mode": mode, "lines": lines}}


# --------------------------------------------------------------------------- pages: a chapter card and a run of type

def _plate(id_, f, c0, c1=None, grain=0.05):
    return {"id": id_, "k": "shot", "f": [int(f[0]), int(f[1])], "pic": "plate", "plate": {"kind": "paper", "c0": c0, "c1": c1 or c0, "grain": grain}}


def _carve(l1, a, z):
    from ..alien import sense as asense
    return asense.carve(l1, a, z)


def chapter(l1, work, ask, sense=None):
    """A type page at a section's joint: a plate takes the frame for half a second, a small kicker types on and the
    title pulls into focus, then the film cuts back."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    t = cased(text_of(o), o, "upper")
    if not t:
        return {"skip": "chapter needs a title"}
    a = K.frame_of(l1, o.get("at")) or 0
    n = int(o.get("dur", 15))
    a, z = max(0, min(a, dur - 4)), min(dur, max(a, 0) + max(n, 4))
    z = K.bounded(o, z, a + 4)
    pol = o.get("polarity") or K.rng_of(l1, ask).choice(["dark", "accent", "light"])
    c0, c1, ink = {"dark": ("#141312", "#070707", pal["ink"]), "light": ("#ECE6DA", "#CFC8BA", K.DARK),
                   "accent": (pal["accent"], pal["accent"], K.DARK if K.lum(pal["accent"]) > 0.45 else pal["ink"])}[pol]
    keep, gone, was = _carve(l1, a, z)
    ci, cd = K.clear_captions(l1, a, z)                  # the page says its own words: no caption over it
    keep, gone = keep + ci, gone + cd
    font = pal["font"]      # palette_of resolves the ask's font word
    vt = K.fit(K.voice(font, 100, track=-1, shadow=0), t, 0.80 * W, cap_px=0.16 * H)
    lines = [t]
    if vt["px"] < 0.06 * H and len(t.split()) > 1:
        vt = K.voice(font, 0.09 * H, track=-1, shadow=0)
        lines = K.wrap(vt, t, 0.84 * W)
    kick = cased(str(o.get("kicker") or ""), o, "upper").strip()
    vk = K.mono(44)
    items = list(keep) + [_plate(f"{ask['id']}.pg", (a, z), c0, c1)]
    voices = {vname(ask, "t"): vt, vname(ask, "k"): vk}
    lh = 0.86 * vt["px"] / H
    y0 = 0.5 - lh * len(lines) / 2 + lh
    m = z - a
    for i, ln in enumerate(lines):
        x, y = K.place(vt, ln, 0.5, y0 + i * lh, W, H)
        f0 = min(a + 2, z - 1)
        k = z - f0
        items.append(K.word(f"{ask['id']}.t{i}", (f0, z), ln, vname(ask, "t"), x, y, ink, z=8, role="big", on=f0,
                            blur=[round(16.0 * max(0.0, 1 - q / 5.0) ** 2, 3) for q in range(k)], s=[round(1.0 + 0.05 * max(0.0, 1 - q / 6.0), 5) for q in range(k)]))
    if kick:
        x, y = K.place(vk, kick, 0.5, y0 - lh - 0.03, W, H)
        n_k = min(len(kick), max(m - 3, 1))
        for q in range(n_k):
            kk = max(1, round(len(kick) * (q + 1) / n_k))
            items.append(K.word(f"{ask['id']}.k{q:02d}", (a + q, a + q + 1 if q < n_k - 1 else z), kick[:kk], vname(ask, "k"), x, y,
                                pal["accent"] if pol == "dark" else ink, z=8, role="big", on=a))
    return {"items": items, "drop": gone, "voices": voices, "marks": _marks(a, "chapter page", 0.8),
            "seat": {"x": 0.5, "y": y0, "f": [a, z]}, "log": {"title": t, "polarity": pol, "frames": z - a}}


def type_run(l1, work, ask, sense=None):
    """A run of type alone: each word he says is a page of its own, cut on the word, set as large as the frame
    holds. Its pages change ground at most three times a second (picture changes, never a strobe of light)."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    words = o.get("words")
    t = text_of(o)
    if words:
        words = [str(w.get("t") if isinstance(w, dict) else w) for w in words]
    else:
        words = t.split()
    words = [w for w in words if w.strip()]
    if not words:
        return {"skip": "type_run needs words"}
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 14)), text=" ".join(words))
    ts = K.times(l1, " ".join(words), a, z, hit) if hit else [a + int(o.get("every", 5)) * i for i in range(len(words))]
    pages = []
    for i, w in enumerate(words):
        f0 = max(ts[i] if i < len(ts) else a, a)
        if pages and f0 - pages[-1][0] < 2:
            pages[-1][1] = pages[-1][1] + " " + w
            continue
        pages.append([f0, w])
    if pages[0][0] > a:
        pages[0][0] = a
    if z - pages[-1][0] < 6:
        z = min(dur, K.bounded(o, pages[-1][0] + 8, pages[-1][0] + 4))
    keep, gone, was = _carve(l1, a, z)
    ci, cd = K.clear_captions(l1, a, z)                  # the pages are the words: no caption over them
    keep, gone = keep + ci, gone + cd
    font = pal["font"]      # palette_of resolves the ask's font word
    rng = K.rng_of(l1, ask)
    grounds = [("#0D0C0B", pal["ink"]), (pal["accent"], K.DARK if K.lum(pal["accent"]) > 0.45 else pal["ink"]), ("#EDE7DB", K.DARK)]
    items, voices = list(keep), {}
    last_flip, last_dark = a, True
    verbs = ["punch", "focus", "slide", "punch", "grow"]
    for i, (f0, w) in enumerate(pages):
        f1 = pages[i + 1][0] if i + 1 < len(pages) else z
        if f1 <= f0:
            continue
        g = grounds[i % len(grounds)] if o.get("grounds") != "dark" else grounds[0]
        dark = K.lum(g[0]) < 0.45
        if dark != last_dark and f0 - last_flip < 10:
            g = next(x for x in grounds if (K.lum(x[0]) < 0.45) == last_dark)
            dark = last_dark
        elif dark != last_dark:
            last_flip, last_dark = f0, dark
        tw = cased(w, o, "upper")
        vn = vname(ask, f"p{i}")
        vv = K.fit(K.voice(font, 100, track=-2, shadow=0), tw, 0.88 * W, cap_px=0.30 * H)
        voices[vn] = vv
        _, b = K.ink_box(vv, tw)
        base = 0.5 + (b[3] - b[1]) / H / 2 - b[3] / H
        x, y = K.place(vv, tw, 0.5, base, W, H)
        n = f1 - f0
        verb = verbs[(i + rng.randrange(5)) % len(verbs)] if o.get("verb") is None else o["verb"]
        it = K.word(f"{ask['id']}.w{i}", (f0, f1), tw, vn, x, y, g[1], z=8, role="graphic", on=f0)
        if verb == "punch":
            it["s"] = K.punch(n, size=0.14, tau=2.0)
        elif verb == "focus":
            it["blur"] = [round(12.0 * max(0.0, 1 - q / 4.0) ** 2, 3) for q in range(n)]
        elif verb == "slide":
            it["x"] = K.ease_out(n, x + 0.05, x, frames=4)
        elif verb == "grow":
            it["s"] = [round(0.92 + 0.08 * min(q / 6.0, 1.0), 5) for q in range(n)]
        items.append(_plate(f"{ask['id']}.pg{i}", (f0, f1), g[0], None, grain=0.04))
        items.append(it)
    return {"items": items, "drop": gone, "voices": voices, "marks": [m for p in pages for m in _marks(p[0], "type page", 0.6)],
            "seat": {"x": 0.5, "y": 0.5, "f": [a, z]}, "log": {"pages": [p[1] for p in pages]}}


# --------------------------------------------------------------------------- larger than the frame

def marquee(l1, work, ask, sense=None):
    """A word set larger than the frame slides across it, a graphic, not a line to read: over the picture, or
    behind the speaker when his matte can be had."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    t = cased(text_of(o), o, "upper")
    if not t:
        return {"skip": "marquee needs a word"}
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 40)), text=t)
    font = pal["font"]      # palette_of resolves the ask's font word
    v = K.voice(font, float(o.get("size", 0.34)) * H, track=-2, shadow=0.0)
    _, b = K.ink_box(v, t)
    wd = (b[2] - b[0]) / W
    n = z - a
    left = o.get("dir", "l") != "r"
    travel = max(wd - 0.70, 0.35)
    x_start = 0.10 - b[0] / W if left else 0.90 - b[2] / W
    xs = [round(x_start + (-1 if left else 1) * travel * (q / max(n - 1, 1)), 6) for q in range(n)]
    cy = float(o.get("y", 0.46))
    cy = max(cy, K.MARK[3] + 0.012 + (b[3] - b[1]) / H / 2)     # it crosses the whole frame: under the watermark's keep-out
    y = cy + (b[3] - b[1]) / H / 2 - b[3] / H
    ink, tone = _ink(S, [0.0, cy - 0.15, 1.0, cy + 0.15], a, z, pal, prefer=o.get("ink"))
    it = K.word(f"{ask['id']}.mq", (a, z), t, vname(ask, "mq"), xs, round(y, 6), ink, z=6, role="graphic", on=a)
    log = {"width": round(wd, 2), "behind": False}
    patch = {"items": [it], "voices": {vname(ask, "mq"): v}}
    if o.get("behind", True):
        from . import behind as B
        got = B.stand_behind(l1, work, a, z, ask)
        if got.get("ok"):
            it["behind"] = True
            patch.setdefault("items", []).extend(got.get("items") or [])
            patch["drop"] = got.get("drop") or []
            patch["assets"] = got.get("assets") or {}
            log["behind"] = True
            log["matte"] = got.get("log")
    if not it.get("behind") and o.get("blend", "difference" if tone > 0.35 else None):
        it["blend"] = o.get("blend") or "difference"
        it["ink"] = "#FFFFFF"
    patch.update(marks=_marks(a, "marquee", 0.5), seat={"x": 0.5, "y": cy, "f": [a, z]}, log=log)
    return patch


# --------------------------------------------------------------------------- one word, with a verb

VERBS = ("focus", "punch", "burn", "scramble", "decay", "shake")


def verb(l1, work, ask, sense=None):
    """One word or short line, large, doing what it says: pulled into focus, punched in, burnt in, unscrambled,
    decaying away, or shaken on its stress. The device every other type device can always fall back to."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    t = text_of(o)
    if not t:
        return {"skip": "verb needs a word"}
    t = cased(t, o, "upper")
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 30)), text=t)
    land = hit[0][1] if hit else a
    a = min(a, land)
    rng = K.rng_of(l1, ask)
    vb = o.get("verb") if o.get("verb") in VERBS else rng.choice(VERBS[:4])
    font = pal["font"]      # palette_of resolves the ask's font word
    v = K.fit(K.voice(font, 100, track=-1, shadow=0.55, glow=0.5 if vb == "burn" else None), t, float(o.get("width", 0.80)) * W,
              cap_px=float(o.get("cap", 0.14)) * H)
    lines = [t]
    if K.ink_box(v, t)[1][2] > 0.86 * W:
        lines = K.wrap(v, t, 0.86 * W)
    _, b = K.ink_box(v, max(lines, key=len))
    lh = (b[3] - b[1]) / H
    bw, bh = max(K.ink_box(v, ln)[1][2] - K.ink_box(v, ln)[1][0] for ln in lines) / W, lh * len(lines) + 0.012 * (len(lines) - 1)
    if o.get("seat"):
        cx, base = [float(q) for q in o["seat"]]
    else:
        x0, base = K.seat(l1, S, land, z, bw, bh, prefer=float(o.get("prefer", 0.38)))
        cx = x0 + bw / 2
    ink, tone = _ink(S, [cx - bw / 2, base - bh, cx + bw / 2, base], land, z, pal, prefer=o.get("ink"))
    vn = vname(ask, "v")
    items = []
    n = z - land
    for li, ln in enumerate(lines):
        x, y = K.place(v, ln, cx, base - (len(lines) - 1 - li) * (lh + 0.012), W, H)
        it = K.word(f"{ask['id']}.v{li}", (land, z), ln, vn, x, y, ink, z=8, role="big", on=land)
        if vb == "focus":
            it["blur"] = [round(18.0 * max(0.0, 1 - q / 6.0) ** 2, 3) for q in range(n)]
            it["s"] = [round(1.0 + 0.06 * max(0.0, 1 - q / 7.0), 5) for q in range(n)]
            it["op"] = [round(min(1.0, 0.35 + 0.33 * q), 3) for q in range(n)]
        elif vb == "punch":
            it["s"] = K.punch(n, size=0.18, tau=2.0)
        elif vb == "burn":
            it["grow"] = [round(6.0 * max(0.0, 1 - q / 6.0), 3) for q in range(n)]
            hot = min(land + 2, z - 1)
            first = K.cut(it, land, hot, new_id=f"{ask['id']}.v{li}h")
            first["ink"] = "#FFFFFF"
            rest = K.cut(it, hot, z)
            rest["ink"] = pal["accent"] if tone < 0.55 else ink
            items += [first, rest]
            continue
        elif vb == "decay":
            k0 = max(int(n * 0.55), 1)
            it["op"] = [1.0] * k0 + [round(max(0.0, (1 - (q / max(n - k0, 1))) * (0.35 + 0.65 * ((q * 7919) % 5 > 1))), 3) for q in range(n - k0)]
            it["blur"] = [0.0] * k0 + [round(8.0 * q / max(n - k0, 1), 3) for q in range(n - k0)]
            it["y"] = [y] * k0 + [round(y + 0.008 * q / max(n - k0, 1), 6) for q in range(n - k0)]
        elif vb == "shake":
            jit = [0.0, 0.007, -0.006, 0.004, -0.003, 0.002]
            it["x"] = [round(x + (jit[q] if q < len(jit) else 0.0), 6) for q in range(n)]
        elif vb == "scramble":
            m = min(10, max(n // 2, 2))
            letters = [c for c in ln if c.strip()] + list("0123456789#%&")
            out = []
            for q in range(0, m, 2):
                good = int(len(ln) * (q + 2) / (m + 2))
                s = "".join(c if (i < good or not c.strip()) else rng.choice(letters) for i, c in enumerate(ln))
                out.append(K.word(f"{ask['id']}.v{li}s{q}", (land + q, min(land + q + 2, z)), s, vn, x, y, ink, z=8, role="big", on=land))
            items += out
            it = K.cut(it, min(land + m, z - 1), z)
        items.append(it)
    return {"items": items, "voices": {vn: v}, "marks": _marks(land, f"word {vb}", 0.7),
            "seat": {"x": cx, "y": base, "w": bw, "h": bh, "f": [land, z]}, "log": {"verb": vb, "lines": lines, "lands": land}}


# --------------------------------------------------------------------------- the caption line, read along

def readalong(l1, work, ask, sense=None):
    """The caption page stands whole from its first word, dimmed, and each word comes up to full as it is said."""
    o = K.opts(ask)
    W, H, fps, dur = K.size(l1)
    a = K.frame_of(l1, o.get("at")) if o.get("at") is not None else 0
    z = K.frame_of(l1, o.get("until")) if o.get("until") is not None else (
        int(o["at"][1]) if isinstance(o.get("at"), (list, tuple)) and len(o["at"]) > 1 else dur)
    a, z = a or 0, z or dur
    z = K.bounded(o, z, a + 2)
    dim = float(o.get("dim", 0.34))
    caps = [it for it in l1.get("items") or [] if it.get("k") == "word" and it.get("role") == "cap" and a <= int(it.get("on", it["f"][0])) < z]
    if not caps:
        return {"skip": "no caption words there"}
    # a page is the words that leave together on one line; a word's ink pieces stay one word
    first = {}
    for it in sorted(caps, key=lambda i: (i["f"][0], i["id"])):
        key = (K.norm(it["t"]), int(it.get("on", it["f"][0])), round(_rest(it["x"]), 4))
        if key not in first:
            first[key] = it
    by_line = {}
    for it in first.values():
        by_line.setdefault(round(_rest(it["y"]), 3), []).append(it)
    out, n = [], 0
    for y, ws in by_line.items():
        ws.sort(key=lambda i: int(i.get("on", i["f"][0])))
        groups, cur = [], []
        for it in ws:
            if cur and int(it.get("on", it["f"][0])) >= max(c["f"][1] for c in cur):
                groups.append(cur)
                cur = []
            cur.append(it)
        if cur:
            groups.append(cur)
        for g in groups:
            start = min(int(i.get("on", i["f"][0])) for i in g)
            for it in g:
                on = int(it.get("on", it["f"][0]))
                if it["f"][0] <= start or on <= start:
                    continue
                lo, hi = it["f"]
                new = dict(it, f=[start, hi])
                pre = on - start
                old_op = it.get("op", 1.0)
                tail = old_op if isinstance(old_op, list) else [float(old_op)] * (hi - lo)
                gap = [1.0] * max(lo - on, 0)
                new["op"] = [round(dim, 3)] * max(lo - start - len(gap), 0) + gap + [round(float(q), 4) for q in tail]
                new["op"] = new["op"][:hi - start] + [new["op"][-1]] * max(0, hi - start - len(new["op"]))
                for k in ("x", "y", "s", "blur", "grow"):
                    if isinstance(it.get(k), list) and len(it[k]) == hi - lo:
                        new[k] = [it[k][0]] * (lo - start) + it[k]
                out.append(new)
                n += 1
    if not out:
        return {"skip": "the captions already stand whole"}
    return {"items": out, "marks": [], "log": {"words": n}}


DEVICES = {
    "counter": {"plan": counter, "layer": 3, "needs": ("voice",), "fallback": ("staircase", "verb"), "words": ("lands",),
                "card": 'counter at(word where the count starts) lands(word that says the final figure) from to unit(short|words|none) fmt("${n}", "{n} MILES") dec sep label hold. '
                        'Digits step hard and slow into the spoken figure on one seat across every cut, then hit and hold with an underline',
                "amplifies": "a figure, money, a count, a jump from one number to another; the payoff when it is a number"},
    "staircase": {"plan": staircase, "layer": 3, "needs": (), "fallback": ("verb",), "words": ("lines[].at",),
                  "card": 'staircase at lines[{t size(big|small) at}] stairs. A two-size stack: each line lands on its word, stepping across',
                  "amplifies": "a figure and what it measures, a before and after, a list of three"},
    "name": {"plan": name, "layer": 3, "needs": (), "fallback": ("verb",),
             "card": 'name at name title layout(stack|left|split). A name super in two sizes: the small line types on, the name slams on the word that says it',
             "amplifies": "the first time a person, company or place the story hangs on is named (the name lands 3-10 s in)"},
    "ladder": {"plan": ladder, "layer": 3, "needs": (), "fallback": ("verb",),
               "card": 'ladder at words[{t w(whisper|shout) accent}] brackets. Whisper words come in as he says them, the shout lands huge on its word',
               "amplifies": "the hook, a verdict, a contrast; whisper the set-up, shout the word that carries it"},
    "brackets": {"plan": brackets, "layer": 3, "needs": (), "fallback": ("verb",),
                 "card": 'brackets at t. One word shouted, held by brackets that snap in',
                 "amplifies": "a single defining word, a label, a definition"},
    "highlight": {"plan": highlight, "layer": 3, "needs": ("type",), "fallback": ("flip", "verb"),
                  "card": 'highlight at t(word already on screen) style(marker|box|under) c. A marker sweeps behind the word on its hit; its ink turns to read',
                  "amplifies": "the one word of a caption or display line that matters most"},
    "flip": {"plan": flip, "layer": 3, "needs": ("type",), "fallback": ("verb",),
             "card": 'flip at t(word on screen) c. Two negative frames over the word on its hit, then it stands in the accent',
             "amplifies": "a word that changes meaning, the turn"},
    "typewriter": {"plan": typewriter, "layer": 3, "needs": (), "fallback": ("verb",),
                   "card": 'typewriter at until t font(mono|display) cursor. Typed a letter at a time on his words, a cursor blinking at its end',
                   "amplifies": "a quote, a message, a document, a date and place, a search or a headline"},
    "redact": {"plan": redact, "layer": 3, "needs": (), "fallback": ("typewriter", "verb"),
               "card": 'redact at t mode(reveal|strike) struck[] c. Black bars come off each word as he says it, or strike words out after they are said',
               "amplifies": "a secret, a confession, something hidden or erased, a correction"},
    "chapter": {"plan": chapter, "layer": 1, "needs": (), "fallback": ("verb",),
                "card": 'chapter at t kicker dur polarity(dark|light|accent). A type page at a section joint: plate, kicker types on, title pulls into focus',
                "amplifies": "a new section, a jump in time or place, a numbered list's next item"},
    "type_run": {"plan": type_run, "layer": 1, "needs": (), "fallback": ("ladder", "verb"),
                 "card": 'type_run at until t|words[] verb. Type alone: each word he says is a page of its own, cut on the word, as large as the frame holds',
                 "amplifies": "the hook, a punchline said word by word, a list, a slogan"},
    "marquee": {"plan": marquee, "layer": 3, "needs": (), "fallback": ("verb",),
                "card": 'marquee at until t dir(l|r) behind y blend. A word larger than the frame slides across it, behind the speaker when his matte can be had',
                "amplifies": "a theme word that hangs over a whole stretch, the subject of the film"},
    "verb": {"plan": verb, "layer": 3, "needs": (), "fallback": (),
             "card": 'verb at t verb(focus|punch|burn|scramble|decay|shake) seat. One word, large, doing what it says',
             "amplifies": "any word that carries the moment: focus for clarity, burn for heat, decay for loss, scramble for confusion, shake for impact"},
    "readalong": {"plan": readalong, "layer": 3, "needs": ("type",), "fallback": (),
                  "card": 'readalong at until dim. The caption page stands whole and dim; each word comes up as it is said',
                  "amplifies": "a slow, important sentence the viewer should read with him"},
}
