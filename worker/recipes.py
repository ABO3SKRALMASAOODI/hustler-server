"""Device recipes: a whole creative move, bound to the moment that earns it.

Wave 1 integration (the brute-force phase runs a mid-tier model): a device
card is ONE item; a recipe is the editor's move made of the right items on
the right word with taste built in — what a premium editor does, solved
from the cut, never a house style:

  mono_until_turn     B&W from the setup, colour landing ON the turn word
                      (18@0-3.64; the owner: B&W sections with colour on the
                      drop)
  colour_kept_payoff  the world goes grey, the speaker stays in colour over
                      the payoff (40@0-23: one colour kept)
  silhouette_hold     the speaker as a flat silhouette on the Look's accent
                      field for one thought (23@1.3-4.7, 38@0.3-1.0, 37)
  negative_hits       1-frame negatives on the strongest hits of the build,
                      accelerating into the payoff (04@1.93-12.6, 20)
  white_silhouette    the speaker flashes white for two frames on the
                      payoff's onset, decaying (22@8.43, 40@4.67)
  lab_scan            a 2-frame registered strobe (photo, negative, line,
                      blueprint, thermal of the frame itself) on a "how it
                      works" or number moment (01@0-3.79)
  thermal_flip        the picture flips to thermal for a breath on a heat /
                      energy / body word (17@1.83, 33@6.0)
  light_figure        the world drops to black and the speaker glows in the
                      accent for one phrase (01@1.708, 33, 36, 41)
  echo_trail          the speaker leaves a fading trail of copies on a word
                      of speed or motion (40@18.3-19.3)
  room_dims           the room drops two stops and greys around the speaker
                      as the argument turns, the speaker untouched: a
                      spotlight made from the cut-out (review w1r: wider
                      range, the 18@10.80 exposure move as a story beat)
  world_negative      the world inverts around the speaker for two frames
                      on a hard negation ("never", "nothing"), the speaker
                      stays himself (13@6.92 turned inside out)
  colour_drain        a build drains the colour step by step (each item
                      greyer) and the colour SNAPS back on the payoff (the
                      18@0-3.64 drop, told as a countdown)

Freshness, not a house style (owner, 15 Oct: "more and better every time,
not the same"): a recipe the sibling shorts of the same batch already used
ranks lower for every use (FRESH_DECAY), so a batch of ten shorts gets ten
different signature moves, and the planner says which ones are fresh.

Restraint: every recipe is silent and zoom-free (owner rules), flash-safe
(negatives are spaced >= MIN_HIT_GAP_S, at most 3 a second), stays clear of
the payoff hold unless it IS the payoff's device, and none spans most of the
short (mono_policy: B&W is a device, never the look).

Pure apart from ``device_planner.moments`` (no database, no network): the
verbs in tools_ext/recipe_tools.py gather the inputs and commit the items
through tools_ext._write.commit (apply_edit_batch's own validation).
"""

import re

PREFIX = "rc_"
# rank() solves an asset recipe's landing with these stand-in keys; its ops
# are written by apply_recipe once the real stills exist (never shown).
STILLS_PLACEHOLDER = ("stills:photo", "stills:negative", "stills:line",
                      "stills:blueprint", "stills:thermal")
# A recipe the batch already used ranks this much lower per use.
FRESH_DECAY = 0.45
MIN_HIT_GAP_S = 0.36           # negatives: <= 3 full-frame flashes a second
MAX_MONO_SHARE = 0.4           # a mono section never takes more than this
THERMAL_WORDS = frozenset("""
heat hot warm warmer warming burn burning burned fire fires flame flames
energy power powerful engine engines fuel sun solar body bodies brain blood
heart inside internal temperature thermal infrared radiation nuclear
reactor explode explosion rocket rockets launch plasma gpu gpus chip chips
compute""".split())
SPEED_WORDS = frozenset("""
fast faster fastest speed speeds rapid rapidly quick quickly accelerate
accelerating acceleration velocity move moving moves motion run running
rush race racing sprint orbit orbital spin spinning momentum zoom""".split())
MECHANISM_WORDS = frozenset("""
how works work working mechanism system systems built build building
design designed engineering engineer code software model models data
process machine machines factory factories robot robots architecture
structure layer layers network""".split())
LIGHT_WORDS = frozenset("""
light lit glow glowing bright brilliant spark sparks electric electricity
lightning idea ideas vision genius alive soul spirit""".split())
IDENTITY_WORDS = frozenset("""
i i'm me myself who identity person people human humans man woman character
founder ceo nobody somebody everyone everybody""".split())


