"""Pre-production: the shots to film for the devices an edit intends (3F).

ROADMAP Wave 3 / 3F (``coverage.py`` ``plan_footage``; shot cards with SVG
framing diagrams; the acquisition dialogue). The best references (16, 21,
38) were PURPOSE-SHOT: type on a floor needs an overhead with a hard shadow
and a clean plate; a word behind a laptop needs the laptop in front of the
speaker; a card on a fingertip needs a hand raised to the lens. Most
uploads are not shot for that. ``plan_footage`` turns a brief into the
shortest shot list that covers its devices (<= 12 cards, filmable in <= 20
minutes on a phone), and ``acquisition`` offers at most three ways forward,
each with its cost and time.

Deterministic and browserless: lexical parsing of the brief, a fixed shot
library; no model.
"""

import re

MAX_SHOTS = 12
MAX_MINUTES = 20.0

# device -> what it needs from the footage, the shot that gives it
SHOTS = {
    "talking_head": {
        "name": "A-cam talking head", "framing": "chest-up, eyes on the "
        "upper third, room on one side for type", "lens": "26-35 mm (phone "
        "1x), eye level", "camera": "locked on a tripod",
        "light": "soft key 45°, a darker background", "duration_s": 60,
        "action": "say the script in takes; then hold 5 s still and silent "
        "(a clean frame for type behind you)", "minutes": 3.0,
        "layout": "head"},
    "floor": {
        "name": "Overhead floor, hard shadow", "framing": "top-down, the "
        "floor fills the frame, you enter from one side",
        "lens": "phone 0.5x or 1x from a high tripod / stairwell",
        "camera": "locked (no drift)", "light": "hard sun or one bare "
        "light, low angle, for a long shadow", "duration_s": 15,
        "action": "5 s EMPTY floor first (the clean plate), then walk in "
        "and stand on your mark", "minutes": 1.5, "layout": "floor"},
    "desk": {
        "name": "Desk overhead", "framing": "top-down on the desk, the "
        "surface fills 2/3 of the frame", "lens": "phone 1x", "camera":
        "locked overhead rig", "light": "even daylight", "duration_s": 12,
        "action": "2 s empty desk, then your hands place and use the "
        "object", "minutes": 1.0, "layout": "desk"},
    "behind_object": {
        "name": "Speaker behind a foreground object", "framing": "you "
        "mid-shot BEHIND a laptop / bottle / mic in the lower third",
        "lens": "phone 1x or 2x", "camera": "locked", "light": "soft key",
        "duration_s": 20, "action": "talk; keep the object still in the "
        "foreground", "minutes": 1.5, "layout": "object"},
    "hands": {
        "name": "Hands to the lens", "framing": "mid-shot, hands raised "
        "between face and lens", "lens": "phone 1x", "camera": "locked",
        "light": "soft front light (fingers readable)", "duration_s": 15,
        "action": "make a finger frame toward the lens; point; hold a "
        "phone screen to camera for 3 s each", "minutes": 1.5,
        "layout": "hands"},
    "silhouette": {
        "name": "Silhouette against a bright window", "framing": "full or "
        "3/4 body, the window behind you", "lens": "phone 1x",
        "camera": "locked", "light": "only the window (expose for it)",
        "duration_s": 10, "action": "turn to profile, then walk out of "
        "frame", "minutes": 1.0, "layout": "silhouette"},
    "product": {
        "name": "Product macro + flat-lay", "framing": "the product on a "
        "plain surface; then 6-8 objects laid flat from above",
        "lens": "phone 2x macro", "camera": "locked / slow turntable",
        "light": "soft box or window", "duration_s": 20,
        "action": "one slow turn; then place the objects one by one",
        "minutes": 2.0, "layout": "product"},
    "screen": {
        "name": "Screen recording of the product", "framing": "the UI, "
        "large fonts, one action per beat", "lens": "record_website / "
        "screen capture", "camera": "—", "light": "—", "duration_s": 30,
        "action": "the 3 actions the script names, slowly", "minutes": 1.5,
        "layout": "screen"},
    "walk_jamb": {
        "name": "Walk behind a door jamb", "framing": "a door frame in the "
        "foreground edge, you cross behind it", "lens": "phone 1x",
        "camera": "locked", "light": "any", "duration_s": 8,
        "action": "cross left to right behind the jamb, twice",
        "minutes": 0.5, "layout": "object"},
    "macros": {
        "name": "Desk macros", "framing": "macro: an eye opening, keys, "
        "a pen, a mouse click", "lens": "phone 2x macro", "camera":
        "handheld steady or locked", "light": "window", "duration_s": 20,
        "action": "four 3-5 s inserts", "minutes": 2.0, "layout": "macro"},
    "wide": {
        "name": "Wide establishing", "framing": "the whole room, you small "
        "in it", "lens": "phone 0.5x", "camera": "locked",
        "light": "practical lights on", "duration_s": 8,
        "action": "sit down / walk in", "minutes": 0.5, "layout": "wide"},
    "wall": {
        "name": "Wall at an angle", "framing": "a plain wall fills a third "
        "of the frame, receding at ~30°; you beside it, mid-shot",
        "lens": "phone 1x", "camera": "locked", "light": "soft key, the "
        "wall evenly lit (no hot spot)", "duration_s": 15,
        "action": "2 s without you, then talk beside the wall",
        "minutes": 1.0, "layout": "wall"},
    "vo": {
        "name": "Voice-over, dry", "framing": "audio only", "lens": "phone "
        "10-20 cm from the mouth, in a closet or under a blanket",
        "camera": "—", "light": "—", "duration_s": 60,
        "action": "read the script twice, slowly", "minutes": 2.5,
        "layout": "vo"},
}

