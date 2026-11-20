"""The Director: one model call a film (one a chapter in long-form) that chooses, moment by moment, which devices of the
scene engine's palette land where. It reads a moment sheet (the spoken words numbered with their times and the voice's
stress, the pictures on one contact sheet with what signal measured of them, the take, the style as a starting palette,
the device sequences of the films delivered lately) and the palette's menu, and answers DIRECTOR_CONTRACT 4:

    beats     [{role: hook|name|figure|list|turn|past|quote|contrast|payoff|close, w, seg, word, energy: strobe|brisk|hold}]
    sections  [{start: word, end: word, segs: [sentences it touches], surface: colour|mono|gate|crt|bloom|thermal|...,
                layout: bleed|window|prints|collage|grid|split|type, energy: strobe|brisk|hold}]   (they tile the film)
    asks      [{dev, beat, at: {w, seg, word}, until?: {w, seg, word}, args: {...}, pics: [ids]}]      (overlap and stack)
    and the older fields every builder reads: display, past_segs, payoff_seg, turn_seg, story_turn_seg, arc

A bad answer is repaired, never refused: a word not said is snapped to the nearest said word of its sentence, a device
name nothing knows is mapped to its family (the palette resolves it), an answer with no JSON is asked again with the
parse error shown, and when the provider still fails a signal director lays seeded devices on the voice's stress peaks
and the beats it can find by signal, away from the ledger's recent sequences, so the film still carries devices. Every
repair is logged in the answer. The call goes through llm.ask (the run's provider, its dollars recorded) and is cached
at supply/director.json.

No reference's timeline is ever in the prompt (owner, 14 Nov 2026: things are spontaneous): principles only.
"""
import base64
import difflib
import hashlib
import io
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CONTRACT = 4
ROLES = ("hook", "name", "figure", "list", "turn", "past", "quote", "contrast", "payoff", "close")
ENERGIES = ("strobe", "brisk", "hold")
LAYOUTS = ("bleed", "window", "prints", "collage", "grid", "split", "type")
SURFACES = ("colour", "mono", "gate", "crt", "bloom", "thermal", "threshold", "halftone", "xray", "blueprint", "night_vision",
            "posterise", "solarise", "line", "neg", "rew", "paper", "xerox", "led", "projector")
# seconds one framing may hold, by a section's energy (scene.pace's band): strobe down to two frames, hold up to about 4 s
BAND_OF = {"strobe": (0.067, 0.6), "brisk": (0.5, 1.6), "hold": (1.4, 4.0)}

PRINCIPLES = """How the best vertical edits keep a stranger watching (principles, never a template; every film finds its own):
- The strangest device of the film is on screen at frame 0, and the hook sentence is over by 3-4 s.
- The name lands second (3-10 s), in two sizes.
- Every figure performs: it counts, stacks or lands with a hit; never a figure as small caption words.
- Every turn changes something you can see: the surface, the polarity, the pace, or a type card.
- Lists burst: one picture or one hit per item, fast.
- The pace alternates strobe against hold: some 3 s windows carry one cut, others ten or twelve.
- The payoff slows down, holds, and calls back to the opening.
- Stack devices on one moment (a flip, then a duotone echo, then a word cut through the picture), and use most of the
  palette over a film rather than one device repeated.
- At least one moment combines devices in a way you have not seen any editor do.
- Restraint is also a device: a hold after a burst lands harder than a burst after a burst. Never decorate every word."""

SYSTEM = ("You are the Director of a vertical short film. You choose, moment by moment, which devices of the engine's "
          "palette land on which spoken word, so the film is the most creative edit of this material anyone has made: "
          "not a video with captions, but a film whose every beat does something to the picture. You see the spoken "
          "words numbered with their times (^ marks the voice's stress peaks), the pictures the film has (one contact "
          "sheet, each picture with its id and what signal measured), the take, the style's starting palette, and what "
          "the films delivered lately did (do something else). You answer in JSON only.\n\n" + PRINCIPLES + "\n\n"
          "Return JSON only: {\"beats\": [{\"role\": one of hook|name|figure|list|turn|past|quote|contrast|payoff|close, "
          "\"w\": the word number it lands on, \"word\": that word as said, \"energy\": strobe|brisk|hold}], "
          "\"sections\": [{\"from\": first word number, \"to\": last word number (runs of words in order, covering the film "
          "from word 0 to the last word, no gaps; a section is often a few seconds: surfaces switch, colour is the base they "
          "cut away from), \"surface\": "
          "colour|mono|gate|crt|bloom|thermal|threshold|halftone|xray|blueprint|night_vision|posterise|solarise|line|neg, "
          "\"layout\": bleed|window|prints|collage|grid|split|type, \"energy\": strobe|brisk|hold}], "
          "\"asks\": [{\"dev\": a device name from the palette, \"beat\": index into beats or null, \"at\": {\"w\": word "
          "number, \"word\": the word}, \"until\": {\"w\": word number, \"word\": the word} (spans only), \"args\": {the "
          "device's own fields from its card, e.g. \"mode\", \"surface\", \"run\", \"n\", \"c\", \"part\"; every time field of a card "
          "(f, at, until, cuts, steps, from, stress, sections[].at, marks[].f, picks[].f) is a WORD NUMBER, a [first, last] pair of "
          "word numbers is a stretch, and any picture field (pic, pool) a picture id: the engine turns them into frames and assets}, \"pics\": [picture ids it uses]}; 10 to 30 asks for a minute, stacked where a "
          "moment deserves it], "
          "\"display\": [up to six words or short phrases to set large, ranked best first: {\"seg\": sentence number, \"text\": "
          "one to three consecutive words exactly as said, \"role\": hook|turn|payoff|figure|name|contrast, \"key\": the "
          "one word set largest}], \"past_segs\": [sentence numbers that tell of the past], \"payoff_seg\": the sentence "
          "number where the payoff begins, \"turn_seg\": the sentence number where the story turns before the payoff or "
          "null, \"arc\": [only for calendar years said aloud: {\"seg\", \"word\": the year as said in digits, \"year\", "
          "\"stakes\": -1..1}], \"why\": one sentence on the film's idea}")


