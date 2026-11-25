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
at supply/director.json, keyed on what the model read (the numbered words, the pictures and their contact sheet, the
take): words scored again are directed again, and a signal answer laid because the provider failed is asked of the model
again on resume (kept as the answer only if the model fails again). The sheet also says, measured, what this material
can hold (pictures with a round shape, faces, the speaker on camera), so the model asks for devices that can stand.

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

CONTRACT = 5
ROLES = ("hook", "name", "figure", "list", "turn", "past", "quote", "contrast", "payoff", "close")
ENERGIES = ("strobe", "brisk", "hold")
LAYOUTS = ("bleed", "window", "prints", "collage", "grid", "split", "type")
SURFACES = ("colour", "mono", "gate", "crt", "bloom", "thermal", "threshold", "halftone", "xray", "blueprint", "night_vision",
            "posterise", "solarise", "line", "neg", "rew", "paper", "xerox", "led", "projector", "vhs", "thermal_hud")
# seconds one framing may hold, by a section's energy (scene.pace's band): strobe down to two frames, hold up to about 4 s
# (the strobe floor is the judge's p10 shot, 0.13 s: shots of 1-2 frames are punctuation the devices place, about 8.6% of
# the judge's shots; a 0.067 s floor cut whole strobe sections into 2-frame framings, 21% of one Cuban film's shots)
BAND_OF = {"strobe": (0.13, 0.6), "brisk": (0.5, 1.6), "hold": (1.4, 4.0)}

PRINCIPLES = """How the best vertical edits keep a stranger watching (principles, never a template; every film finds its own):
- Every device says what he says, literally, on the word that says it: real yearbook pages landing as prints for "they
  circled it as being me", a grid of four phones for "four different answers", the real guide dog at the kerb for "a
  guide dog". A device with no reason in the words is decoration: leave that moment plain.
- The first second shows the speaker or the true subject of his first words, and the hook sentence is over by 3-4 s.
  Nothing on screen then says what he does not say: no number, counter or title he does not say, one piece of display
  type at a time, and no picture that carries another editor's own words or logo fighting the captions.
- The name lands second (3-10 s), in two sizes.
- A fixed figure lands whole on its word. Count between two sourced endpoints when the passage describes that change.
  Keep the unit, relation and any qualification together with the figure.
- Every turn changes something you can see: the surface, the polarity, the pace, or a type card.
- Lists burst: one picture or one hit per item, fast, for as long as the list is said (about a second), then it lands.
- The pace alternates fast against hold: some 3 s windows carry one cut, others six to ten. Use a brief dip or held image to separate two thoughts when the pause,
  change of subject or new picture gives it a purpose. Carry an unbroken spoken turn on the moving picture.
- Pictures move inside their shots: a push, a drift, a settle on the word; a still never just sits.
- Premium, never cringe: one look carries the film (its colour; black and white for the past against colour for the
  present; a film gate, a tape or a page for an era the story names), and a surface changes where the story changes.
  A treatment that replaces the picture's own tones (thermal, threshold, x-ray, blueprint, line, night vision,
  solarise, posterise, negative) is a beat of a second or less on the word it literally means (thermal on heat or
  surveillance, a blueprint on a plan, an x-ray on what is hidden, a negative on a reversal): never a section, never
  over the speaker's face or a face the story hangs on, never where words must be read.
- A stranger never stands for the speaker: when he tells of himself, show him, his own archive, or the thing he names,
  never another person who could read as him.
- The payoff slows down, holds, and calls back to the opening.
- Stack devices on a moment that deserves it (a flip, then a duotone echo, then a word cut through the picture); choose
  each device for what this moment and this story need, never a set you bring to every film.
- At least one moment combines devices in a way you have not seen any editor do, and it says what is said there.
- Restraint is also a device: a hold after a burst lands harder than a burst after a burst. Never decorate every word;
  a stretch with no device at all is often what makes the next one land.
- The sheet says what this material can hold (how many pictures have a round shape, whether the speaker is on camera):
  a device the material cannot hold falls back to a plainer one, so ask for what can stand here."""

