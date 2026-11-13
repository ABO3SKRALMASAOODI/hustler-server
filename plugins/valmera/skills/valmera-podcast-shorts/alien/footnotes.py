"""Footnoted speech (ALIEN C6) on the plan: a superscript on the claim's word, the footnote typed at the foot.

Reads ``alien/footnotes.json`` (evidence.py: only claims a source states) and adds type to the baked plan:
the number in the accent beside the caption word that ends the claim, and the note with its source at the
foot of the safe area, word after word, then held and faded. One footnote at a time, two rows at most.
"""
import l1pass as L

ACCENT = "#FFC857"
DIM = "#AEB4BE"
X0, X1 = 0.085, 0.855                    # inside the safe area, left of the platform's buttons
ROWS = (0.748, 0.776)                    # baselines; the lower row ends at 0.781, inside y 0.80
NOTE_PX, NUM_PX = 40, 30
HOLD_F, FADE_F, STEP_F = 135, 6, 2


def frame_of(l1, t):
    """A second of the clip as a programme frame, through the spine. None when that second was cut."""
    for e in l1.get("spine") or []:
        if e["src"][0] - 1e-3 <= t <= e["src"][1] + 1e-3:
            return int(round(e["f"][0] + (t - e["src"][0]) * l1.get("fps", 30)))
    return None


def _cap_word(l1, f):
    caps = [it for it in l1["items"] if it.get("k") == "word" and it.get("role") == "cap"]
    best = min(caps, key=lambda it: abs(it.get("on", it["f"][0]) - f), default=None)
    return best if best is not None and abs(best.get("on", best["f"][0]) - f) <= 15 else None


def apply(l1, notes, hold=()):
    """``hold``: frame spans in which a designed panel is being read. A note waits for the panel to leave, and leaves
    before the next one lands: one thing is read at a time (TASTE P8). A note with no time left is not placed."""
    W, H = l1["frame"]
    n_f = int(l1["dur_f"])
    cap_px = float((l1["voices"].get("a") or next(iter(l1["voices"].values()))).get("px") or 56)
    l1["voices"].update(fn=dict(font="InstrumentSerif-Regular.ttf", px=NOTE_PX, track=0, shadow=dict(a=0.5, blur=0.008)),
                        fn_n=dict(font="InterDisplay-Bold.ttf", px=NUM_PX, track=0, shadow=dict(a=0.5, blur=0.008)),
                        fn_sup=dict(font="InterDisplay-Bold.ttf", px=int(round(cap_px * 0.5)), track=0, shadow=dict(a=0.5, blur=0.008)))
    placed, items, waived = [], [], []
    for n in notes:
        f = frame_of(l1, float(n["t_s"]))
        w = _cap_word(l1, f) if f is not None else None
        if w is None:
            continue
        placed.append((n, w))
    for i, (n, w) in enumerate(placed):
        v = l1["voices"][w["v"]]
        wx, wy = float(L._rest(w["x"])), float(L._rest(w["y"]))
        ww = L.text_w(v, w["t"], W)
        chunk = [it for it in l1["items"] if it.get("k") == "word" and it.get("chunk") == w.get("chunk")] if w.get("chunk") else [w]
        end = max(it["f"][1] for it in chunk)
        on = min(w.get("on", w["f"][0]) + 6, end - 2)
        start = on + 4
        held = False
        for a, b in sorted(hold):
            if start < b and start + 20 > a:
                start, held = b + 2, True
        nxt = placed[i + 1][1].get("on", placed[i + 1][1]["f"][0]) if i + 1 < len(placed) else n_f
        stop = max(min(start + HOLD_F, nxt, n_f), start + 20)
        stop = min(stop, n_f)
        for a, b in sorted(hold):
            if start < a < stop:
                stop, held = a - 2, True
        if held and stop - start < 45:
            waived.append(dict(n=n["n"], why="a designed panel is being read there"))
            continue
        sup_w = L.text_w(l1["voices"]["fn_sup"], str(n["n"]), W) + 0.004
        for it in chunk:                                           # the words after it on its row make room
            if it is not w and abs(float(L._rest(it["y"])) - wy) < 0.004 and float(L._rest(it["x"])) > wx:
                it["x"] = L._shift(it["x"], sup_w)
        items.append(dict(id=f"fn{n['n']}.sup.0", k="word", f=[on, end], t=str(n["n"]), v="fn_sup", x=round(wx + ww + 0.003, 5),
                          y=round(wy - 0.40 * float(v.get("px") or cap_px) / H, 5), ink=ACCENT, role="big", on=on, z=5))
        sp = L.text_w(l1["voices"]["fn"], "n n", W) - 2 * L.text_w(l1["voices"]["fn"], "n", W)

        def lay(source):
            toks = ([(str(n["n"]), "fn_n", ACCENT)] + [(t, "fn", "#FFFFFF") for t in n["note"].split()] + [("—", "fn", DIM)]
                    + [(t, "fn", DIM) for t in source.split()])
            x, row, out = X0, 0, []
            for t, voice, ink in toks:
                tw = L.text_w(l1["voices"][voice], t, W)
                if x + tw > X1 and x > X0:
                    row, x = row + 1, X0
                if row >= len(ROWS):
                    return None
                out.append((t, voice, ink, x, row))
                x += tw + sp
            return out

        laid = lay(n["source"]) or lay(n["source"].split(",")[0])       # the page's title stays in film.json when it cannot fit
        if laid is None:
            continue
        for k, (t, voice, ink, x, row) in enumerate(laid):
            a = min(start + k * STEP_F, stop - FADE_F - 1)
            life = stop - a
            op = [1.0] * (life - FADE_F) + [round(1.0 - (j + 1) / FADE_F, 3) for j in range(FADE_F)]
            items.append(dict(id=f"fn{n['n']}.t.{k}", k="word", f=[a, stop], t=t, v=voice, x=round(x, 5), y=ROWS[row], ink=ink,
                              op=op, role="big", on=a, z=5))
        items.append(dict(id=f"fn{n['n']}.pool", k="flash", f=[start, stop], mode="pool", rect=[0.07, 0.70, 0.90, 0.81], a=0.55, z=0))
    spans = [tuple(it["f"]) for it in items if it["id"].endswith(".pool")]
    lifted = []
    for key, us in L.units(l1).items():                    # TASTE P6: other type standing where a footnote will be read moves above it
        a, b = min(u["f"][0] for u in us), max(u["f"][1] for u in us)
        box = L.unit_box(us, l1)
        if box[3] > 0.705 and box[1] < 0.81 and any(min(b, sb) - max(a, sa) > 0 for sa, sb in spans):
            dy = 0.692 - box[3]
            for u in us:
                u["y"] = L._shift(u["y"], dy)
            lifted.append(key)
    l1["items"] = sorted(l1["items"] + items, key=lambda it: it["f"][0])
    return dict(asked=len(notes), lifted=lifted, waived=waived, placed=[dict(n=n["n"], note=n["note"], source=n["source"], word=w["t"], on=w.get("on")) for n, w in placed
                        if not any(x["n"] == n["n"] for x in waived)])
