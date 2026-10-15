"""The attachment language (3F): a phrase -> where a graphic lives.

"on the desk", "behind the laptop", "follow her hand", "round his head",
"on his fingertip", "between his hands", "on the wall", "over his shoulder"
— deterministic, browserless, lexical (no model): the agent's own words
become a relation and a target the world verbs understand.

    parse("hide it behind the laptop")
      -> {"relation": "behind", "target": "object", "name": "laptop"}
    parse("make the label follow his hand")
      -> {"relation": "follow", "target": "hand", "part": "palm"}

``anchor_for(parsed)`` turns a parse into the MotionItem.anchor fields
(``to`` for landmark targets; object / plane targets need their definition
written first — place_in_world does both in one batch).
"""

import re

SURFACES = {"floor": "floor", "ground": "floor", "wall": "wall",
            "desk": "desk", "table": "table", "counter": "table",
            "screen": "screen", "monitor": "screen", "tv": "screen",
            "projection": "screen", "projector": "screen", "slide": "screen",
            "led wall": "screen", "ceiling": "ceiling", "board": "wall",
            "whiteboard": "wall", "window": "wall", "door": "wall"}
FACE = {"head": ("head", None), "face": ("face", None),
        "eye": ("face", "eye"), "eyes": ("face", "eye"),
        "iris": ("face", "iris"), "pupil": ("face", "iris"),
        "nose": ("face", "nose"), "mouth": ("face", "mouth"),
        "lips": ("face", "mouth"), "chin": ("face", "chin"),
        "forehead": ("face", "forehead"), "ear": ("face", "ear"),
        "brow": ("face", "brow"), "eyebrow": ("face", "brow")}
HAND = {"fingertip": "index_tip", "finger": "index_tip",
        "index finger": "index_tip", "thumb": "thumb_tip",
        "palm": "palm", "hand": "palm", "hands": "frame", "wrist": "wrist",
        "fingers": "frame", "pinch": "pinch"}
BODY = {"shoulder": "shoulder", "chest": "chest", "elbow": "elbow",
        "knee": "knee", "hip": "hip", "ankle": "ankle", "foot": "ankle",
        "feet": "ankle"}
RELATIONS = (
    ("behind", ("behind", "hidden behind", "tucked behind", "under the",
                "underneath")),
    ("around", ("around", "round", "circling", "orbiting", "orbit",
                "encircling", "halo")),
    ("between", ("between", "inside the frame of", "framed by")),
    ("follow", ("follow", "following", "tracks", "tracking", "riding",
                "ride", "stuck to", "glued to", "attached to", "on top of his",
                "on top of her", "pinned to his", "pinned to her")),
    ("on", ("on the", "on his", "on her", "on their", "onto", "pinned to",
            "painted on", "printed on", "across the", "along the")),
    ("in_front", ("in front of", "over the", "over his", "over her")),
    ("beside", ("next to", "beside", "by his", "by her", "near")),
)
_WORD = re.compile(r"[a-z]+(?:\s[a-z]+)?")


def _side(text):
    if re.search(r"\bleft\b", text):
        return "left"
    if re.search(r"\bright\b", text):
        return "right"
    return None