# shots that share a camera position pay its setup once; the plan is
# ordered by setup (the shooting order)
SETUP = {"talking_head": "a-cam", "behind_object": "a-cam", "hands": "a-cam",
         "desk": "overhead", "product": "overhead", "macros": "table",
         "floor": "high", "silhouette": "window", "walk_jamb": "door",
         "wide": "wide", "screen": "screen", "vo": "vo", "wall": "wall"}
SETUP_MIN = {"a-cam": 3.0, "overhead": 2.0, "table": 1.0, "high": 2.5,
             "window": 1.0, "door": 1.0, "wide": 0.5, "screen": 0.5,
             "vo": 0.5, "wall": 1.0}

# brief words -> devices -> the shots that enable them
DEVICE_WORDS = [
    (r"\bfloor|ground|under (?:my|his|her) shadow", "floor_type", ["floor"]),
    (r"\bdesk|table\b", "desk_type", ["desk"]),
    (r"\bwall\b|poster on|billboard", "wall_type", ["wall"]),
    (r"behind me\b|behind (?:him|her)\b|text behind", "type_behind",
     ["talking_head"]),
    (r"outline|contour|along (?:my|his|her) (?:body|arm|edge)",
     "contour_type", ["talking_head"]),
    (r"colou?r.?fields?|poster colou?r", "colour_field", ["talking_head"]),
    (r"\bbehind (?:the|a|my)\s+\w+|laptop|bottle|\bmic\b", "behind_object",
     ["behind_object"]),
    (r"fingertip|finger|hands?\b|point(?:ing)?\b|phone to (?:the )?camera",
     "hand_devices", ["hands"]),
    (r"round (?:my|his|her) head|around (?:my|his|her) head|\bhead\b"
     r"|\barcs?\b|orbit",
     "head_orbit", ["talking_head"]),
    (r"silhouette|figure|light\b|embers|glow", "silhouette_light",
     ["silhouette"]),
    (r"product|kit|objects|flat.?lay|spec", "product", ["product"]),
    (r"\bapp\b|\bui\b|screen|website|dashboard|software|tool",
     "device_stage", ["screen"]),
    (r"door|jamb|walk past", "jamb_wipe", ["walk_jamb"]),
    (r"macro|eye|keys|pen\b|click", "macros", ["macros"]),
    (r"voice.?over|\bvo\b|narrat", "voiceover", ["vo"]),
]
# every essay needs its speaker and a place
BASE = ["talking_head", "wide"]