def _word(m):
    return str(m.get("word") or "").lower().strip(".,!?;:'\"")


def _tags(m):
    return set(m.get("tags") or ())


def _fmt_t(t):
    return round(float(t), 2)


class Recipe:
    def __init__(self, name, title, story, exemplars, families, matte=False,
                 flash=False, assets=False):
        self.name, self.title, self.story = name, title, story
        self.exemplars, self.families = exemplars, families
        self.matte, self.flash, self.assets = matte, flash, assets


RECIPES = {
    r.name: r for r in (
        Recipe("mono_until_turn", "B&W until the turn, colour lands on it",
               "a setup that flips: before/after, then/now, a 'but' that "
               "changes the story", ["18@0-3.64", "24@10.7-12"],
               ("finish",)),
        Recipe("colour_kept_payoff", "the world goes grey, the speaker "
               "stays in colour", "the payoff is about HIM (his claim, his "
               "stake): everything else drops away", ["40@0-23"],
               ("matte_fx",), matte=True),
        Recipe("silhouette_hold", "silhouette on the accent field for one "
               "thought", "identity, a role, a 'who': the person becomes "
               "the symbol for one phrase", ["23@1.3-4.7", "38@0.3-1.0",
                                             "37"], ("matte_fx",),
               matte=True),
        Recipe("negative_hits", "1-frame negatives accelerating into the "
               "payoff", "a build: a list, a run of numbers, rising "
               "stakes; the hits land on the stressed syllables",
               ["04@1.93-12.6", "20@0-12"], ("punctuation",), flash=True),
        Recipe("white_silhouette", "the speaker flashes white on the "
               "payoff", "one punchline word that should hit like a "
               "camera flash", ["22@8.43", "40@4.67", "20@9.10"],
               ("matte_fx",), matte=True, flash=True),
        Recipe("lab_scan", "registered 2-frame strobe of the frame "
               "itself (photo, negative, line, blueprint, thermal)",
               "how it works, the mechanism, the data, a technical number",
               ["01@0-3.79", "17@0-6.4"], ("punctuation",), flash=True,
               assets=True),
        Recipe("thermal_flip", "a breath of thermal on the word",
               "heat, energy, the body, what is inside", ["17@1.83",
                                                          "33@6.0"],
               ("finish",)),
        Recipe("light_figure", "the speaker becomes light on black",
               "energy, power, an idea that lights up, the moment someone "
               "becomes the symbol: the world drops to black and the figure "
               "glows in the accent", ["01@1.708", "33@6.0", "36@4-6",
                                       "41@9-11.7"], ("matte_fx",),
               matte=True),
        Recipe("echo_trail", "a fading trail of copies", "speed, motion, "
               "momentum: the word is about moving", ["40@18.3-19.3",
                                                      "22@6.0-7.3"],
               ("matte_fx",), matte=True),
        Recipe("room_dims", "the room dims around the speaker as the "
               "argument turns", "a turn, a confession, a 'but here is the "
               "thing': the world falls away and only he is lit",
               ["18@10.80", "23@1.3-4.7"], ("matte_fx",), matte=True),
        Recipe("world_negative", "the world inverts around the speaker on a "
               "hard negation", "'never', 'nothing', 'no one': the world "
               "flips for two frames, he stays himself", ["13@6.92",
                                                          "04@1.93"],
               ("matte_fx",), matte=True, flash=True),
        Recipe("colour_drain", "a build drains to grey, colour snaps back on "
               "the payoff", "a list, a countdown, rising stakes before the "
               "payoff: each item greyer, then full colour ON the payoff "
               "word", ["18@0-3.64", "24@10.7-12"], ("finish",)),
    )
}
NEGATION_WORDS = frozenset("""never nothing nobody none no not cannot can't
won't don't impossible zero nowhere neither""".split())


# ── where each recipe lands ──────────────────────────────────────────────

def _payoff_hold(plan):
    p = plan.get("payoff") or {}
    if not p:
        return None
    return float(p["moment"]["t"]), float(p.get("hold_end") or
                                          p["moment"]["t"] + 1.0)