SYSTEM = ("You are the Director of a vertical short film. You choose, moment by moment, which devices of the engine's "
          "palette land on which spoken word, so the film is the most creative edit of this material anyone has made: "
          "not a video with captions, but a film whose every beat does something to the picture. You see the spoken "
          "words numbered with their times (^ marks the voice's stress peaks), the pictures the film has (one contact "
          "sheet, each picture with its id and what signal measured), the take, the style's starting palette, and what "
          "the films delivered lately did (do something else). You answer in JSON only.\n\n" + PRINCIPLES + "\n\n"
          "Sound is optional. Read the story, contact sheet and picture change together. Choose at most two "
          "moments in the whole short where a sudden reveal, turn, hook or figure would lose force in silence. "
          "On those beats only include sound (air, rise, sub, impact or tick) and sound_reason explaining what "
          "changes on screen. Tick only for text landing. Leave ordinary cuts, stressed words and holds silent. "
          "Return JSON only: {\"beats\": [{\"role\": one of hook|name|figure|list|turn|past|quote|contrast|payoff|close, "
          "\"w\": the word number it lands on, \"word\": that word as said, \"energy\": strobe|brisk|hold}], "
          "\"sections\": [{\"from\": first word number, \"to\": last word number (runs of words in order, covering the film "
          "from word 0 to the last word, no gaps; sections are the story's parts (the hook, the past, the turn, the "
          "payoff): the film keeps one look, and a surface changes only where the story does), \"surface\": "
          "colour|mono|gate|crt|bloom|thermal|threshold|halftone|xray|blueprint|night_vision|posterise|solarise|line|neg|"
          "rew|vhs|projector|led|xerox|paper|thermal_hud, "
          "\"layout\": bleed|window|prints|collage|grid|split|type, \"energy\": strobe|brisk|hold}], "
          "\"asks\": [{\"dev\": a device name from the palette, \"beat\": index into beats or null, \"at\": {\"w\": word "
          "number, \"word\": the word}, \"until\": {\"w\": word number, \"word\": the word} (spans only), \"args\": {the "
          "device's own fields from its card, e.g. \"mode\", \"surface\", \"run\", \"n\", \"c\", \"part\"; every time field of a card "
          "(f, at, until, lands, cuts, steps, stress, sections[].at, lines[].at, marks[].f, picks[].f, a callback's from) is a WORD "
          "NUMBER, a [first, last] pair of word numbers is a stretch, and any picture field (pic, pool) a picture id: the engine "
          "turns them into frames and assets; a text field (t, text, words, lines, name, title, label) holds the words as he "
          "says them, never a word number; a counter's from and to are the figures themselves; numbers in the units and "
          "ranges the card gives}, \"pics\": [picture ids it uses]}; as many asks as this material earns, "
          "stacked where a moment deserves it, none where restraint serves it], "
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

def palette_module():
    """worker/scene/palette, or None where the engine cannot be imported."""
    try:
        import brand
        wk = brand.worker_dir()
        for p in (os.path.dirname(wk), wk):
            if p not in sys.path:
                sys.path.append(p)
        from scene import palette
        palette._discover()
        return palette
    except Exception:      # noqa: BLE001
        return None




_palette = palette_module      # the name palette_asks reads the registry's word fields through


def palette_menu():
    """The palette's cards from the engine itself (worker/scene/palette), so the Director never sees a device the
    engine cannot draw. Each card carries its fields' units and drawable ranges."""
    pal = palette_module()
    if pal is None:
        try:            # a worker that has not shipped the palette yet: the alien bank it has (scene.alien) is the menu
            from scene import alien
            return alien.menu(), sorted(alien.DEVICES)
        except Exception:      # noqa: BLE001
            return "(palette menu unavailable: the engine could not be imported)", []
    return pal.menu(), sorted(pal.DEVICES)


def round_shapes(path):
    """The round shapes a picture holds that the engine's rhyme would cut on, by its own circle finder and support floor
    (worker/scene/alien/rhyme): [(cx, cy, r) as fractions of the width (cx, r) and height (cy)], with the picture's
    size: ([...], pw, ph). None where it cannot be read or the engine cannot be imported."""
    try:
        import cv2
        im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if im is None:
            return None
        palette_module()                 # the worker on the path; the rhyme's own finder is in scene.alien on every worker
        from scene.alien import rhyme
        ph, pw = im.shape[:2]
        k = rhyme.SIDE / min(pw, ph)
        w, h = max(int(pw * k) // 2 * 2, 2), max(int(ph * k) // 2 * 2, 2)
        cs, _ = rhyme.circles(cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA))
        return [(c[0] / w, c[1] / h, c[2] / w) for c in cs if c[3] >= rhyme.MIN_SUPPORT][:2], pw, ph
    except Exception:      # noqa: BLE001 - not measured: the sheet says so
        return None


def round_count(path):
    got = round_shapes(path)
    return None if got is None else len(got[0])


def shared_rounds(pictures, W=1080, H=1920):
    """How many of the pictures can stand one round shape on one seat of the frame (the rhyme's own test: the largest
    shape that that many pictures hold on one seat). 0 when none or not measured."""
    try:
        from scene.alien import rhyme
    except Exception:      # noqa: BLE001
        return 0
    shapes = [p["_round"] for p in pictures if p.get("_round") and p["_round"][0]]
    if not shapes:
        return 0
    seats = [(x / 100.0, y / 100.0) for x in range(32, 69, 8) for y in range(30, 63, 8)]
    best = 0
    for R in (0.40, 0.34, 0.28, 0.22, 0.16):
        for seat in seats:
            n = sum(1 for cs, pw, ph in shapes if any(rhyme.fits(c, seat, R, pw, ph, W, H) for c in cs))
            best = max(best, n)
    return best


RHYME_PICTURES = 4          # the rhyme chain cuts on one round shape through at least this many pictures


def capability(pictures, take, pal=None):
    """What this material can hold, measured, as lines for the moment sheet: information for the model, never a gate
    (a device asked for anyway runs and falls back to its next device where it cannot stand)."""
    pics = list(pictures or [])
    rounds = [p for p in pics if isinstance(p.get("rounds"), int) and p["rounds"] > 0]
    shared = shared_rounds(pics)
    faces = [p for p in pics if isinstance(p.get("faces"), int) and p["faces"] > 0]
    clips = [p for p in pics if p.get("kind") == "clip"]
    tk = take or {}
    on_cam = bool(tk) and tk.get("on_camera") is not False
    matte = depth = None
    if pal is not None:
        try:
            from scene.palette import sense as SN
            matte = os.path.exists(SN.RVM)
        except Exception:      # noqa: BLE001
            matte = None
    depth = bool(os.environ.get("VALMERA_DEPTH_PY"))
    have = {"pictures": len(pics) > 0, "rounds": shared >= RHYME_PICTURES, "faces": bool(faces) or on_cam,
            "take": on_cam, "matte": on_cam and matte is not False, "depth": depth}
    lines = [f"- pictures: {len(pics)} on the contact sheet ({len(clips)} moving clips, {len(pics) - len(clips)} stills)",
             f"- round shapes: {len(rounds)} of the pictures show one the engine can register; at most {shared} of them hold "
             f"one on a shared seat of the frame (a rhyme chain cuts through {RHYME_PICTURES} or more on one seat)",
             f"- faces: {len(faces)} pictures show a face" + ("; the speaker's face is in the take" if on_cam else ""),
             "- the speaker: " + ("on camera; a device that stands on him (lamp, neg_world, a spotlight on his face) keeps "
                                  "him on screen over its span" if on_cam else "not on camera: devices that stand on him fall back"),
             "- depth of a still: " + ("measured where asked" if depth else "not on this machine (devices that need it fall back)")]
    short = []
    if pal is not None:
        for name in sorted(pal.DEVICES):
            miss = [n for n in pal.DEVICES[name].get("needs") or () if n in have and not have[n]]
            if miss:
                fb = next((d for d in pal.DEVICES[name].get("fallback") or () if d in pal.DEVICES), "white")
                short.append(f"{name} (needs {', '.join(miss)}; would land as {fb})")
    if short:
        lines.append("- cannot stand on this material: " + "; ".join(short))
    return lines


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


def moment_sheet(sentences, stress_w, pictures, take, style, workflow, dur_s, recent, title=None, hook=None, payoff=None,
                 can=None):
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
    if can:
        lines.append("\n# What this material can hold (measured; a device it cannot hold lands as its fallback)")
        lines.extend(can)
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


# The words a device sets on the frame are words he says (20 Nov rating: ladders printed '3 4 5 11' and type pages '0 1 2 3'
# where the answer gave word numbers in a text field, and type_run, ladder and redact asked with no words landed nothing).
TEXT_FIELDS = ("t", "text", "word", "name", "title", "kicker")
TEXT_LISTS = ("words", "lines", "struck")
SETS_SAID = ("ladder", "type_run", "verb", "brackets", "highlight", "flip", "typewriter", "redact", "marquee", "behind",
             "lamp", "diecut", "staircase", "name", "chapter")
SAID_SPAN = {"ladder": 4, "type_run": 6, "typewriter": 8, "redact": 8, "staircase": 4, "marquee": 3, "chapter": 4, "name": 3}


def said_text(dev, args, at, until, words):
    """An ask's text fields as the words said. A word number written where text belongs (7, "7", [7, 9], a ladder row
    {"t": 7}) becomes the word or words said there, unless that number is itself said in the ask's own sentences (then it
    is the figure); a device that sets the words he says and was given none sets the words said over its span (its word,
    or from its word to its until). Returns (args, repairs)."""
    args = dict(args or {})
    if not words or not isinstance(at, dict):
        return args, []
    rep = []
    seg = int(at.get("seg") or 0)
    near = {_norm(x["w"]) for x in words if abs(int(x["seg"]) - seg) <= 1}

    def run(a, b):
        a, b = max(min(a, b), 0), min(max(a, b), len(words) - 1)
        return " ".join(str(words[n]["w"]) for n in range(a, b + 1)).strip(" ,.;:!?")

    def number(v):
        """The word number v names, or None when v is text (or a figure said here)."""
        n = _word_no(v)
        if n is None or not 0 <= n < len(words):
            return None
        return None if _norm(v) in near else n

    def one(v):
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(number(x) is not None for x in v):
            return run(number(v[0]), number(v[1]))
        n = number(v)
        return v if n is None else run(n, n)
    for key in TEXT_FIELDS:
        if args.get(key) is not None and not isinstance(args[key], dict):
            new = one(args[key])
            if new != args[key]:
                rep.append(f"{key} {args[key]!r} was a word number: the words said there, {new!r}")
                args[key] = new
    for key in TEXT_LISTS:
        if not isinstance(args.get(key), list):
            continue
        out = []
        for el in args[key]:
            if isinstance(el, dict):
                el = dict(el)
                for f in ("t", "text"):
                    if el.get(f) is not None:
                        el[f] = one(el[f])
            elif isinstance(el, (list, tuple)) and len(el) == 2 and isinstance(el[1], str) and not str(el[1]).strip().isdigit():
                el = {"t": one(el[0]), "w": el[1]}         # a [word, whisper|shout] pair
            else:
                el = one(el)
            out.append(el)
        if out != args[key]:
            rep.append(f"{key} held word numbers: the words said there, {out!r}"[:240])
            args[key] = out
    has = any(str(args.get(k) or "").strip() for k in TEXT_FIELDS) or any(args.get(k) for k in TEXT_LISTS)
    if dev in SETS_SAID and not has:
        a = int(at["w"])
        if isinstance(until, dict) and isinstance(until.get("w"), int) and until["w"] > a:
            b = min(until["w"], a + SAID_SPAN.get(dev, 0))
        elif dev in SAID_SPAN:
            b = max([x["n"] for x in words if x["seg"] == words[a]["seg"] and x["n"] <= a + SAID_SPAN[dev] - 1] + [a])
        else:
            b = a
        args["t"] = run(a, b)
        rep.append(f"set no words: the words said over its span, {args['t']!r}")
    return args, rep


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
        cue = b.get("sound")
        accent = {"sound": cue, "sound_reason": b["sound_reason"]} if (
            cue in ("air", "rise", "sub", "impact", "tick") and isinstance(b.get("sound_reason"), str)
            and b["sound_reason"].strip()) else {}
        if accent and isinstance(b.get("sound_partner"), dict):
            accent["sound_partner"] = {k:v for k,v in b["sound_partner"].items()
                                       if k in ("before", "after", "device", "text") and isinstance(v,str) and v}
        beats.append(dict(role=role, **accent, energy=b.get("energy") if b.get("energy") in ENERGIES else "brisk", **a))
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
        row["args"], why = said_text(row["dev"], row["args"], row["at"], row.get("until"), words)
        rep += [f"ask {k} {row['dev']}: {w_}" for w_ in why]
        if row["dev"] == "counter" and any(row["args"].get(k) is None for k in ("from", "to")):
            row["args"].setdefault("t", at["word"])  # preserve the landing through the palette's word fallback
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
    ends = d.get("caption_ends")
    caption_ends = sorted({i for i in ends if type(i) is int and 0 <= i < len(words)}) if isinstance(ends, list) else []
    return dict(contract=CONTRACT, beats=beats, sections=sections, asks=asks, display=display[:6],
                caption_ends=caption_ends,
                **({"publication_copy": d["publication_copy"]} if isinstance(d.get("publication_copy"),dict) else {}),
                **({"thought_reading": d["thought_reading"]} if isinstance(d.get("thought_reading"),dict) else {}),
                **({"model_phases": d["model_phases"]} if isinstance(d.get("model_phases"),list) else {}),
                **({"reporting_example": d["reporting_example"]} if isinstance(d.get("reporting_example"),dict) else {}),
                **({"approval_example": d["approval_example"]} if isinstance(d.get("approval_example"),dict) else {}),
                **({"principle_example": d["principle_example"]} if isinstance(d.get("principle_example"),dict) else {}),
                archive_views=d.get("archive_views") if isinstance(d.get("archive_views"), list) else [],
                archive_credits=d.get("archive_credits") if isinstance(d.get("archive_credits"), list) else [],
                past_segs=[int(i) for i in d.get("past_segs") or [] if isinstance(i, int) and 0 <= i < nseg],
                payoff_seg=payoff, turn_seg=payoff, story_turn_seg=turn,
                arc=arc, why=str(d.get("why") or "")[:400]), rep


# --------------------------------------------------------------------- the signal director

POOL = ("white", "neg", "tint", "blur_in", "spotlight", "neg_split", "variant", "word_blend", "black", "snap", "settle", "whip", "glimpse")
SPREADS = ("grid", "collage")      # a sentence dense with stress fills a layout one tile (or print) per stressed word
VARIANT_MODES = ("thermal", "threshold", "halftone", "xray", "blueprint", "posterise", "solarise", "line", "mosaic", "night_vision")
# a section's surface keeps the picture's own tones (faces and words read through it); the treatments that replace them
# (thermal, threshold, x-ray) are beats of the variant device, never a section
FINISHES = ("mono", "gate", "crt", "bloom", "vhs", "projector")


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
            if words[b["w"]]["t0"] - words[prev]["t1"] >= .3:
                asks.append(dict(dev="black", beat=bi, at=dict(w=prev, seg=words[prev]["seg"], word=words[prev]["w"]), args=dict(n=2), pics=[]))
            asks.append(dict(dev="settle", beat=bi, at=a, args=dict(dir="in"), pics=[]))
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
           title=None, hook=None, payoff=None, seed=0, recent=None, ask=None, effort="high", cache_name="director.json", log=None, editorial=None):
    """The film's per-moment device score (DIRECTOR_CONTRACT 4), cached at supply/<cache_name>.

    sentences  [{"i", "text"}]; word_rows [{"w", "t0", "t1", "seg"}] in order (the film's own clock, seconds);
    stress_t   [(seconds, strength)]; pictures [{"id", "path", "label", "kind", "motion", "faces", "luma", ...}];
    ask        llm.ask (the run's provider); recent: the ledger's recent sequences (devices_ledger.recent)."""
    path = os.path.join(supply, cache_name)
    say = log or (lambda m: None)
    sents, words = numbered(sentences, word_rows)
    hot = stress_words(words, stress_t)
    pictures = [dict(p) for p in pictures or ()]
    for p in pictures:                       # what signal reads of each picture's shapes, for the sheet and the cards' needs
        if "rounds" not in p and p.get("path"):
            got = round_shapes(p["path"])
            p["rounds"] = None if got is None else len(got[0])
            if got:
                p["_round"] = got
    dur = float(dur_s or (words[-1]["t1"] if words else 0.0))
    pal = palette_module()
    can = capability(pictures, take, pal)
    cs = contact_sheet(list(pictures), os.path.join(supply, "director_sheet.jpg")) if pictures else None
    # the cache is keyed on what the model reads (the numbered words, the pictures and their contact sheet, the take):
    # a short whose lines were scored again never reads an answer that addresses words it no longer has
    brief = style if "placed_evidence" in (style or {}) else None
    system = SYSTEM
    if brief is not None:
        system += (' For each sound give "sound_partner" as an object containing only the applicable fields: '
                   '"after" (the picture ID), "before" (optional), "device" (the exact device on that beat), '
                   'or "text" (exact printed words). A tick accompanies type landing.')
    contract = [CONTRACT, system, brief] if brief is not None else CONTRACT
    key = hashlib.sha1(json.dumps([contract, words_sheet(sents, hot),
                                   [{k: p.get(k) for k in ("id", "path", "label", "kind", "rounds", "faces", "seg")} for p in pictures],
                                   take, editorial, hashlib.sha1(open(cs, "rb").read()).hexdigest() if cs else None],
                                  sort_keys=True, default=str).encode()).hexdigest()[:16]
    prior = None
    if os.path.exists(path):
        try:
            got = json.load(open(path))
        except (OSError, ValueError):
            got = {}
        if got.get("contract") == CONTRACT and got.get("key") == key:
            if not (got.get("by") == "signal" and got.get("retry")):
                return got
            prior = got                       # the provider failed last time: the model is asked again on resume
            say("director: the cached answer is the signal director's after a provider failure; asking the model again")
    if recent is None:
        try:
            import devices_ledger
            recent = devices_ledger.recent(12)
        except Exception:      # noqa: BLE001 - no ledger yet
            recent = []
    menu, names = palette_menu()
    sheet = moment_sheet(sents, hot, pictures, take, style, workflow, dur, recent, title, hook, payoff, can)
    if editorial:
        sheet += "\n\n" + editorial["writing"] + "\n" + json.dumps(editorial["material"], ensure_ascii=False)
    content = [{"type": "text", "text": sheet + "\n\n# The palette (one card a device: its fields with their units, then what it does and the moment it amplifies)\n" + menu}]
    if cs:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(open(cs, "rb").read()).decode()}})
    seed = int(seed) or int(prior.get("seed") if prior else 0) or int(hashlib.sha1(sheet.encode()).hexdigest()[:8], 16)
    spend, repairs, d, by = [], [], None, "model"
    failed = False
    if ask is None and os.environ.get("PYTEST_CURRENT_TEST"):
        repairs.append("a test never reaches a real model: the signal director")
    elif ask is None:
        try:
            ask = shell_llm().ask
        except Exception as e:      # noqa: BLE001
            repairs.append(f"no model client: {e!r}")
            failed = True
    if ask is not None:
        msg = content
        for attempt in range(2):
            try:
                text, use = ask(system, msg, effort=effort, max_tokens=24000)
                spend.append(dict(stage="score", what="director: devices by moment", **(use or {})))
            except Exception as e:      # noqa: BLE001 - a provider failure: the signal director lays the devices
                repairs.append(f"provider failed: {type(e).__name__}: {str(e)[:200]}")
                failed = True
                break
            try:
                d = shell_llm().json_block(text)
                missing = [i for i, row in enumerate(d.get("asks", [])) if isinstance(row, dict)
                           and row.get("dev") == "counter"
                           and any((row.get("args") or {}).get(k) is None for k in ("from", "to"))]
                if missing and attempt == 0 and not editorial:
                    msg = content + [{"type": "text", "text": "Complete your score again. Counter asks "
                                      + str(missing) + " need numeric from and to from the actual passage. "
                                      "If there is no spoken number, choose a meaningful word device on that same beat."}]
                    continue
                break
            except Exception as e:      # noqa: BLE001 - asked again with the parse error shown
                repairs.append(f"answer {attempt + 1} had no JSON: {str(e)[:120]}")
                msg = content + [{"type": "text", "text": f"Your answer could not be read as JSON ({str(e)[:200]}). Answer again "
                                                          "with the JSON object only."}]
                d = None
    if not isinstance(d, dict):
        if prior is not None:                # the model failed again: the signal answer it had is kept, and asked again next time
            out = dict(prior, repairs=list(prior.get("repairs") or []) + repairs, retry=True)
            for r in repairs:
                say(f"director repair: {r}")
            json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
            return out
        d = signal_director(sents, words, hot, pictures, seed, recent, dur)
        by = "signal"
    out, rep = repair(d, sents, words, names, seed)
    if by == "model" and not out["asks"]:
        sig = signal_director(sents, words, hot, pictures, seed, recent, dur)
        out["asks"], out["beats"] = sig["asks"], out["beats"] or sig["beats"]
        rep.append("the answer laid no device: the signal director's asks were laid")
    out.update(by=by, repairs=repairs + rep, seed=seed, recent=recent, stress_words=hot, key=key, can=can,
               retry=bool(by == "signal" and failed),
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

SPAN_DEVICES = ("finish", "neg_hold", "neg_split", "neg_world", "variant", "spotlight", "word_blend", "tint", "lamp", "diecut",
                "keep_hue", "colour_bloom", "tear", "keep_colour", "parallax", "proof_object")
# time fields every card may carry; a device declares more (palette "words"). "lands" (a counter's figure word) and
# "lines[].at" (a staircase line's word) are words on every worker: a counter read word 36 as frame 36 and counted in the
# first second of five Cuban films (20 Nov rating)
WORD_FIELDS = ("marks[].f", "picks[].f", "cuts[]", "lands", "lines[].at")


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


def sound_cues(d, word_frame, picture_keys=None, *, use_asked_device=False):
    cues = []
    for i, beat in enumerate(d.get("beats") or []):
        if not beat.get("sound") or word_frame(beat["w"]) is None:
            continue
        cue = dict(f=int(word_frame(beat["w"])), role=beat["role"], sound=beat["sound"], why=beat["sound_reason"])
        if picture_keys is not None:
            partner = dict(beat.get("sound_partner") or {})
            if use_asked_device and not partner:
                # Older scores name the visible change in their own device ask.
                # Bind that exact beat and word to the composed device.
                own = [a for a in d.get("asks") or [] if a.get("beat") == i and
                       any((a.get(k) or {}).get("w") == beat["w"] for k in ("at", "until"))]
                if own:
                    partner["device"] = own[0]["dev"]
            for side in ("before", "after"):
                if side in partner:
                    partner[side] = picture_keys.get(partner[side], partner[side])
            cue.update(beat=i, partner=partner)
        cues.append(cue)
    return cues


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
        row.pop("at", None)  # the word address above has already been resolved
        row.pop("until", None)
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
            row.setdefault("t", args.get("t") or args.get("text") or a["at"]["word"])     # its fallback (diecut) sets the word
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


ON_HIM = ("spotlight",)       # devices that stand on the speaker by their kind, beside those whose card needs the take


def take_spans(asks, dur_f=None, most=90):
    """[[a, b]] programme frames over which the speaker must stay on screen: the spans of the asks that stand on him (a
    device whose card needs the take or his matte, such as lamp and neg_world; a spotlight on his face; any ask marked
    ``behind``), merged. The builder keeps the take on screen there instead of a cutaway, so the device lands on him."""
    pal = palette_module()
    out = []
    for a in asks or []:
        dev = str(a.get("dev") or "")
        name = pal.resolve(dev) if pal is not None else dev
        needs = set((pal.DEVICES.get(name) or {}).get("needs") or ()) if pal is not None else (
            {"take"} if dev in ("lamp", "neg_world") else set())
        if not ({"take", "matte"} & needs or name in ON_HIM or a.get("behind")):
            continue
        f = a.get("at") if name in ("lamp", "diecut", "answer") and isinstance(a.get("at"), list) else a.get("f")
        if isinstance(f, list) and len(f) >= 2:
            lo, hi = int(f[0]), int(f[1])
        elif isinstance(f, (int, float)):
            lo, hi = int(f), int(f) + 30
        else:
            continue
        if name == "lamp":                   # the light dies over the frames after its word
            hi += int(a.get("tail") if isinstance(a.get("tail"), (int, float)) else 10)
        hi = min(hi, lo + most)
        if dur_f:
            hi = min(hi, int(dur_f))
        if hi > lo:
            out.append([max(lo - 1, 0), hi])
    out.sort()
    merged = []
    for lo, hi in out:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def hold_take(ins, spans, min_f=8, fps=30.0):
    """Cutaway rows (f0, f1, kind, name, rec) with the frames of ``spans`` given back to the take: a row inside a span
    leaves, a row across one is cut round it (a clip's source time cut with it, so it plays at its own speed), and a
    piece shorter than ``min_f`` leaves. Returns (rows, frames cleared)."""
    out, cleared = [], 0
    for row in ins:
        f0, f1, kind, name, rec = row[0], row[1], row[2], row[3], row[4]
        pieces = [(f0, f1)]
        for a, b in spans:
            nxt = []
            for lo, hi in pieces:
                if hi <= a or lo >= b:
                    nxt.append((lo, hi))
                    continue
                if lo < a:
                    nxt.append((lo, a))
                if hi > b:
                    nxt.append((b, hi))
            pieces = nxt
        pieces = [(lo, hi) for lo, hi in pieces if hi - lo >= min_f]
        kept = sum(hi - lo for lo, hi in pieces)
        cleared += (f1 - f0) - kept
        if pieces == [(f0, f1)]:
            out.append(row)
            continue
        for lo, hi in pieces:
            r = dict(rec)
            src = rec.get("src")
            if isinstance(src, (list, tuple)) and len(src) == 2 and f1 > f0:
                rate = (float(src[1]) - float(src[0])) / (f1 - f0)
                r["src"] = [round(float(src[0]) + rate * (lo - f0), 4), round(float(src[0]) + rate * (hi - f0), 4)]
            if isinstance(r.get("audit_until"), (int, float)):
                r["audit_until"] = min(int(r["audit_until"]), hi)
            out.append(type(row)([lo, hi, kind, name, r] + list(row[5:])) if isinstance(row, (list, tuple)) else row)
    return out, cleared


STRENGTH = {"rgb_split": 0.7, "slice": 0.8, "smear": 0.8, "glitch": 1.0, "tear": 0.6, "filmburn": 0.9, "bg_swap": 0.8,
            "silhouette": 0.9, "poster": 0.8, "sticker": 0.7, "clones": 0.7, "parallax": 0.5, "proof_object": 0.6,
            "white": 0.9, "neg": 0.8, "black": 0.6, "neg_hold": 0.7, "neg_world": 0.7, "rhyme": 0.7, "answer": 0.7, "lamp": 0.6,
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
    """Place each ask in a free interval around the reel's own devices, including its rendered hold."""
    if not spans:
        return asks
    free = []
    at = 0
    limit = dur_f if dur_f is not None else 10 ** 9
    for lo, hi in sorted((int(a), int(b)) for a, b in spans if b > a):
        if lo > at:
            free.append((at, min(lo, limit)))
        at = max(at, hi + 1)
    if at < limit:
        free.append((at, limit))
    free = [(lo, hi) for lo, hi in free if hi - lo >= 2]
    if not free:
        return asks
    out = []
    for ask in asks:
        f = ask.get("f")
        origin = f[0] if isinstance(f, list) else f
        if not isinstance(origin, int):
            out.append(ask)
            continue
        # Keep its word when possible. An ask inside the final hero uses the
        # preceding free interval, instead of landing inside the end card.
        length = max(2, f[1] - origin) if isinstance(f, list) else 2
        slot = next(((max(origin, lo), hi) for lo, hi in free if hi - max(origin, lo) >= 2), None)
        if slot is None:
            lo, hi = free[-1]
            slot = max(lo, hi - length), hi
        start, bound = slot
        end = min(start + length, bound)
        row = dict(ask, f=[start, end] if isinstance(f, list) else start, bound=bound)
        if isinstance(ask.get("bound"), (int, float)):
            row["bound"] = min(bound, ask["bound"] + start - origin)
        if start != origin or (isinstance(f, list) and end != f[1]):
            if start != origin:
                row["moved"] = start - origin
            def move(frame):
                return min(end - 1, max(start, start + frame - origin))
            if isinstance(ask.get("at"), list):
                row["at"] = [start, min(bound, max(start + 2, ask["at"][1] + start - origin))]
            pal = _palette()
            fields = WORD_FIELDS + tuple(k for k in (pal.word_fields(ask.get("dev")) if pal else ()) if k not in WORD_FIELDS)
            words_to_frames(row, fields, move, move)
        out.append(row)
    return out


def energy_band(energy, base=None, take=False):
    """The pace band of a section's energy (scene.pace's hold, seconds). A strobe section cuts its pictures into framings
    down to four frames (the judge's p10 shot) and its moving footage into new framings too (not one eased move); the speaker's own take is cut
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