def parse_brief(brief):
    """[(device, [shot keys])] the brief asks for, in brief order."""
    text = " " + str(brief or "").lower() + " "
    out = []
    for pat, dev, shots in DEVICE_WORDS:
        if re.search(pat, text):
            out.append((dev, shots))
    return out


# framing diagrams: one primitive list per layout, drawn as SVG (the card
# the UI shows) and with OpenCV (the sheet the editor sees); fractions of
# the frame. ("rect"|"fill"|"circle"|"ellipse"|"poly"|"dash"|"line", ...)
_SKIN, _ACC, _SHADE, _INK = "#bbbbbb", "#4fa3ff", "#333333", "#111111"
_PERSON_MID = [("circle", .5, .34, .11, _SKIN),
               ("fill", .3, .44, .7, .98, _SKIN)]
DIAGRAMS = {
    "head": [("circle", .5, .38, .17, _SKIN),
             ("ellipse", .5, 1.0, .42, .3, _SKIN),
             ("dash", .62, .12, .96, .5, _ACC)],          # room for type
    "wide": [("line", 0, .62, 1, .62, "#555555"),
             ("rect", .08, .2, .3, .5, "#555555"),         # a window
             ("circle", .62, .5, .03, _SKIN),
             ("fill", .58, .54, .66, .7, _SKIN)],
    "floor": [("fill", 0, 0, 1, 1, "#2a2b30"),
              ("dash", .04, .04, .96, .96, _ACC),          # the clean plate
              ("poly", [(.5, .5), (.56, .47), (.95, .82), (.86, .9)],
               _SHADE),                                    # the long shadow
              ("ellipse", .5, .5, .16, .07, _SKIN),        # shoulders, above
              ("circle", .5, .5, .07, "#999999")],
    "desk": [("poly", [(.06, .55), (.94, .55), (.99, .97), (.01, .97)],
              "#2f3036"),
             ("dash", .06, .55, .94, .97, _ACC),
             ("rect", .55, .66, .8, .8, _SKIN),            # the object
             ("ellipse", .3, .85, .08, .05, "#c9a98f")],   # a hand
    "object": _PERSON_MID + [("fill", .2, .74, .8, .98, _ACC)],
    "hands": [("circle", .5, .3, .11, _SKIN),
              ("fill", .3, .4, .7, .98, _SKIN),
              ("ellipse", .3, .56, .11, .08, _ACC),
              ("ellipse", .7, .56, .11, .08, _ACC)],
    "silhouette": [("fill", 0, 0, 1, .72, "#eeeeee"),
                   ("circle", .5, .32, .1, _INK),
                   ("fill", .32, .42, .68, 1.0, _INK)],
    "product": [("line", 0, .7, 1, .7, "#555555"),
                ("fill", .3, .42, .7, .7, _ACC),
                ("rect", .1, .78, .3, .88, _SKIN),
                ("rect", .4, .78, .6, .9, _SKIN),
                ("rect", .7, .78, .9, .86, _SKIN)],
    "screen": [("fill", .08, .3, .92, .62, _ACC),
               ("fill", .14, .36, .5, .4, "#ffffff"),
               ("fill", .14, .44, .8, .56, "#2a6fc0")],
    "wall": [("poly", [(0, 0), (.45, .12), (.45, .82), (0, 1)], "#3a3c44"),
             ("dash", .05, .2, .4, .7, _ACC),
             ("circle", .7, .36, .1, _SKIN),
             ("fill", .52, .46, .88, .98, _SKIN)],
    "macro": [("circle", .5, .5, .32, _ACC), ("circle", .5, .5, .1, _SKIN)],
    "vo": [("line", .2 + k * .1, .5 - (.08 + .1 * ((k * 3) % 5) / 5),
            .2 + k * .1, .5 + (.08 + .1 * ((k * 3) % 5) / 5), _ACC)
           for k in range(7)],
}