def _in_hold(plan, t):
    h = _payoff_hold(plan)
    return bool(h and h[0] - 0.05 <= t <= h[1])


def _words(plan, vocab):
    return [m for m in plan["moments"] if m["kind"] in ("stress", "word")
            and _word(m) in vocab]


TURN_MARKERS = frozenset("""but however instead actually yet until except
unless although though whereas now suddenly then""".split())


def _turn(plan):
    """The turn of the argument: a contrast / negation in the first 70% of
    the short, at least 1.2 s in (a setup before it). Colour lands on the
    word that CARRIES the turn: the first stressed or payload word within
    1.2 s after a bare marker ('but', 'instead', 'until'), else the marked
    word itself; strong markers ('but') beat weak ones ('so')."""
    prog = plan["program_s"]
    words = [m for m in plan["moments"] if m["kind"] in ("stress", "word")]
    cands = [m for m in words if _tags(m) & {"contrast", "negation"}
             and 1.2 <= m["t"] <= 0.7 * prog and not _in_hold(plan, m["t"])]
    if not cands:
        return None

    def carrier(m):
        if _word(m) not in TURN_MARKERS:
            return m
        after = [x for x in words if 0.05 < x["t"] - m["t"] <= 1.2
                 and (x["kind"] == "stress" or _tags(x) & {
                     "payload", "number", "emotion"})
                 and not _in_hold(plan, x["t"])]
        return min(after, key=lambda x: x["t"]) if after else m

    def score(m):
        return (m["strength"] + (0.25 if _word(m) in TURN_MARKERS else 0.0),
                -m["t"])
    best = max(cands, key=score)
    return carrier(best)


def _payoff(plan):
    p = plan.get("payoff")
    return p["moment"] if p else None


def _strongest(plan, pred, before=None):
    cands = [m for m in plan["moments"] if m["kind"] in ("stress", "word")
             and pred(m) and (before is None or m["t"] < before)
             and not _in_hold(plan, m["t"])]
    return max(cands, key=lambda m: (m["strength"], -m["t"])) \
        if cands else None


def _phrase_end(m, plan, cap):
    ph = m.get("phrase")
    end = float(ph[1]) if ph else float(m["t"]) + cap
    return min(end, float(m["t"]) + cap, plan["program_s"] - 0.05)


def _bind(m):
    b = dict(m.get("bind") or {})
    return b or {"on": "time", "ref": _fmt_t(m["t"])}


def _frames(n, fps):
    return n / float(fps)


def _ink_safe(edl, t0, t1, choices):
    """device_planner.ink_safe: a flat colour laid under type keeps every
    lockup on screen readable (first readable choice), or choices[0]."""
    if not edl:
        return choices[0]
    try:
        import device_planner
        return device_planner.ink_safe(edl, t0, t1, choices) or choices[0]
    except Exception:  # noqa: BLE001 — taste advice never fails a build
        return choices[0]