def shell_llm():
    """The shell's llm.py under a name of its own (the worker has an `llm` module too); the same module wf1_short loads."""
    import importlib.util
    name = "valmera_shell_llm"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "llm.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return sys.modules[name]


# --------------------------------------------------------------------- the sheet

def _palette():
    """The engine's palette module (worker/scene/palette), or None when the worker is not at hand."""
    try:
        import brand
        wk = brand.worker_dir()
        for p in (os.path.dirname(wk), wk):
            if p not in sys.path:
                sys.path.append(p)
        from scene import palette
        palette._discover()
        return palette
    except Exception:      # noqa: BLE001 - the caller reads the fixed defaults
        return None


def palette_menu():
    """The palette's cards from the engine itself (worker/scene/palette), so the Director never sees a device the
    engine cannot draw."""
    try:
        import brand
        wk = brand.worker_dir()
        for p in (os.path.dirname(wk), wk):
            if p not in sys.path:
                sys.path.append(p)
        from scene import palette
        return palette.menu(), sorted(palette.DEVICES)
    except Exception as e:      # noqa: BLE001 - the cards are fixed text; without the engine the model reads the names alone
        return f"(palette menu unavailable: {e!r})", []


def contact_sheet(pictures, out, side=220, cols=6, most=36):
    """One JPEG of the film's pictures with their ids, for the model to see. pictures: [{"id", "path"}] (a still of a
    clip, or a photograph). Returns the path, or None when nothing could be read."""
    from PIL import Image, ImageDraw, ImageOps
    tiles = []
    for p in pictures[:most]:
        try:
            with Image.open(p["path"]) as im:
                t = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (side, int(side * 1.25)))
        except Exception:      # noqa: BLE001 - an unreadable picture is left off the sheet
            continue
        d = ImageDraw.Draw(t)
        d.rectangle([0, 0, 58, 26], fill=(0, 0, 0))
        d.text((5, 5), str(p["id"]), fill=(255, 230, 80))
        tiles.append(t)
    if not tiles:
        return None
    rows = -(-len(tiles) // cols)
    sheet = Image.new("RGB", (cols * (side + 4), rows * (int(side * 1.25) + 4)), (16, 16, 16))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * (side + 4), (k // cols) * (int(side * 1.25) + 4)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out, quality=82)
    return out


def words_sheet(sentences, stress_w=()):
    """The spoken words, numbered, sentence by sentence, with their times; ^ after a stressed word."""
    hot = set(int(w) for w in stress_w)
    out = []
    for s in sentences:
        ws = s.get("words") or []
        t0 = ws[0]["t0"] if ws else s.get("t0", 0.0)
        t1 = ws[-1]["t1"] if ws else s.get("t1", 0.0)
        toks = []
        for w in ws:
            toks.append(f"{w['w']}#{w['n']}" + ("^" if w["n"] in hot else ""))
            if w.get("pause"):
                toks.append(f"|{w['t1']:.1f}s|")
        out.append(f"S{s['i']} [{t0:.1f}-{t1:.1f}s]: " + " ".join(toks))
    return "\n".join(out)


def moment_sheet(sentences, stress_w, pictures, take, style, workflow, dur_s, recent, title=None, hook=None, payoff=None):
    pics = []
    for p in pictures:
        tags = [p.get("kind") or "photo"]
        for k in ("at", "motion", "faces", "luma", "hue", "rounds", "text", "seg"):
            if p.get(k) not in (None, "", []):
                tags.append(f"{k} {p[k]}")
        pics.append(f"{p['id']}: {p.get('label') or ''} ({', '.join(tags)})")
    st = style or {}
    lines = [f"# The film: {workflow}, {dur_s:.1f} s" + (f", '{title}'" if title else "")]
    if hook:
        lines.append(f"Hook line: {hook}")
    if payoff:
        lines.append(f"Payoff line: {payoff}")
    if st:
        lines.append(f"# Starting palette (the style; a start, not a rule): {json.dumps(st, ensure_ascii=False)}")
    if take:
        lines.append(f"# The take: {json.dumps(take, ensure_ascii=False)}")
    lines.append("\n# Spoken words (word#number; ^ = a stress peak of the voice; |t| = a pause)")
    lines.append(words_sheet(sentences, stress_w))
    lines.append("\n# Pictures (ids on the contact sheet)")
    lines.append("\n".join(pics) if pics else "(none: the film stands on the take, type and treatments of the take)")
    lines.append("\n# What the films delivered lately did (do something else)")
    lines.append("\n".join(f"- {r}" for r in recent) if recent else "- (nothing recorded yet)")
    return "\n".join(lines)


# --------------------------------------------------------------------- repair

def _norm(s):
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def snap(addr, sentences, words):
    """A word address {w, seg, word} made real: the numbered word when it is said where the address says, else the
    word of that sentence nearest in spelling, else the sentence's first word. Returns (address, repair or None)."""
    if not words:
        return None, "no words"
    addr = addr if isinstance(addr, dict) else ({"w": addr} if isinstance(addr, int) else {})
    w = addr.get("w")
    seg = addr.get("seg")
    text = addr.get("word")
    if isinstance(w, int) and 0 <= w < len(words):
        if not text or _norm(words[w]["w"]) == _norm(text) or _norm(text) in _norm(words[w]["w"]):
            return dict(w=w, seg=words[w]["seg"], word=words[w]["w"]), None
    nseg = len(sentences)
    if not isinstance(seg, int):
        seg = words[w]["seg"] if isinstance(w, int) and 0 <= w < len(words) else 0
    seg = min(max(int(seg), 0), max(nseg - 1, 0))
    pool = [x for x in words if x["seg"] == seg] or words
    if text:
        best = max(pool, key=lambda x: (difflib.SequenceMatcher(None, _norm(x["w"]), _norm(text)).ratio(),
                                        -abs(x["n"] - (w if isinstance(w, int) else x["n"]))))
    elif isinstance(w, int):
        best = min(words, key=lambda x: abs(x["n"] - w))
    else:
        best = pool[0]
    return dict(w=best["n"], seg=best["seg"], word=best["w"]), f"snapped {addr} to word {best['n']} '{best['w']}'"


def repair(d, sentences, words, names=(), seed=0):
    """The model's answer made usable, every change logged. Never refuses: what cannot be read is replaced."""
    rep = []
    d = d if isinstance(d, dict) else {}
    nseg = len(sentences)
    beats = []
    for b in d.get("beats") or []:
        if not isinstance(b, dict):
            continue
        a, why = snap(dict(w=b.get("w"), seg=b.get("seg"), word=b.get("word")), sentences, words)
        if a is None:
            continue
        if why:
            rep.append(f"beat: {why}")
        role = b.get("role") if b.get("role") in ROLES else "quote"
        beats.append(dict(role=role, energy=b.get("energy") if b.get("energy") in ENERGIES else "brisk", **a))
    sections = []
    last = len(words) - 1
    for s_ in d.get("sections") or []:
        if not isinstance(s_, dict):
            continue
        if s_.get("from") is not None or s_.get("to") is not None:
            fa, _ = snap(s_.get("from") if isinstance(s_.get("from"), dict) else dict(w=s_.get("from")), sentences, words)
            ta, _ = snap(s_.get("to") if isinstance(s_.get("to"), dict) else dict(w=s_.get("to")), sentences, words)
            if fa is None or ta is None:
                continue
            w0, w1 = min(fa["w"], ta["w"]), max(fa["w"], ta["w"])
        else:                              # sentences named: their words
            segs = [int(i) for i in s_.get("segs") or [] if isinstance(i, int) and 0 <= i < nseg]
            ws = [w["n"] for w in words if w["seg"] in segs]
            if not ws:
                continue
            w0, w1 = min(ws), max(ws)
        lay = s_.get("layout") if s_.get("layout") in LAYOUTS else "bleed"
        if s_.get("layout") not in LAYOUTS:
            rep.append(f"section {w0}-{w1}: layout {s_.get('layout')!r} read as bleed")
        sections.append(dict(start=w0, end=w1, surface=str(s_.get("surface") or "colour").lower(), layout=lay,
                             energy=s_.get("energy") if s_.get("energy") in ENERGIES else "brisk"))
    sections.sort(key=lambda x: (x["start"], x["end"]))
    tiled, at = [], 0                      # the sections tile the film: an overlap is cut, a gap is colour
    for x in sections:
        if x["end"] < at:
            rep.append(f"section {x['start']}-{x['end']} lay inside an earlier one")
            continue
        if x["start"] > at:
            tiled.append(dict(start=at, end=x["start"] - 1, surface="colour", layout="bleed", energy="brisk"))
            rep.append(f"words {at}-{x['start'] - 1} had no section: colour")
        tiled.append(dict(x, start=max(x["start"], at)))
        at = x["end"] + 1
    if words and at <= last:
        tiled.append(dict(start=at, end=last, surface="colour", layout="bleed", energy="brisk"))
        if sections:
            rep.append(f"words {at}-{last} had no section: colour")
    for x in tiled:
        x["segs"] = sorted({words[w]["seg"] for w in range(x["start"], x["end"] + 1)})
    sections = tiled
    asks = []
    known = set(names or ())
    for k, a in enumerate(d.get("asks") or []):
        if not isinstance(a, dict) or not a.get("dev"):
            continue
        at, why = snap(a.get("at") if a.get("at") is not None else dict(w=a.get("w"), seg=a.get("seg"), word=a.get("word")), sentences, words)
        if at is None:
            continue
        if why:
            rep.append(f"ask {k} {a.get('dev')}: {why}")
        row = dict(dev=str(a["dev"]), beat=a.get("beat") if isinstance(a.get("beat"), int) and 0 <= a["beat"] < len(beats) else None,
                   at=at, args=a.get("args") if isinstance(a.get("args"), dict) else {},
                   pics=[str(p) for p in a.get("pics") or [] if p is not None])
        if a.get("until") is not None:
            un, why = snap(a["until"], sentences, words)
            if un is not None and un["w"] >= at["w"]:
                row["until"] = un
            elif un is not None:
                rep.append(f"ask {k} {a.get('dev')}: until before at, dropped the until")
        if known and row["dev"] not in known:
            rep.append(f"ask {k}: device {row['dev']!r} is not in the palette; the palette maps it to its family")
        asks.append(row)
    display = []
    for r in d.get("display") or []:
        if isinstance(r, dict) and r.get("text") and isinstance(r.get("seg"), int) and 0 <= r["seg"] < nseg:
            role = r.get("role") if r.get("role") in ("hook", "turn", "payoff", "figure", "name", "contrast") else "hook"
            display.append(dict(r, role=role, accent=r.get("accent", role in ("figure", "payoff"))))
    arc = []
    for r in d.get("arc") or []:
        if not isinstance(r, dict):
            continue
        a, why = snap(dict(w=r.get("w"), seg=r.get("seg"), word=r.get("word") or r.get("year")), sentences, words)
        if a is None:
            continue
        try:
            year = int(r.get("year"))
        except (TypeError, ValueError):
            year = None
        arc.append(dict(r, w=a["w"], seg=a["seg"], word=a["word"], year=year))
    arc = arc[:6]
    payoff = d.get("payoff_seg") if isinstance(d.get("payoff_seg"), int) and 0 <= d["payoff_seg"] < nseg else (nseg - 1 if nseg else None)
    turn = d.get("turn_seg") if isinstance(d.get("turn_seg"), int) and 0 <= d["turn_seg"] < nseg else None
    if payoff is not None and turn is not None and turn >= payoff:
        turn = None
    return dict(contract=CONTRACT, beats=beats, sections=sections, asks=asks, display=display[:6],
                past_segs=[int(i) for i in d.get("past_segs") or [] if isinstance(i, int) and 0 <= i < nseg],
                payoff_seg=payoff, turn_seg=payoff, story_turn_seg=turn,
                arc=arc, why=str(d.get("why") or "")[:400]), rep


# --------------------------------------------------------------------- the signal director

POOL = ("white", "neg", "tint", "blur_in", "spotlight", "neg_split", "variant", "word_blend", "black", "snap", "settle", "whip", "glimpse")
SPREADS = ("grid", "collage")      # a sentence dense with stress fills a layout one tile (or print) per stressed word
VARIANT_MODES = ("thermal", "threshold", "halftone", "xray", "blueprint", "posterise", "solarise", "line", "mosaic", "night_vision")
FINISHES = ("mono", "gate", "crt", "bloom", "thermal", "halftone", "threshold", "xray")


def _least_used(choices, recent, rng):
    """The choices ordered by how rarely the recent films used them, ties broken by the seed."""
    text = " ".join(recent or [])
    return sorted(choices, key=lambda c: (text.count(c), rng.random()))


def signal_director(sentences, words, stress_w, pictures=(), seed=0, recent=(), dur_s=None):
    """A director that reads no model: devices on the voice's stress peaks and the beats signal can find (the first
    stressed word, the strongest stress of the middle, the payoff sentence's first word), seeded by the film and kept
    away from the recent films' sequences. The film still carries devices when the provider fails."""
    rng = random.Random(int(seed) ^ 0xD1EC)
    nseg = len(sentences)
    if not words or not nseg:
        return dict(contract=CONTRACT, beats=[], sections=[], asks=[], display=[], past_segs=[], payoff_seg=None, turn_seg=None,
                    story_turn_seg=None, arc=[], why="signal director: no words")
    hot = [int(w) for w in stress_w if 0 <= int(w) < len(words)]
    first = [w for w in hot if words[w]["seg"] == 0] or [0]
    payoff = nseg - 1
    pay_w = next((x["n"] for x in words if x["seg"] == payoff), len(words) - 1)
    mid = [w for w in hot if 0 < words[w]["seg"] < payoff] or [w for w in hot if words[w]["n"] < pay_w] or [len(words) // 2]
    turn_w = mid[len(mid) // 2]
    beats = [dict(role="hook", w=first[0], seg=words[first[0]]["seg"], word=words[first[0]]["w"], energy="strobe")]
    if words[turn_w]["seg"] != 0:
        beats.append(dict(role="turn", w=turn_w, seg=words[turn_w]["seg"], word=words[turn_w]["w"], energy="hold"))
    beats.append(dict(role="payoff", w=pay_w, seg=payoff, word=words[pay_w]["w"], energy="hold"))
    # one beat on a stress peak about every 3 s between them
    last_t = -9.0
    for w in hot:
        t = words[w]["t0"]
        if t - last_t >= 3.0 and all(abs(w - b["w"]) > 2 for b in beats):
            beats.append(dict(role="quote", w=w, seg=words[w]["seg"], word=words[w]["w"], energy="brisk"))
            last_t = t
    beats.sort(key=lambda b: b["w"])
    pool = _least_used(POOL, recent, rng)
    modes = _least_used(VARIANT_MODES, recent, rng)
    asks = []
    hk = next(k for k, b in enumerate(beats) if b["role"] == "hook")
    open_stack = rng.choice((("variant", dict(mode=modes[0], flips=3)), ("neg", dict(run=3)), ("tint", dict(c="cyan"))))
    asks.append(dict(dev=open_stack[0], beat=hk, at=dict(w=0, seg=0, word=words[0]["w"]),
                     until=dict(w=first[0], seg=words[first[0]]["seg"], word=words[first[0]]["w"]), args=open_stack[1], pics=[]))
    asks.append(dict(dev="white", beat=hk, at=dict(w=first[0], seg=0, word=words[first[0]]["w"]), args={}, pics=[]))
    k = 0
    for bi, b in enumerate(beats):
        a = dict(w=b["w"], seg=b["seg"], word=b["word"])
        if b["role"] == "quote":
            dev = pool[k % len(pool)]
            k += 1
            args = dict(mode=modes[k % len(modes)]) if dev == "variant" else dict(c=rng.choice(("cyan", "magenta"))) if dev == "tint" else \
                dict(size=rng.choice((1.2, 1.3, 1.4))) if dev == "snap" else dict(dir=rng.choice(("in", "in", "out"))) if dev == "settle" else {}
            asks.append(dict(dev=dev, beat=bi, at=a, args=args, pics=[]))
            if k % 3 == 0:
                asks.append(dict(dev="blur_in", beat=bi, at=a, args={}, pics=[]))
        elif b["role"] == "turn":
            asks.append(dict(dev="neg", beat=bi, at=a, args=dict(n=2), pics=[]))
            asks.append(dict(dev="spotlight", beat=bi, at=a, args={}, pics=[]))
        elif b["role"] == "payoff":
            prev = max(0, b["w"] - 1)
            asks.append(dict(dev="black", beat=bi, at=dict(w=prev, seg=words[prev]["seg"], word=words[prev]["w"]), args=dict(n=2), pics=[]))
            asks.append(dict(dev="white", beat=bi, at=a, args={}, pics=[]))
    # a burst on the strongest stress between the hook and the payoff, about a second of pictures from the pool
    # nearest it; and the sentence densest with stress spreads into a layout, one tile per stressed word
    hook_t = words[first[0]]["t0"] + 2.0
    mid_hot = [w for w in hot if words[w]["t0"] >= hook_t and w < pay_w - 2]
    burst_w = None
    if mid_hot:
        burst_w = rng.choice(mid_hot[:max(len(mid_hot) // 2, 1)])
        end = next((x["n"] for x in words if x["n"] > burst_w and x["t0"] >= words[burst_w]["t0"] + rng.uniform(0.9, 1.4)), None)
        if end is not None and end < pay_w:
            asks.append(dict(dev="burst", beat=None, at=dict(w=burst_w, seg=words[burst_w]["seg"], word=words[burst_w]["w"]),
                             until=dict(w=end, seg=words[end]["seg"], word=words[end]["w"]), args={}, pics=[]))
    dense = []
    for sg in range(1, payoff):
        ws = [x for x in words if x["seg"] == sg]
        if len(ws) < 4 or (burst_w is not None and ws[0]["n"] <= burst_w <= ws[-1]["n"]):
            continue
        sec = max(ws[-1]["t1"] - ws[0]["t0"], 0.5)
        steps = [w for w in hot if words[w]["seg"] == sg]
        if len(steps) < 3:                                   # too few peaks: every word a third of a second apart
            steps, last_t = [], -9.0
            for x in ws:
                if x["t0"] - last_t >= 0.33:
                    steps.append(x["n"])
                    last_t = x["t0"]
        dense.append((len([w for w in hot if words[w]["seg"] == sg]) / sec, sg, steps[:9], ws))
    if dense:
        _, sg, steps, ws = max(dense, key=lambda q: (q[0], -q[1]))
        if len(steps) >= 3:
            lay = _least_used(SPREADS, recent, rng)[0]
            asks.append(dict(dev=lay, beat=None, at=dict(w=steps[0], seg=sg, word=words[steps[0]]["w"]),
                             until=dict(w=ws[-1]["n"], seg=sg, word=ws[-1]["w"]), args=dict(steps=list(steps)), pics=[]))
    # sections: the hook's sentence strobes, the payoff holds; one middle stretch gets a surface of its own
    sections = []
    fin = _least_used(FINISHES, recent, rng)[0]
    hook_end = max(first[0], min(len(words) - 1, next((x["n"] for x in words if x["t0"] >= words[0]["t0"] + 3.5), first[0])))
    sections.append(dict(start=0, end=hook_end, surface="colour", layout="bleed", energy="strobe"))
    tw = next(b["w"] for b in beats if b["role"] in ("turn", "payoff"))
    if tw > hook_end + 1:
        t_end = next((x["n"] for x in words if x["n"] > tw and x["t0"] >= words[tw]["t0"] + 4.0), tw)
        sections.append(dict(start=hook_end + 1, end=tw - 1, surface="colour", layout="bleed", energy="brisk"))
        if t_end < pay_w:
            sections.append(dict(start=tw, end=t_end, surface=fin, layout="bleed", energy="hold"))
            sections.append(dict(start=t_end + 1, end=pay_w - 1, surface="colour", layout="bleed", energy="brisk")) if t_end + 1 <= pay_w - 1 else None
        else:
            sections[-1]["end"] = pay_w - 1
    sections.append(dict(start=pay_w, end=len(words) - 1, surface="colour", layout="bleed", energy="hold"))
    sections = [x for x in sections if x["end"] >= x["start"]]
    display = [dict(seg=b["seg"], text=b["word"], role="hook" if b["role"] == "hook" else "payoff" if b["role"] == "payoff" else
                    "turn" if b["role"] == "turn" else "hook", key=b["word"]) for b in beats if len(_norm(b["word"])) >= 4][:6]
    return dict(contract=CONTRACT, beats=beats, sections=sections, asks=asks, display=display, past_segs=[], payoff_seg=payoff,
                turn_seg=payoff, story_turn_seg=next((b["seg"] for b in beats if b["role"] == "turn"), None), arc=[],
                why="signal director: devices on the voice's stress peaks", by="signal")


# --------------------------------------------------------------------- the call

def numbered(sentences, word_rows):
    """Words of the film numbered in order, each with its sentence, times and a pause flag (a gap of 0.35 s or more
    after it). word_rows: [{"w", "t0", "t1", "seg"}] in order (a film's clock)."""
    words = []
    for n, w in enumerate(word_rows):
        words.append(dict(n=n, w=str(w["w"]), t0=float(w["t0"]), t1=float(w["t1"]), seg=int(w.get("seg", 0))))
    for a, b in zip(words, words[1:]):
        a["pause"] = b["t0"] - a["t1"] >= 0.35
    out = []
    for s in sentences:
        out.append(dict(s, words=[w for w in words if w["seg"] == s["i"]]))
    return out, words


def stress_words(words, stress_t, top=0.0):
    """Word numbers under the voice's stress peaks: [(seconds, strength)] -> the word each falls in (strongest first)."""
    out = []
    for t, s in sorted(stress_t, key=lambda q: -q[1]):
        if s < top:
            continue
        w = next((x["n"] for x in words if x["t0"] - 0.05 <= t <= x["t1"] + 0.05), None)
        if w is not None and w not in out:
            out.append(w)
    return sorted(out[:max(6, len(words) // 6)])


def direct(supply, sentences, word_rows, stress_t=(), pictures=(), take=None, style=None, workflow="podcast", dur_s=None,
           title=None, hook=None, payoff=None, seed=0, recent=None, ask=None, effort="high", cache_name="director.json", log=None):
    """The film's per-moment device score (DIRECTOR_CONTRACT 4), cached at supply/<cache_name>.

    sentences  [{"i", "text"}]; word_rows [{"w", "t0", "t1", "seg"}] in order (the film's own clock, seconds);
    stress_t   [(seconds, strength)]; pictures [{"id", "path", "label", "kind", "motion", "faces", "luma", ...}];
    ask        llm.ask (the run's provider); recent: the ledger's recent sequences (devices_ledger.recent)."""
    path = os.path.join(supply, cache_name)
    if os.path.exists(path):
        try:
            got = json.load(open(path))
            if got.get("contract") == CONTRACT:
                return got
        except (OSError, ValueError):
            pass
    say = log or (lambda m: None)
    sents, words = numbered(sentences, word_rows)
    hot = stress_words(words, stress_t)
    if recent is None:
        try:
            import devices_ledger
            recent = devices_ledger.recent(12)
        except Exception:      # noqa: BLE001 - no ledger yet
            recent = []
    dur = float(dur_s or (words[-1]["t1"] if words else 0.0))
    menu, names = palette_menu()
    sheet = moment_sheet(sents, hot, list(pictures), take, style, workflow, dur, recent, title, hook, payoff)
    content = [{"type": "text", "text": sheet + "\n\n# The palette (one card a device: its fields, then what it does and the moment it amplifies)\n" + menu}]
    cs = contact_sheet(list(pictures), os.path.join(supply, "director_sheet.jpg")) if pictures else None
    if cs:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(open(cs, "rb").read()).decode()}})
    seed = int(seed) or int(hashlib.sha1(sheet.encode()).hexdigest()[:8], 16)
    spend, repairs, d, by = [], [], None, "model"
    if ask is None and os.environ.get("PYTEST_CURRENT_TEST"):
        repairs.append("a test never reaches a real model: the signal director")
    elif ask is None:
        try:
            ask = shell_llm().ask
        except Exception as e:      # noqa: BLE001
            repairs.append(f"no model client: {e!r}")
    if ask is not None:
        msg = content
        for attempt in range(2):
            try:
                text, use = ask(SYSTEM, msg, effort=effort, max_tokens=24000)
                spend.append(dict(stage="score", what="director: devices by moment", **(use or {})))
            except Exception as e:      # noqa: BLE001 - a provider failure: the signal director lays the devices
                repairs.append(f"provider failed: {type(e).__name__}: {str(e)[:200]}")
                break
            try:
                d = shell_llm().json_block(text)
                break
            except Exception as e:      # noqa: BLE001 - asked again with the parse error shown
                repairs.append(f"answer {attempt + 1} had no JSON: {str(e)[:120]}")
                msg = content + [{"type": "text", "text": f"Your answer could not be read as JSON ({str(e)[:200]}). Answer again "
                                                          "with the JSON object only."}]
                d = None
    if not isinstance(d, dict):
        d = signal_director(sents, words, hot, pictures, seed, recent, dur)
        by = "signal"
    out, rep = repair(d, sents, words, names, seed)
    if by == "model" and not out["asks"]:
        sig = signal_director(sents, words, hot, pictures, seed, recent, dur)
        out["asks"], out["beats"] = sig["asks"], out["beats"] or sig["beats"]
        rep.append("the answer laid no device: the signal director's asks were laid")
    out.update(by=by, repairs=repairs + rep, seed=seed, recent=recent, stress_words=hot,
               spend=spend[-1] if len(spend) == 1 else (dict(spend[-1], usd=round(sum(float(s.get("usd") or 0) for s in spend), 5)) if spend else None))
    for r in out["repairs"]:
        say(f"director repair: {r}")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    return out


# --------------------------------------------------------------------- a built film's own pictures

def still_of(path, out_dir, ffmpeg="ffmpeg", t=None):
    """A still to show the model: the picture itself, or one frame of a clip (its middle, or ``t``)."""
    if str(path).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return path
    import subprocess
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, hashlib.sha1(f"{path}@{t}".encode()).hexdigest()[:12] + ".jpg")
    if not os.path.exists(out):
        r = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{float(t or 0.5):.3f}", "-i", path, "-frames:v", "1",
                            "-vf", "scale=360:-2", out], capture_output=True)
        if r.returncode != 0 or not os.path.exists(out):
            return None
    return out


def film_pictures(l1, placed, out_dir, ffmpeg="ffmpeg", fps=30.0, most=36):
    """The pictures a built film placed (its builder's record: [{"f", "path", "kind", "shows"|"name", "src"}]), as the
    Director's contact sheet rows, and {id: asset key}."""
    by_path = {}
    for k, a in (l1.get("assets") or {}).items():
        if a.get("path") and k not in ("matte",):
            by_path.setdefault(a["path"], k)
    out, ids, seen = [], {}, set()
    for p in placed or []:
        path = p.get("path")
        key = by_path.get(path)
        if not key or path in seen:
            continue
        seen.add(path)
        src = p.get("src") or [0.5, 0.5]
        st = still_of(path, out_dir, ffmpeg, (float(src[0]) + float(src[1])) / 2 if src else None)
        if not st:
            continue
        pid = f"P{len(out)}"
        f = p.get("f") or [0, 0]
        out.append(dict(id=pid, path=st, kind=p.get("kind") or "photo", label=str(p.get("shows") or p.get("name") or "")[:60],
                        at=f"{f[0] / fps:.1f}-{f[1] / fps:.1f}s"))
        ids[pid] = key
        if len(out) >= most:
            break
    # the supply's pictures the film did not place (the scene's pool): the Director may choose them by id too; a pool
    # id resolves to the picture's path, which the palette's devices read from the pool
    for r in l1.get("pool") or []:
        if len(out) >= most:
            break
        path = r.get("path")
        if not path or path in seen or path in by_path:
            continue
        seen.add(path)
        clip = str(r.get("kind") or "").lower() in ("clip", "video")
        st = still_of(path, out_dir, ffmpeg, float(r.get("seconds") or 1.0) / 2 if clip else None)
        if not st:
            continue
        pid = f"P{len(out)}"
        t = r.get("t") if isinstance(r.get("t"), (list, tuple)) else None
        row = dict(id=pid, path=st, kind=("clip" if clip else "photo") + ", supply, not placed",
                   label=str(r.get("subject") or r.get("shows") or "")[:60])
        if t:
            row["at"] = f"{float(t[0]):.1f}-{float(t[1]):.1f}s"
        out.append(row)
        ids[pid] = path
    return out, ids


# --------------------------------------------------------------------- to the palette

SPAN_DEVICES = ("finish", "neg_hold", "neg_split", "neg_world", "variant", "spotlight", "word_blend", "tint", "lamp", "diecut")
WORD_FIELDS = ("marks[].f", "picks[].f", "cuts[]")      # time fields every card may carry; a device declares more (palette "words")


def _word_no(v):
    """A word number as the model wrote it (7, 7.0, "7"), or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def words_to_frames(row, fields, frame_of, end_of):
    """The time fields of one ask (``fields``: the palette's "words" specs) turned from word numbers into programme
    frames, in place: a word is its start frame, a [first, last] pair the stretch from the first word's start to the last
    word's end. A word with no frame leaves a list (or keeps the field as it was). Returns the frames written."""
    got = []

    def one(v, pair=True):
        w = _word_no(v)
        if w is not None:
            f = frame_of(w)
            if f is not None:
                got.append(int(f))
            return None if f is None else int(f)          # None: a word with no frame
        if pair and isinstance(v, (list, tuple)) and len(v) == 2 and _word_no(v[0]) is not None and _word_no(v[1]) is not None:
            a, b = frame_of(_word_no(v[0])), end_of(_word_no(v[1]))
            if a is not None and b is not None:
                got.extend([int(a), int(b)])
                return [int(a), int(max(b, a + 1))]
        return v
    for spec in fields:
        if spec.endswith("[]"):
            key = spec[:-2]
            if isinstance(row.get(key), list):
                out = []
                for el in row[key]:
                    if isinstance(el, (list, tuple)) and el and _word_no(el[0]) is not None:      # a [word, strength] pair
                        f = frame_of(_word_no(el[0]))
                        if f is not None:
                            got.append(int(f))
                            out.append([int(f)] + list(el[1:]))
                    elif _word_no(el) is not None:
                        f = frame_of(_word_no(el))
                        if f is not None:
                            got.append(int(f))
                            out.append(int(f))
                row[key] = out
        elif "[]." in spec:
            key, sub_ = spec.split("[].", 1)
            if isinstance(row.get(key), list):
                conv = []
                for d in row[key]:
                    if isinstance(d, dict) and d.get(sub_) is not None:
                        d = dict(d, **{sub_: one(d[sub_])})
                        if d[sub_] is None and key not in ("marks", "picks"):
                            d.pop(sub_)                       # a mark or a pick keeps None: the ask's own frame stands in
                    conv.append(d)
                row[key] = conv
        elif row.get(spec) is not None:
            v = one(row[spec])
            if v is None:
                row.pop(spec)
            else:
                row[spec] = v
    return got


def palette_asks(d, frame_of, seg_span=None, pic_key=None, dur_f=None):
    """The Director's asks as the palette's, in programme frames. frame_of(w) -> the frame word w starts on;
    seg_span(seg) -> (first frame, last frame) of a sentence; pic_key(id) -> the scene's asset key for a picture id (None
    when it is not in the scene). A section printed on a surface other than colour or mono (mono is the builders' own
    black and white) becomes a finish ask over its sentences; the arc's years become the arc's marks. Returns (asks, notes)."""
    out, notes = [], []
    dur_f = int(dur_f or 10 ** 9)
    pal = _palette()

    def end_of(w):
        nxt = frame_of(w + 1)
        f = frame_of(w)
        return nxt if nxt is not None else (None if f is None else f + 12)
    for k, a in enumerate(d.get("asks") or []):
        f = frame_of(a["at"]["w"])
        if f is None:
            notes.append(f"ask {k}: no frame for word {a['at']['w']}")
            continue
        f = max(int(f), 0)
        args = dict(a.get("args") or {})
        dev = str(a["dev"])
        row = dict(args, dev=dev, id=f"dr{k}", beat=a.get("beat"))
        row.pop("f", None)                                # the ask's own place is its at/until word, set below
        # every time field the device declares (the palette registry's "words"), plus the ones every card may carry,
        # from word numbers to programme frames
        fields = WORD_FIELDS + tuple(x for x in (pal.word_fields(dev) if pal else ()) if x not in WORD_FIELDS)
        inner = words_to_frames(row, fields, frame_of, end_of)
        for key in ("marks", "picks"):
            if isinstance(row.get(key), list):
                row[key] = [dict(m, f=f) if isinstance(m, dict) and "f" in m and m["f"] is None else m for m in row[key]]
                if pic_key:
                    row[key] = [dict(m, pic=pic_key(str(m["pic"])) or m["pic"]) if isinstance(m, dict) and m.get("pic") is not None else m
                                for m in row[key]]
        spanning = dev in SPAN_DEVICES or dev in SURFACES or bool(pal and pal.spans(dev))
        if a.get("until"):
            g = max(end_of(a["until"]["w"]) or frame_of(a["until"]["w"]) or f, f + 2)
        elif spanning:
            g = (seg_span(a["at"]["seg"])[1] if seg_span else None) or f + 30
            g = min(g, f + 90)
        else:
            g = None
        if inner and (g is not None or spanning):         # a stretch reaches every step, section and cut it names
            g = max(g or f, max(inner) + (12 if pal and pal.spans(dev) else 0))
        row["f"] = [f, min(max(int(g), f + 2), dur_f)] if g is not None else f
        if isinstance(row.get("pool"), list) and pic_key:
            row["pool"] = [pic_key(str(p)) or p for p in row["pool"]]
        pics = [p for p in ([pic_key(p) for p in a.get("pics") or []] if pic_key else []) if p]
        if pics:
            row.setdefault("pool", pics)
        # the alien bank's asks hold their span as "at": [a, z] (programme frames)
        if dev in ("diecut", "lamp"):
            row["at"] = row["f"] if isinstance(row["f"], list) else [f, min(f + 36, dur_f)]
            row.setdefault("t", args.get("t") or args.get("text") or a["at"]["word"])
        elif dev == "answer":
            if pics and "picks" not in row:
                row["picks"] = [dict(f=f + 8 * q, pic=p, word=a["at"]["word"]) for q, p in enumerate(pics[:3])]
            last = max([int(m["f"]) for m in row.get("picks") or [] if isinstance(m, dict) and isinstance(m.get("f"), int)] + [f])
            row["at"] = [f, min(max(row["f"][1] if isinstance(row["f"], list) else f, last + 30), dur_f)]
        elif dev == "rhyme":
            cuts = [c for c in row.get("cuts") or [] if isinstance(c, int)]
            end = max(cuts + [row["f"][1] if isinstance(row["f"], list) else f + 60]) + 8
            row["at"] = [f, min(end, dur_f)]
            if not cuts:
                row.setdefault("n", int(args.get("n") or 4))
        out.append(row)
    for s_ in d.get("sections") or []:
        surf = str(s_.get("surface") or "colour").lower()
        if surf in ("colour", "color", "none", "mono"):
            continue
        f0, f1 = section_frames(s_, frame_of, seg_span, dur_f)
        if f0 is None or f1 is None or f1 - f0 < 8:
            continue
        out.append(dict(dev="finish", id=f"ds{s_.get('start', min(s_.get('segs') or [0]))}", surface=surf, f=[int(f0), int(min(f1, dur_f))]))
    marks = []
    for m in d.get("arc") or []:
        f = frame_of(m["w"]) if isinstance(m.get("w"), int) else None
        if f is not None:
            mk = dict(f=int(f), stakes=round(min(max(float(m.get("stakes") or 0), -1.0), 1.0), 2))
            if isinstance(m.get("year"), int) and 1000 <= m["year"] <= 2100:
                mk["year"] = m["year"]
            marks.append(mk)
    if marks:
        out.append(dict(dev="arc", id="darc", marks=sorted(marks, key=lambda m: m["f"]), bars=0.085, develop=6))
    return out, notes


STRENGTH = {"white": 0.9, "neg": 0.8, "black": 0.6, "neg_hold": 0.7, "neg_world": 0.7, "rhyme": 0.7, "answer": 0.7, "lamp": 0.6,
            "diecut": 0.6, "tint": 0.5, "variant": 0.5, "finish": 0.4, "arc": 0.3, "spotlight": 0.3, "word_blend": 0.4,
            "blur_in": 0.25, "neg_split": 0.5}


def device_events(asks):
    """The landings the Director asked for, as events the separate music and sound workflow may answer (no device makes
    a sound): [{"f", "kind", "strength"}], in film order."""
    out = []
    for a in asks or []:
        f = a.get("f")
        f = f[0] if isinstance(f, list) else f
        if isinstance(f, int):
            out.append(dict(f=int(f), kind=str(a.get("dev")), strength=STRENGTH.get(str(a.get("dev")), 0.5)))
    return sorted(out, key=lambda e: e["f"])


def section_frames(sec, frame_of, seg_span=None, dur_f=None):
    """(first frame, end frame) of a section: its words' (the frame after its last word is the next word's start, or the
    end of its sentence), else its sentences'."""
    if sec.get("start") is not None and sec.get("end") is not None:
        f0 = frame_of(int(sec["start"]))
        f1 = frame_of(int(sec["end"]) + 1)
        if f1 is None:
            f1 = seg_span(max(sec.get("segs") or [0]))[1] if seg_span else dur_f
        return f0, f1
    if seg_span and sec.get("segs"):
        return seg_span(min(sec["segs"]))[0], seg_span(max(sec["segs"]))[1]
    return None, None


def around(asks, spans, dur_f=None):
    """Asks moved clear of frames another device owns (a marketing reel's hero device): an ask that starts inside a span
    starts on the frame after it, its length kept. Nothing is dropped."""
    spans = sorted([int(a), int(b)] for a, b in spans or [] if b > a)
    if not spans:
        return asks
    out = []
    for a in asks:
        f = a.get("f")
        a0 = f[0] if isinstance(f, list) else f
        if not isinstance(a0, int):
            out.append(a)
            continue
        for s0, s1 in spans:
            if s0 <= a0 < s1:
                shift = s1 + 1 - a0
                a = dict(a, f=[a0 + shift, f[1] + shift] if isinstance(f, list) else a0 + shift, moved=shift)
                if dur_f and isinstance(a["f"], list):
                    a["f"] = [min(a["f"][0], dur_f - 3), min(a["f"][1], dur_f)]
                elif dur_f:
                    a["f"] = min(a["f"], dur_f - 3)
                break
        out.append(a)
    return out


def energy_band(energy, base=None, take=False):
    """The pace band of a section's energy (scene.pace's hold, seconds). A strobe section cuts its pictures into framings
    down to two frames and its moving footage into new framings too (not one eased move); the speaker's own take is cut
    into punch-in framings no shorter than a third of a second, so his words stay readable. A hold section keeps a
    framing up to about four seconds."""
    lo, hi = BAND_OF.get(energy, BAND_OF["brisk"])
    b = dict(base or {})
    if energy == "strobe":
        b["ease_takes"] = False
        if take:
            lo, hi = 0.33, 1.0
    b["hold"] = (lo, hi)
    b["never"] = max(hi + 0.6, float(b.get("never") or 0))
    return b