FRAMES = {"9:16": (90, 160), "4:5": (128, 160), "1:1": (140, 140),
          "16:9": (160, 90)}


def _frame(aspect, scale=1.0):
    w, h = FRAMES.get(str(aspect), FRAMES["9:16"])
    return int(w * scale), int(h * scale)


def svg_card(layout, aspect="9:16"):
    """A tiny framing diagram (SVG text): the frame, the subject, the
    surface or object the device needs (DIAGRAMS)."""
    w, h = _frame(aspect)
    m = min(w, h)
    parts = []
    for p in DIAGRAMS.get(layout, DIAGRAMS["head"]):
        k = p[0]
        if k in ("rect", "fill", "dash"):
            x0, y0, x1, y1, col = p[1:]
            style = (f'fill="{col}"' if k == "fill" else
                     f'fill="none" stroke="{col}"' + (
                         ' stroke-dasharray="4 3"' if k == "dash" else ""))
            parts.append(f'<rect x="{x0 * w:.1f}" y="{y0 * h:.1f}" '
                         f'width="{(x1 - x0) * w:.1f}" '
                         f'height="{(y1 - y0) * h:.1f}" {style}/>')
        elif k == "circle":
            cx, cy, r, col = p[1:]
            parts.append(f'<circle cx="{cx * w:.1f}" cy="{cy * h:.1f}" '
                         f'r="{r * m:.1f}" fill="{col}"/>')
        elif k == "ellipse":
            cx, cy, rx, ry, col = p[1:]
            parts.append(f'<ellipse cx="{cx * w:.1f}" cy="{cy * h:.1f}" '
                         f'rx="{rx * w:.1f}" ry="{ry * h:.1f}" '
                         f'fill="{col}"/>')
        elif k == "poly":
            pts, col = p[1:]
            parts.append('<polygon points="' + " ".join(
                f"{x * w:.1f},{y * h:.1f}" for x, y in pts)
                + f'" fill="{col}"/>')
        elif k == "line":
            x0, y0, x1, y1, col = p[1:]
            parts.append(f'<line x1="{x0 * w:.1f}" y1="{y0 * h:.1f}" '
                         f'x2="{x1 * w:.1f}" y2="{y1 * h:.1f}" '
                         f'stroke="{col}" stroke-width="2"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" '
            f'height="{h}" viewBox="0 0 {w} {h}"><rect width="{w}" '
            f'height="{h}" fill="#1b1c20"/>{"".join(parts)}<rect '
            f'width="{w}" height="{h}" fill="none" stroke="#666"/></svg>')