def parse(phrase):
    """{relation, target, name?, part?, side?, surface?} or None."""
    t = " " + re.sub(r"[^a-z0-9 ]+", " ", str(phrase or "").lower()) + " "
    t = re.sub(r"\s+", " ", t)
    rel = None
    for name, keys in RELATIONS:
        if any(f" {k} " in t or t.strip().startswith(k) for k in keys):
            rel = name
            break
    side = _side(t)
    out = {"relation": rel or "on", "side": side}
    # body parts first (they also say "on his ..."), longest names first
    for word in sorted(HAND, key=len, reverse=True):
        if re.search(rf"\b{word}\b", t):
            part = HAND[word]
            if rel == "between" or (word in ("hands", "fingers")
                                    and rel != "follow"):
                part = "frame"
            out.update(target="hand", part=part)
            if rel in (None, "on"):
                out["relation"] = "follow"
            return out
    for word in sorted(FACE, key=len, reverse=True):
        if re.search(rf"\b{word}s?\b", t):
            kind, part = FACE[word]
            out.update(target=kind, part=part)
            return out
    for word in sorted(BODY, key=len, reverse=True):
        if re.search(rf"\b{word}s?\b", t):
            out.update(target="pose", part=BODY[word])
            return out
    for word in sorted(SURFACES, key=len, reverse=True):
        if re.search(rf"\b{word}\b", t) and rel not in ("behind",):
            out.update(target="plane", surface=SURFACES[word], name=word)
            if rel in (None, "in_front", "beside"):
                out["relation"] = "on"
            return out
    if re.search(r"\b(him|her|them|speaker|person|subject|guy|woman|man)\b",
                 t) and rel == "behind":
        out.update(target="person")
        return out
    # an object: the noun after the relation word ("behind the laptop")
    m = re.search(r"\b(?:behind|under|underneath|on|onto|around|round|"
                  r"follow|following|over|next to|beside|near)\s+(?:the|a|"
                  r"an|his|her|their|that|this)?\s*([a-z]+(?:\s[a-z]+)?)",
                  t)
    if m:
        name = m.group(1).strip()
        name = re.sub(r"\b(the|a|an)\b", "", name).strip()
        if name:
            out.update(target="object", name=name.split()[-1]
                       if len(name.split()) > 1 and name.split()[-1] not in
                       ("bottle", "mic", "cup", "glass") else name)
            return out
    return None


def behind_params(box, W=1080, H=1920):
    """word_slam placement for a word hidden BEHIND an object whose box is
    ``box`` [x0, y0, x1, y1] (PROGRAM fractions): {x, y, width}. A wide
    object (a laptop, a desk edge, a car) hides the word's lower part — the
    word rises from behind its top edge; a tall one (a mic, a bottle, a
    door jamb) crosses the word at its middle. Without this the word sits
    where the template puts it and the object hides nothing."""
    x0, y0, x1, y1 = (float(v) for v in box)
    bw, bh = max(1e-3, x1 - x0), max(1e-3, y1 - y0)
    cx = (x0 + x1) / 2.0

    def clamp(v, lo, hi):
        return round(min(hi, max(lo, v)), 3)
    if bw * W >= 1.2 * bh * H:
        # the word's centre a little above the top edge: its lower third
        # or so goes behind the object
        return {"x": clamp(cx, 0.3, 0.7), "y": clamp(y0 - 0.02, 0.12, 0.8),
                "width": clamp(1.15 * bw, 0.45, 0.85)}
    return {"x": clamp(cx, 0.3, 0.7), "y": clamp((y0 + y1) / 2.0, 0.12, 0.8),
            "width": 0.8}


def anchor_for(parsed, which=None):
    """The anchor fields for a parsed landmark target (face, head, hand,
    pose, person-behind), or None for object / plane targets (they need a
    definition first)."""
    if not parsed:
        return None
    tgt = parsed.get("target")
    side = parsed.get("side")
    part = parsed.get("part")
    rel = parsed.get("relation")
    if tgt == "head":
        return {"to": "head", "follow": ["position", "scale"]}
    if tgt == "face":
        if part in ("eye", "iris", "ear", "brow"):
            part = f"{part}_{'r' if side == 'right' else 'l'}"
        a = {"to": f"face.{part}" if part else "face",
             "follow": ["position", "scale"]}
        if rel == "beside":
            a["offset"] = [0.9 if side != "left" else -0.9, 0.0]
        return a
    if tgt == "hand":
        a = {"to": f"hand.{part or 'palm'}", "lost": "hide",
             "follow": ["position"]}
        if side and part != "frame":
            a["which"] = side
        return a
    if tgt == "pose":
        joint = part
        if part not in ("chest",):
            # MediaPipe names the person's own sides; on screen the
            # person's left is the viewer's right
            joint = f"{part}_{'r' if side == 'left' else 'l'}"
        return {"to": f"pose.{joint}", "follow": ["position"]}
    if tgt == "person":
        return {"to": "screen", "occluders": ["person"]}
    return None