def build(name, plan, *, ids=(), accent=None, variant_keys=None,
          flash_ok=False, edl=None):
    """(items, landing moment, why) for recipe ``name`` on ``plan`` (a
    device_planner.moments result), or (None, None, reason). Items are
    complete fx items (ids prefixed rc_<name>_), ready for upsert ops.
    ``edl`` (optional) lets a flat colour laid under type pick a colour
    the lockups on screen stay readable on."""
    r = RECIPES.get(name)
    if r is None:
        return None, None, f"unknown recipe {name!r}"
    fps = float(plan.get("fps") or 30.0)
    prog = float(plan["program_s"])
    taken = set(ids)
    n = [0]

    def nid():
        while True:
            n[0] += 1
            cand = f"{PREFIX}{name}_{n[0]}"
            if cand not in taken:
                taken.add(cand)
                return cand

    if name == "mono_until_turn":
        m = _turn(plan) or _payoff(plan)
        if m is None:
            return None, None, "no turn and no payoff word to land colour on"
        end = _fmt_t(m["t"])
        start = max(0.0, end - MAX_MONO_SHARE * prog)
        # the setup: from the start of the turn's sentence group, or the top
        sec = plan["sections"][m.get("section", 0)] if plan.get(
            "sections") else (0.0, end)
        start = max(start, float(sec[0]) if end - float(sec[0]) >= 1.2
                    else 0.0)
        if end - start < 1.0:
            return None, None, "the turn comes too early for a mono setup"
        return [{"id": nid(), "type": "grade_window", "stage": "world",
                 "start": _fmt_t(start), "end": end, "saturation": 0.0,
                 "contrast": 1.12, "ramp_out_f": 3, "device": "grade_section",
                 "bind": _bind(m)}], m, (
            f"grey from {start:.2f}s; colour lands ON '{m.get('word_raw') or m.get('word')}' "
            f"at {end:.2f}s (3-frame ramp ending on it)")
    if name == "colour_kept_payoff":
        m = _payoff(plan)
        if m is None:
            return None, None, "no payoff word"
        h = _payoff_hold(plan)
        start = max(0.0, float(m["t"]) - 0.5)
        end = min(prog - 0.05, max(h[1] if h else m["t"] + 1.5,
                                   start + 1.5), start + 3.5)
        return [{"id": nid(), "type": "subject", "stage": "world",
                 "start": _fmt_t(start), "end": _fmt_t(end),
                 "kind": "isolate", "scope": "subject",
                 "device": "subject_isolate", "bind": _bind(m)}], m, (
            f"grey world, speaker in colour {start:.2f}-{end:.2f}s around "
            f"the payoff '{m.get('word_raw') or m.get('word')}'")
    if name == "silhouette_hold":
        m = _strongest(plan, lambda x: _word(x) in IDENTITY_WORDS
                       or "emotion" in _tags(x)) or _turn(plan)
        if m is None:
            return None, None, "no identity, emotion or turn word"
        end = _phrase_end(m, plan, 1.4)
        if end - m["t"] < 0.5:
            end = min(prog - 0.05, m["t"] + 0.8)
        field = _ink_safe(edl, float(m["t"]), float(end),
                          [accent or "accent", "#f1ede4"])
        return [{"id": nid(), "type": "subject", "stage": "world",
                 "start": _fmt_t(m["t"]), "end": _fmt_t(end),
                 "kind": "poster", "field": field,
                 "keep_footage": False, "colour": "black",
                 "device": "subject_poster", "bind": _bind(m)}], m, (
            f"black silhouette on the accent field for "
            f"'{m.get('word_raw') or m.get('word')}' "
            f"({m['t']:.2f}-{end:.2f}s)")
    if name == "negative_hits":
        pay = _payoff(plan)
        limit = float(pay["t"]) if pay else prog
        # the BUILD into the payoff: never the hook, at most 8 s before it
        lo = max(1.5, limit - 8.0)
        hits = sorted((m for m in plan["moments"] if m["kind"] == "stress"
                       and lo <= m["t"] < limit - 0.25
                       and not _in_hold(plan, m["t"])),
                      key=lambda m: (-m["strength"], m["t"]))
        picked = []
        for m in hits:
            if all(abs(m["t"] - p["t"]) >= MIN_HIT_GAP_S for p in picked):
                picked.append(m)
            if len(picked) == 4:
                break
        picked.sort(key=lambda m: m["t"])
        if len(picked) < 2:
            return None, None, "fewer than two stressed hits before the payoff"
        items = [{"id": nid(), "type": "accent", "stage": "pre_captions",
                  "start": _fmt_t(m["t"]),
                  "end": _fmt_t(m["t"] + _frames(1, fps) + 0.004),
                  "kind": "negative", "device": "accent_negative",
                  "bind": _bind(m)} for m in picked]
        return items, picked[-1], (
            f"{len(items)} one-frame negatives on "
            + ", ".join(f"'{m.get('word_raw') or m.get('word')}'@{m['t']:.2f}"
                        for m in picked) + " (type untouched)")
    if name == "white_silhouette":
        m = _payoff(plan) or _strongest(plan, lambda x: True)
        if m is None:
            return None, None, "no payoff or stressed word"
        # a fill's decay frames are the LAST decay_f frames of its window:
        # two full frames + three decaying = a five-frame window
        end = m["t"] + _frames(2 + 3, fps) + 0.004
        # white unless a lockup on screen is white or pale (the payoff
        # landing on a white silhouette washes out): then the same hit in
        # black, and the type pops
        col = _ink_safe(edl, float(m["t"]), float(end), ["white", "black"])
        return [{"id": nid(), "type": "subject", "stage": "world",
                 "start": _fmt_t(m["t"]), "end": _fmt_t(end),
                 "kind": "fill", "colour": col, "decay_f": 3,
                 "device": "subject_fill_flash", "bind": _bind(m)}], m, (
            f"{col} silhouette for 2 frames on "
            f"'{m.get('word_raw') or m.get('word')}' at {m['t']:.2f}s, "
            "decaying over 3"
            + (" (black: the lockup on screen is light and would wash out "
               "on white)" if col == "black" else ""))
    if name == "lab_scan":
        m = _strongest(plan, lambda x: _word(x) in MECHANISM_WORDS
                       or "number" in _tags(x))
        if m is None:
            return None, None, "no mechanism or number word"
        # flash safety (owner rule): unless the user opted in to flashing,
        # three images, each held the schema's longest hold (8 frames:
        # ~0.27 s at 30 fps, 0.32 s at 25 — three image changes, counted by
        # flash_times, never more in any second) and nothing is negated;
        # opted in, the reference's 2-frame strobe
        hold = 2 if flash_ok else max(2, int(-(-fps // 3)))
        keys = list(variant_keys or [])
        if not keys and not flash_ok:
            # without stills the scan strobes the frame itself, negated
            # every other hold — a flash; flash-safe, that would be three
            # identical frames (no move at all)
            return None, None, ("the lab scan needs its registered stills "
                                "(apply_recipe makes them from the frame)")
        n_img = (len(keys) or 4) if flash_ok else min(3, len(keys) or 3)
        dur = n_img * hold / fps
        start = float(m["t"])
        if start + dur > prog - 0.05:
            return None, None, "too close to the end"
        if keys:
            frames = [{"source": {"kind": "asset", "asset_key": k},
                       "hold_f": min(8, hold)} for k in keys[:n_img]]
            fit = "register"
        else:
            # without stills: the frame itself, negated every other hold
            # (opted in only: a negative is a full-frame flash)
            frames = [{"source": {"kind": "program", "offset_s": 0.0},
                       "hold_f": min(8, hold),
                       "negate": bool(i % 2) and flash_ok}
                      for i in range(n_img)]
            fit = "cover"
        return [{"id": nid(), "type": "strobe", "stage": "pre_captions",
                 "start": _fmt_t(start), "end": _fmt_t(start + dur),
                 "frames": frames, "pattern": "sequence", "fit": fit,
                 "device": "strobe_frames", "bind": _bind(m)}], m, (
            f"{min(8, hold)}-frame registered "
            f"{'strobe' if flash_ok else 'variant sequence'} on "
            f"'{m.get('word_raw') or m.get('word')}' at {start:.2f}s")
    if name == "thermal_flip":
        m = _strongest(plan, lambda x: _word(x) in THERMAL_WORDS)
        if m is None:
            return None, None, "no heat / energy / body word"
        start = float(m["t"])
        end = min(prog - 0.05, start + 0.42)
        return [{"id": nid(), "type": "finish", "stage": "world",
                 "start": _fmt_t(start), "end": _fmt_t(end),
                 "kind": "thermal", "device": "finish_thermal",
                 "bind": _bind(m)}], m, (
            f"thermal for a breath on '{m.get('word_raw') or m.get('word')}' "
            f"({start:.2f}-{end:.2f}s)")
    if name == "light_figure":
        m = _strongest(plan, lambda x: _word(x) in THERMAL_WORDS
                       or _word(x) in LIGHT_WORDS or "emotion" in _tags(x)) \
            or _payoff(plan)
        if m is None:
            return None, None, "no energy, light or payoff word"
        start = float(m["t"])
        end = min(prog - 0.05, max(_phrase_end(m, plan, 1.2), start + 0.7))
        col = accent or "accent"
        bind = _bind(m)
        items = [
            {"id": nid(), "type": "background", "stage": "world",
             "start": _fmt_t(start), "end": _fmt_t(end), "kind": "solid",
             "colour": "black", "device": "background_swap", "bind": bind},
            {"id": nid(), "type": "subject", "stage": "world",
             "start": _fmt_t(start), "end": _fmt_t(end), "kind": "fill",
             "colour": col, "intensity": 0.55, "device": "subject_fill_flash",
             "bind": bind},
            {"id": nid(), "type": "subject", "stage": "world",
             "start": _fmt_t(start), "end": _fmt_t(end), "kind": "glow",
             "colour": col, "radius": 0.035, "intensity": 1.0,
             "device": "subject_glow", "bind": bind}]
        return items, m, (
            f"the world to black, the figure lit in the accent on "
            f"'{m.get('word_raw') or m.get('word')}' "
            f"({start:.2f}-{end:.2f}s)")
    if name == "echo_trail":
        m = _strongest(plan, lambda x: _word(x) in SPEED_WORDS)
        if m is None:
            return None, None, "no speed or motion word"
        end = _phrase_end(m, plan, 1.0)
        return [{"id": nid(), "type": "subject", "stage": "world",
                 "start": _fmt_t(m["t"]), "end": _fmt_t(max(end,
                                                              m["t"] + 0.5)),
                 "kind": "echo", "echo": {"mode": "trail",
                                          "offsets_f": [3, 6, 9],
                                          "opacity": 0.55, "decay": 0.35},
                 "device": "subject_echo", "bind": _bind(m)}], m, (
            f"a trail of 3 fading copies on "
            f"'{m.get('word_raw') or m.get('word')}' at {m['t']:.2f}s")
    if name == "room_dims":
        m = _turn(plan) or _strongest(plan, lambda x: "emotion" in _tags(x))
        if m is None:
            return None, None, "no turn or emotional word for the room to dim on"
        end = _phrase_end(m, plan, 2.4)
        end = min(prog - 0.05, max(end, m["t"] + 1.2))
        return [{"id": nid(), "type": "subject", "stage": "world",
                 "start": _fmt_t(m["t"]), "end": _fmt_t(end),
                 "kind": "exposure", "scope": "background", "ev": -1.8,
                 "saturation": 0.45, "device": "subject_exposure",
                 "bind": _bind(m)}], m, (
            f"the room drops ~2 stops and greys around him on "
            f"'{m.get('word_raw') or m.get('word')}' ({m['t']:.2f}-"
            f"{end:.2f}s); he stays lit")
    if name == "world_negative":
        pay = _payoff(plan)
        m = _strongest(plan, lambda x: _word(x) in NEGATION_WORDS
                       and "negation" in _tags(x) and x["t"] >= 1.0)
        if m is None:
            return None, None, "no hard negation ('never', 'nothing') to land on"
        if pay is not None and abs(float(pay["t"]) - float(m["t"])) < 0.3:
            return None, None, "the negation is the payoff word: it keeps its own device"
        return [{"id": nid(), "type": "subject", "stage": "world",
                 "start": _fmt_t(m["t"]),
                 "end": _fmt_t(m["t"] + _frames(2, fps) + 0.004),
                 "kind": "negate", "scope": "background",
                 "device": "subject_negate", "bind": _bind(m)}], m, (
            f"the world inverts around him for 2 frames on "
            f"'{m.get('word_raw') or m.get('word')}' at {m['t']:.2f}s")
    if name == "colour_drain":
        pay = _payoff(plan)
        if pay is None:
            return None, None, "no payoff for the colour to snap back on"
        pt = float(pay["t"])
        steps = sorted((x for x in plan["moments"]
                        if x["kind"] in ("stress", "word")
                        and _tags(x) & {"payload", "number"}
                        and max(1.0, pt - 9.0) <= x["t"] < pt - 0.4),
                       key=lambda x: x["t"])
        picked = []
        for x in reversed(steps):
            if not picked or picked[-1]["t"] - x["t"] >= 0.6:
                picked.append(x)
            if len(picked) == 3:
                break
        picked.reverse()
        if len(picked) < 3:
            return None, None, "no build of three loaded words before the payoff"
        edges = [float(x["t"]) for x in picked] + [pt]
        if (pt - edges[0]) > MAX_MONO_SHARE * prog:
            return None, None, "the build is too long a share of the short"
        sats = (0.7, 0.42, 0.14)
        items = []
        for k, (a, b) in enumerate(zip(edges, edges[1:])):
            it = {"id": nid(), "type": "grade_window", "stage": "world",
                  "start": _fmt_t(a), "end": _fmt_t(b),
                  "saturation": sats[k], "contrast": round(1.0 + 0.05 * k, 2),
                  "device": "grade_section", "bind": _bind(picked[k])}
            if k == len(sats) - 1:
                it["ramp_out_f"] = 2         # colour lands ON the payoff
            items.append(it)
        return items, pay, (
            "the colour drains in three steps on "
            + ", ".join(f"'{x.get('word_raw') or x.get('word')}'"
                        for x in picked)
            + f" and snaps back ON '{pay.get('word_raw') or pay.get('word')}' "
            f"at {pt:.2f}s")
    return None, None, f"recipe {name!r} has no builder"


# ── ranking: story fit x freshness ────────────────────────────────────────

def _is_payoff(m):
    return bool(m.get("payoff") or m.get("kind") == "word.payoff"
                or m.get("kind_extra") == "payoff")


def fit(name, plan, landing):
    """0..1: how well the story earns this recipe at ``landing``."""
    if landing is None:
        return 0.0
    tags = _tags(landing)
    w = _word(landing)
    base = float(landing.get("strength") or 0.5)
    bonus = {
        "mono_until_turn": 0.35 if tags & {"contrast", "negation"} else 0.0,
        "colour_kept_payoff": 0.25 if _is_payoff(landing) else 0.0,
        "silhouette_hold": 0.3 if w in IDENTITY_WORDS else
        0.15 if "emotion" in tags else 0.0,
        "negative_hits": 0.2 if "number" in tags else 0.1,
        "white_silhouette": 0.2 if _is_payoff(landing) else 0.0,
        "lab_scan": 0.3 if w in MECHANISM_WORDS else
        0.2 if "number" in tags else 0.0,
        "thermal_flip": 0.35 if w in THERMAL_WORDS else 0.0,
        "echo_trail": 0.3 if w in SPEED_WORDS else 0.0,
        "light_figure": 0.3 if w in LIGHT_WORDS or w in THERMAL_WORDS else
        0.1 if "emotion" in tags else 0.0,
        "room_dims": 0.3 if tags & {"contrast"} else
        0.2 if tags & {"negation", "emotion"} else 0.0,
        "world_negative": 0.3 if w in NEGATION_WORDS else 0.0,
        "colour_drain": 0.3 if _is_payoff(landing) else 0.1,
    }.get(name, 0.0)
    return round(min(1.0, 0.55 * base + bonus + 0.1), 3)


def batch_usage(edls):
    """{recipe name: shorts using it} over sibling EDLs (dicts): a recipe's
    items carry ids rc_<name>_<n>."""
    out = {}
    for e in edls or []:
        seen = set()
        for it in (e or {}).get("fx") or []:
            fid = str((it or {}).get("id") or "")
            m = re.match(rf"^{PREFIX}([a-z_]+?)_\d+$", fid)
            if m and m.group(1) in RECIPES:
                seen.add(m.group(1))
        for name in seen:
            out[name] = out.get(name, 0) + 1
    return out


def rank(plan, *, used_here=(), batch=None, matte_ok=True, flash_ok=False,
         accent=None, ids=(), edl=None):
    """[{name, title, story, at, word, why, score, fit, fresh, uses,
    exemplars, items, notes}] best first. ``used_here``: recipes already in
    this edit (never twice); ``batch``: batch_usage of the sibling shorts;
    ``matte_ok`` False drops the matte recipes (no person in shot)."""
    out = []
    for name, r in RECIPES.items():
        if name in set(used_here):
            continue
        if r.matte and matte_ok is False:
            continue
        items, landing, why = build(
            name, plan, ids=ids, accent=accent, flash_ok=flash_ok, edl=edl,
            variant_keys=STILLS_PLACEHOLDER if r.assets else None)
        if not items:
            continue
        if r.assets:
            items = None        # apply_recipe makes the stills, then writes
        f = fit(name, plan, landing)
        uses = int((batch or {}).get(name, 0))
        fresh = FRESH_DECAY ** uses
        notes = []
        if r.matte and matte_ok is None:
            notes.append("needs the person matte: measuring until it is "
                         "ready (previews show a stand-in)")
        if r.assets:
            notes.append("apply_recipe makes the registered stills first")
        out.append({"name": name, "title": r.title, "story": r.story,
                    "at": _fmt_t(landing["t"]),
                    "word": landing.get("word_raw") or landing.get("word"),
                    "why": why, "fit": f, "uses": uses,
                    "fresh": uses == 0, "score": round(f * fresh, 3),
                    "exemplars": r.exemplars, "items": items,
                    "notes": notes})
    out.sort(key=lambda c: (-c["score"], c["at"], c["name"]))
    return out