def plan_footage(brief, *, aspect="9:16", have=(), max_shots=MAX_SHOTS):
    """{shots: [card], devices: [...], covered: share, minutes, missing}.
    ``have``: shot keys the user already has footage for (skipped)."""
    asked = parse_brief(brief)
    keys = []
    for k in BASE:
        if k not in keys:
            keys.append(k)
    for _dev, shots in asked:
        for k in shots:
            if k not in keys:
                keys.append(k)
    keys = [k for k in keys if k not in set(have or ())]
    picked, minutes, setups = [], 0.0, set()
    for k in keys:
        if len(picked) >= max_shots:
            break
        spec = SHOTS[k]
        su = SETUP.get(k, k)
        cost = spec["minutes"] + (0.0 if su in setups
                                  else SETUP_MIN.get(su, 1.0))
        if minutes + cost > MAX_MINUTES:
            continue
        minutes += cost
        setups.add(su)
        picked.append(k)
    order = []
    for k in picked:
        if SETUP.get(k, k) not in order:
            order.append(SETUP.get(k, k))
    rank = {k: i for i, k in enumerate(picked)}
    picked = sorted(picked, key=lambda k: (order.index(SETUP.get(k, k)),
                                           rank[k]))
    cards = []
    for k in picked:
        spec = SHOTS[k]
        enables = [d for d, shots in asked if k in shots] or (
            ["talking head devices"] if k == "talking_head" else
            ["establishing, HUD"] if k == "wide" else [])
        cards.append({"n": len(cards) + 1, "key": k, "name": spec["name"],
                      "setup": SETUP.get(k, k),
                      "enables": enables, "framing": spec["framing"],
                      "lens": spec["lens"], "camera": spec["camera"],
                      "light": spec["light"],
                      "duration_s": spec["duration_s"],
                      "action": spec["action"], "minutes": spec["minutes"],
                      "svg": svg_card(spec["layout"], aspect)})
    devices = [d for d, _s in asked]
    covered = [d for d, shots in asked
               if all(k in {c["key"] for c in cards} | set(have or ())
                      for k in shots)]
    return {"shots": cards, "devices": devices,
            "covered": round(len(covered) / float(len(devices)), 3)
            if devices else 1.0,
            "missing": [d for d in devices if d not in covered],
            "minutes": round(minutes, 1)}


def acquisition(plan, uploaded_minutes=0.0):
    """At most three ways forward, each with its cost and time."""
    opts = [{"option": "Shoot the list",
             "time": f"≈{plan['minutes']:.0f} min on a phone + tripod",
             "cost": "no credits until you upload",
             "gives": f"{int(plan['covered'] * 100)}% of the planned "
                      "devices at full quality"}]
    if uploaded_minutes:
        opts.append({"option": "Edit what you uploaded now",
                     "time": "this session",
                     "cost": "the usual credits",
                     "gives": "the devices your footage supports; the rest "
                              "fall back (type in front, static placement)"
                              + (f" — missing: {', '.join(plan['missing'])}"
                                 if plan.get("missing") else "")})
    opts.append({"option": "Stand-ins",
                 "time": "this session",
                 "cost": "the usual credits",
                 "gives": "procedural light, layouts and type on black "
                          "instead of the missing setups (no generated "
                          "imagery)"})
    return opts[:3]


def format_plan(plan, options):
    lines = [f"{len(plan['shots'])} shots, ≈{plan['minutes']:.0f} min, "
             f"covers {int(plan['covered'] * 100)}% of the devices the "
             "brief asks for:"]
    for c in plan["shots"]:
        lines.append(f"{c['n']}. {c['name']} ({c['duration_s']} s) — "
                     f"{c['framing']}; {c['lens']}; {c['camera']}; "
                     f"{c['light']}. Do: {c['action']}. Enables: "
                     f"{', '.join(c['enables']) or '—'}.")
    if plan.get("missing"):
        lines.append(f"Not coverable in {MAX_MINUTES:.0f} min: "
                     f"{', '.join(plan['missing'])}.")
    lines.append("Options:")
    for k, o in enumerate(options, start=1):
        lines.append(f"  {k}) {o['option']} — {o['time']}; {o['cost']}; "
                     f"{o['gives']}.")
    return "\n".join(lines)


def _bgr(col):
    col = col.lstrip("#")
    return (int(col[4:6], 16), int(col[2:4], 16), int(col[0:2], 16))


def _draw_card(img, x0, y0, w, h, layout):
    """DIAGRAMS[layout], drawn with OpenCV (no browser on agent lanes)."""
    import cv2
    import numpy as np
    m = min(w, h)
    out, ox, oy = img, x0, y0
    img = np.zeros((h + 1, w + 1, 3), np.uint8)   # clipped to the frame
    x0 = y0 = 0
    cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), _bgr("#1b1c20"), -1)

    def P(fx, fy):
        return (int(round(x0 + fx * w)), int(round(y0 + fy * h)))
    for p in DIAGRAMS.get(layout, DIAGRAMS["head"]):
        k = p[0]
        if k in ("rect", "fill"):
            cv2.rectangle(img, P(p[1], p[2]), P(p[3], p[4]), _bgr(p[5]),
                          -1 if k == "fill" else 1, cv2.LINE_AA)
        elif k == "dash":
            (ax, ay), (bx, by) = P(p[1], p[2]), P(p[3], p[4])
            for (u0, v0, u1, v1) in ((ax, ay, bx, ay), (bx, ay, bx, by),
                                     (bx, by, ax, by), (ax, by, ax, ay)):
                n = max(1, int(np.hypot(u1 - u0, v1 - v0) // 7))
                for i in range(0, n, 2):
                    t0, t1 = i / n, min(1.0, (i + 1) / n)
                    cv2.line(img, (int(u0 + (u1 - u0) * t0),
                                   int(v0 + (v1 - v0) * t0)),
                             (int(u0 + (u1 - u0) * t1),
                              int(v0 + (v1 - v0) * t1)), _bgr(p[5]), 1)
        elif k == "circle":
            cv2.circle(img, P(p[1], p[2]), int(p[3] * m), _bgr(p[4]), -1,
                       cv2.LINE_AA)
        elif k == "ellipse":
            cv2.ellipse(img, P(p[1], p[2]), (int(p[3] * w), int(p[4] * h)),
                        0, 0, 360, _bgr(p[5]), -1, cv2.LINE_AA)
        elif k == "poly":
            cv2.fillPoly(img, [np.array([P(x, y) for x, y in p[1]])],
                         _bgr(p[2]), cv2.LINE_AA)
        elif k == "line":
            cv2.line(img, P(p[1], p[2]), P(p[3], p[4]), _bgr(p[5]), 2,
                     cv2.LINE_AA)
    cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), (102, 102, 102), 1)
    out[oy:oy + h + 1, ox:ox + w + 1] = img


def sheet_png(plan, path, aspect="9:16", cols=4):
    """The shot cards as one contact sheet (PNG): each diagram with its
    number, name and duration. Returns ``path`` (None when empty)."""
    import cv2
    import numpy as np
    shots = plan.get("shots") or []
    if not shots:
        return None
    w, h = _frame(aspect, 1.66)
    band = 54
    cols = min(cols, len(shots))
    rows = (len(shots) + cols - 1) // cols
    pad = 12
    W = cols * (w + pad) + pad
    H = rows * (h + band + pad) + pad
    img = np.full((H, W, 3), 245, np.uint8)
    for i, c in enumerate(shots):
        r, k = divmod(i, cols)
        x0, y0 = pad + k * (w + pad), pad + r * (h + band + pad)
        key = c.get("key")
        _draw_card(img, x0, y0, w, h, SHOTS.get(key, {}).get("layout",
                                                              "head"))
        name = f"{c['n']}. {c['name']}"
        words, lines, cur = name.split(), [], ""
        for wd in words:
            t = (cur + " " + wd).strip()
            if cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, .4, 1)[0][0] > w:
                lines.append(cur)
                cur = wd
            else:
                cur = t
        lines.append(cur)
        for j, ln in enumerate(lines[:2]):
            cv2.putText(img, ln, (x0, y0 + h + 16 + j * 15),
                        cv2.FONT_HERSHEY_SIMPLEX, .4, (20, 20, 20), 1,
                        cv2.LINE_AA)
        cv2.putText(img, f"{c['duration_s']} s  ~{c['minutes']:g} min",
                    (x0, y0 + h + 48), cv2.FONT_HERSHEY_SIMPLEX, .36,
                    (90, 90, 90), 1, cv2.LINE_AA)
    cv2.imwrite(path, img)
    return path
