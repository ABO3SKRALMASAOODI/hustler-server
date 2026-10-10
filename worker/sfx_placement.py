"""Where a sound effect may sit: the placement checks every sound tool shares.

Owner, Oct 10 2026: zooms and sound effects are optional, never rules, and
podcast shorts default to ZERO sounds. Independent judges then named what
the remaining sounds did wrong on the showcase shorts:

* a notification ding landed exactly on the payoff word '140' and, bright
  and loud in the 2-4 kHz band, masked the word it was meant to sell;
* a camera shutter on the spoken word 'pictures' was a literal sound pun on
  a line about computer graphics — childish, with no photo on screen;
* the opening whoosh hit 0.25 s into a hook that was already static from
  frame 0: no visual event for it to belong to (the same opening formula on
  all three shorts);
* a swish sat 12 dB under the voice and was inaudible (sfx_mix levels).

So a sound needs (1) a VISUAL PARTNER: a graphic landing, entrance or exit,
a cut or B-roll entry, a zoom or card edge within ~50 ms of its hit (a
whoosh, swish or riser accompanies a movement, so within 0.12 s); (2) to
stay OFF a payoff or emphasis word's onset energy when it is bright (ding,
pop, tick, click, shutter, cash, glitch over the word's first 300 ms) —
payoff/emphasis words are the spoken words a designed graphic shows and the
captions' emphasis words; (3) no literal pun: foley that names the spoken
word under it (shutter/'pictures', cash register/'money') while nothing on
screen shows that action. A talking short carries at most 1-2 sounds and no
reflexive opening whoosh.

Pure functions over (edl, index words): add_sfx/move_sfx report them at
write time (add_sfx also nudges a bright sound off a protected onset when a
speech gap with its own visual partner is within reach), template cues are
reported when written, audit_audio_mix lists them, and taste.critique turns
them into advisory notes. Every finding is advisory evidence, never a write
gate: the editor (or the user) may keep a sound deliberately.
"""

import re

import sound_library

PARTNER_S = 0.05            # a transient's visual partner
SWEEP_PARTNER_S = 0.12      # a whoosh/swish/riser accompanies a movement
GRID_S = 0.006              # the EDL keeps sfx on a 10 ms grid
ONSET_GUARD_S = 0.03        # just before a protected onset still collides
ONSET_PROTECT_S = 0.30      # the word's first 300 ms carry its consonants
PUN_BEFORE_S = 0.30         # a sound this close before a word is "on" it
PUN_AFTER_S = 0.15
GAP_MIN_S = 0.12            # a speech gap a nudged sound may sit in
NUDGE_REACH_S = 1.5         # how far from the word a nudge may look
OPENING_S = 1.0             # an opening whoosh: the reflexive hook formula
TALKING_SHORT_MAX = 2       # sounds in a podcast/talking short
TALKING_SHORT_MAX_S = 120.0
TALKING_SHORT_MIN_WORDS = 20

BRIGHT_ROLES = {"ding", "pop", "tick", "click", "shutter", "cash", "glitch"}
SWEEP_ROLES = {"whoosh", "swish", "riser"}

# Spoken words a literal foley sound would pun on. A sound in one of these
# roles landing on one of its words is a pun unless something on screen
# shows the action (a B-roll insert or overlay, or a graphic whose own
# designed sound is that role: a typewriter types, a notification card
# dings, a counter ticks).
PUN_WORDS = {
    "shutter": {"picture", "pictures", "photo", "photos", "photograph",
                "photographs", "photography", "camera", "cameras", "snapshot",
                "selfie", "selfies", "pic", "pics", "image", "images"},
    "cash": {"money", "cash", "dollar", "dollars", "paid", "pay", "pays",
             "paying", "payment", "price", "prices", "rich", "richer",
             "wealth", "wealthy", "profit", "profits", "revenue", "sale",
             "sales", "buck", "bucks", "earn", "earned", "earning",
             "million", "millions", "billion", "billions", "salary"},
    "ding": {"notification", "notifications", "ding", "bell", "bells",
             "message", "messages", "text", "texts", "texted", "alert",
             "alerts", "ping", "email", "emails", "inbox", "dm", "dms",
             "phone", "phones"},
    "typing": {"type", "types", "typing", "typed", "keyboard", "keyboards",
               "typewriter", "write", "writes", "writing", "wrote",
               "written", "code", "coding"},
    "heartbeat": {"heart", "hearts", "heartbeat", "pulse", "nervous",
                  "scared", "afraid", "fear", "anxious", "anxiety"},
    "tick": {"clock", "clocks", "tick", "ticking", "time", "timer",
             "seconds", "second", "minute", "minutes", "countdown",
             "deadline"},
    "click": {"click", "clicks", "clicked", "clicking", "button", "buttons",
              "tap", "tapped", "mouse"},
    "glitch": {"glitch", "glitches", "bug", "bugs", "buggy", "broken",
               "error", "errors", "hack", "hacked", "hacker", "virus",
               "crash", "crashed"},
    "pop": {"pop", "pops", "popped", "popping", "bubble", "bubbles",
            "balloon"},
}

# Role vocabulary for sounds that are not library recordings (uploads,
# fetched sounds, legacy keys): matched against the storage key and purpose.
_ROLE_VOCAB = (
    ("riser", ("riser", "rise", "swell", "build")),
    ("whoosh", ("whoosh", "woosh", "swoosh", "transition")),
    ("swish", ("swish", "swipe", "whip")),
    ("impact", ("impact", "boom", "thud", "kick", "slam", "hit", "sub")),
    ("ding", ("ding", "chime", "bell", "notification", "ping")),
    ("cash", ("cash", "coin", "register", "money")),
    ("shutter", ("shutter", "camera", "photo")),
    ("typing", ("typing", "keyboard", "typewriter")),
    ("click", ("click", "tap", "button")),
    ("tick", ("tick", "clock")),
    ("pop", ("pop", "bubble", "blip")),
    ("glitch", ("glitch", "static", "digital")),
    ("heartbeat", ("heartbeat", "heart")),
)

_STOP = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "at", "is",
         "it", "its", "it's", "that", "this", "we", "you", "i", "be", "for",
         "with", "as", "was", "are", "so", "but", "not", "just", "been",
         "have", "has", "had", "do", "did", "our", "your", "their", "they",
         "he", "she", "his", "her", "me", "my", "from", "by", "all", "got"}
# Graphic params that carry the words a graphic SHOWS as its hero. Kickers,
# labels, names and sub-lines are connective or identity text.
_HERO_KEYS = {"text", "value", "left", "right", "word", "words", "title",
              "headline", "number", "figure", "query", "rows", "items",
              "lines", "steps"}


def _norm(word):
    w = re.sub(r"[^a-z0-9%$]+", "", str(word or "").lower().replace("’", "'")
               .replace("'", ""))
    return w.strip("$")


def say(word):
    """A spoken word as a message quotes it (no trailing punctuation)."""
    return str(word or "").strip(" .,!?;:\"'“”‘’")


def tokens(text):
    return [t for t in (_norm(x) for x in re.split(r"[\s/\-–—_,.;:!?()\"]+",
                                                    str(text or ""))) if t]


def sound_id(item):
    return sound_library.id_for_key((item or {}).get("storage_key"))


def role_of(item):
    """The library role of an sfx item (its recording's role, else a
    keyword match on its key and purpose), or None."""
    sid = sound_id(item)
    if sid:
        return (sound_library.get(sid) or {}).get("role")
    hay = " ".join(str((item or {}).get(k) or "")
                   for k in ("storage_key", "purpose")).lower()
    words = set(re.split(r"[^a-z]+", hay))
    for role, vocab in _ROLE_VOCAB:
        if any(v in words for v in vocab):
            return role
    return None


def hit_time(item):
    return float(sound_library.hit_at(item))


def program_words(edl, index):
    """Kept words on the program clock: [{'w', 't0', 't1'}]."""
    from timeline import Timeline
    words = (index or {}).get("words") or []
    if not words:
        return []
    try:
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
        return [w for w in tl.kept_words(words)
                if float(w["t1"]) > float(w["t0"]) - 1e-6]
    except Exception:          # noqa: BLE001
        return []


# ── what is on screen ─────────────────────────────────────────────────────

# Templates whose picture shows a real-world action whatever their declared
# cues are (a photo card is a photo being shown; a search bar is typed into).
TEMPLATE_DEPICTS = {
    "shutter": {"image_card", "photo_stack", "post_card"},
    "ding": {"notification", "chat_bubbles", "post_card"},
    "typing": {"typewriter", "search_bar", "comment_cta", "chat_bubbles"},
    "click": {"search_bar", "comment_cta", "follow_cta", "save_cta"},
    "tick": {"counter", "progress_ring", "checklist", "timeline_steps"},
    "pop": {"emoji_pop", "chat_bubbles"},
    "glitch": {"glitch_burst", "text_scramble", "glow_title"},
}


def _cue_roles(m):
    """Library roles a motion item's own designed sound cues play for ITS
    params (a cue's `when` gate applies), plus what its template shows
    (TEMPLATE_DEPICTS): what the graphic itself depicts — a typewriter
    types, a notification dings, a photo card is a photo."""
    import motion_templates
    template = m.get("template") or ""
    out = {role for role, names in TEMPLATE_DEPICTS.items() if template in names}
    try:
        spec = motion_templates.spec(template)
        import motion_tools
        applies = motion_tools._cue_applies
    except Exception:          # noqa: BLE001
        return out
    params = m.get("params") or {}
    for c in spec.get("sfx") or []:
        if not applies(c, params):
            continue
        role = sound_library.ROLE_ALIASES.get(c.get("kind"), c.get("kind"))
        if role:
            out.add(role)
    return out


def _motion_landings(m):
    """Program seconds a motion graphic visibly lands: its entrance and
    exit, its template's declared landing moments (the cue times its own
    sound would hit, whether or not sound is on) and per-row reveal times."""
    try:
        s, e = float(m["start"]), float(m["end"])
    except (KeyError, TypeError, ValueError):
        return []
    times = [s, e]
    params = m.get("params") or {}
    try:
        import motion_templates
        import motion_tools
        spec = motion_templates.spec(m.get("template") or "")
        times += [c[0] for c in motion_tools._sfx_cues(spec, params, s, e)]
    except Exception:          # noqa: BLE001
        pass
    for v in params.values():
        if isinstance(v, list):
            for row in v:
                if isinstance(row, dict) and row.get("at") not in (None, ""):
                    try:
                        times.append(s + float(str(row["at"]).replace(",", ".")))
                    except ValueError:
                        pass
    return sorted({round(t, 3) for t in times if s - 1e-6 <= t <= e + 1e-6})


def _shot_of(shots, t):
    for k, sh in enumerate(shots or []):
        try:
            if float(sh["start"]) - 1e-3 <= t < float(sh["end"]) + 1e-3:
                return sh.get("id", k)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def visual_events(edl, index=None):
    """[(t, label)] program seconds where the picture visibly changes in a
    way a sound may belong to: graphic landings, entrances and exits, B-roll
    entries and exits, a cut to a different camera shot, text, overlay,
    zoom, card and effect edges. An ordinary cut inside one shot of a
    conversation is not one (owner policy: never a sound on ordinary cuts)."""
    from timeline import program_blocks
    ev = []
    try:
        blocks = program_blocks(edl)
    except Exception:          # noqa: BLE001
        blocks = []
    shots = (index or {}).get("shots") or []
    for k, b in enumerate(blocks):
        if k and b["kind"] == "insert":
            ev.append((float(b["out_start"]), "a B-roll entry"))
        elif k and blocks[k - 1]["kind"] == "insert":
            ev.append((float(b["out_start"]), "the cut back from B-roll"))
        elif k:
            a = _shot_of(shots, float(blocks[k - 1]["src_end"]) - 0.02)
            c = _shot_of(shots, float(b["src_start"]) + 0.02)
            if a is not None and c is not None and a != c:
                ev.append((float(b["out_start"]), "a cut to another shot"))
    for m in edl.get("motion") or []:
        name = f"{m.get('template')} graphic {m.get('id')}"
        land = _motion_landings(m)
        for t in land:
            ev.append((t, f"the {name}"))
    for key, label in (("texts", "text"), ("vectors", "shape")):
        for it in edl.get(key) or []:
            for f in ("start", "end"):
                if it.get(f) is not None:
                    ev.append((float(it[f]), f"{label} {it.get('id')}"))
    for it in edl.get("overlays") or []:
        try:
            s = float(it["start"])
            ev += [(s, f"overlay {it.get('id')}"),
                   (s + float(it.get("duration_s") or 0), f"overlay {it.get('id')}")]
        except (KeyError, TypeError, ValueError):
            continue
    fx = edl.get("effects") or {}
    for key, label in (("zooms", "zoom"), ("picture_cards", "picture card"),
                       ("stylize", "effect")):
        for it in fx.get(key) or []:
            for f in ("start", "end"):
                if it.get(f) is not None:
                    ev.append((float(it[f]), f"{label} {it.get('id')}"))
    for it in fx.get("frame_shifts") or []:
        if it.get("at") is not None:
            ev.append((float(it["at"]), f"frame shift {it.get('id')}"))
    return sorted(ev)


def depicts(edl, role, t):
    """Whether something on screen at program second t shows `role`'s
    action: a B-roll insert or overlay (any), or a motion graphic whose own
    designed cues play that role."""
    from timeline import program_blocks
    try:
        for b in program_blocks(edl):
            if b["kind"] == "insert" and b["out_start"] - 0.05 <= t <= b["out_end"]:
                return True
    except Exception:          # noqa: BLE001
        pass
    for it in edl.get("overlays") or []:
        try:
            s = float(it["start"])
            if s - 0.05 <= t <= s + float(it.get("duration_s") or 0):
                return True
        except (KeyError, TypeError, ValueError):
            continue
    for m in edl.get("motion") or []:
        try:
            on = float(m["start"]) - 0.05 <= t <= float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if on and role in _cue_roles(m):
            return True
    return False


# ── what the viewer must hear ─────────────────────────────────────────────

def _hero_tokens(params):
    out = set()

    def walk(v, hero):
        if isinstance(v, str):
            if hero:
                out.update(t for t in tokens(v.replace("*", " ")))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            if hero:
                out.add(_norm(f"{v:g}"))
        elif isinstance(v, list):
            for x in v:
                walk(x, hero)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, hero and k in ("text", "value", "label_hero"))
    for k, v in (params or {}).items():
        if k in _HERO_KEYS:
            walk(v, True)
    return {t for t in out if t and t not in _STOP
            and (len(t) > 1 or t.isdigit())}


def protected_words(edl, words):
    """Payoff/emphasis words on the program clock: the spoken words a
    designed graphic shows (spoken while it is up) and the captions'
    emphasis words. [{'w', 't0', 't1', 'why'}]."""
    out = {}
    for m in edl.get("motion") or []:
        try:
            s, e = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        hero = _hero_tokens(m.get("params") or {})
        if not hero:
            continue
        for w in words:
            if s - 0.35 <= float(w["t0"]) <= e and _norm(w["w"]) in hero:
                out.setdefault(round(float(w["t0"]), 3), dict(
                    w, why=f"the {m.get('template')} graphic "
                           f"{m.get('id')} shows it"))
    caps = edl.get("captions") or {}
    emph = {_norm(x) for x in (caps.get("emphasis_words") or [])
            if isinstance(caps, dict)} - {""}
    for w in words:
        if _norm(w["w"]) in emph:
            out.setdefault(round(float(w["t0"]), 3),
                           dict(w, why="a caption emphasis word"))
    return [out[k] for k in sorted(out)]


# ── the checks ────────────────────────────────────────────────────────────

def talking_short(edl, words):
    """A vertical/square short of TALKING_SHORT_MAX_S or less carried by
    speech — the podcast-short case the owner's zero-sound default is for."""
    from schemas import program_duration
    frame = edl.get("frame") or {}
    ratio = frame.get("ratio") if isinstance(frame, dict) else None
    try:
        dur = float(program_duration(edl))
    except Exception:          # noqa: BLE001
        return False
    return (ratio in ("9:16", "4:5", "1:1") and 0 < dur <= TALKING_SHORT_MAX_S
            and len(words) >= TALKING_SHORT_MIN_WORDS)


def _nearest(events, t):
    if not events:
        return None
    return min(events, key=lambda e: abs(e[0] - t))


def partner(events, role, hit):
    """(event, within) — the nearest visual event and whether it is close
    enough to be this sound's partner."""
    tol = (SWEEP_PARTNER_S if role in SWEEP_ROLES else PARTNER_S) + GRID_S
    ev = _nearest(events, hit)
    return ev, bool(ev and abs(ev[0] - hit) <= tol)


def collides(role, hit, protected):
    """The protected word whose onset a bright sound lands on, or None."""
    if role not in BRIGHT_ROLES:
        return None
    for w in protected:
        o = float(w["t0"])
        if o - ONSET_GUARD_S - GRID_S <= hit <= o + ONSET_PROTECT_S:
            return w
    return None


def pun(edl, role, hit, words):
    """The spoken word a literal foley sound puns on, or None."""
    lex = PUN_WORDS.get(role)
    if not lex:
        return None
    for w in words:
        if (float(w["t0"]) - PUN_BEFORE_S <= hit <= float(w["t1"]) + PUN_AFTER_S
                and _norm(w["w"]) in lex):
            if not depicts(edl, role, hit):
                return w
    return None


def speech_gaps(words, a, b, end=None):
    """[(g0, g1)] silences of at least GAP_MIN_S between kept words that
    overlap program [a, b]; with `end`, the silence after the last word up
    to it counts too."""
    out = []
    words = sorted(words, key=lambda w: float(w["t0"]))
    for x, y in zip(words, words[1:]):
        g0, g1 = float(x["t1"]), float(y["t0"])
        if g1 - g0 >= GAP_MIN_S and g1 > a and g0 < b:
            out.append((g0, g1))
    if words and end is not None:
        g0 = float(words[-1]["t1"])
        if end - g0 >= GAP_MIN_S and end > a and g0 < b:
            out.append((g0, float(end)))
    return out


def nudge(events, words, word, role, hit, end=None):
    """(new_hit, label) — a visual partner inside a speech gap next to the
    protected word, clear of its onset, within NUDGE_REACH_S; or None.
    `end` (the last second a sound may hit) closes the final gap."""
    o = float(word["t0"])
    best = None
    for g0, g1 in speech_gaps(words, o - NUDGE_REACH_S, o + NUDGE_REACH_S,
                              end=end):
        for t, label in events:
            if not (g0 - GRID_S <= t <= g1 + GRID_S):
                continue
            if end is not None and t > end:
                continue
            if o - ONSET_GUARD_S - 0.05 <= t <= o + ONSET_PROTECT_S:
                continue
            if abs(t - o) > NUDGE_REACH_S:
                continue
            if best is None or abs(t - hit) < abs(best[0] - hit):
                best = (round(t, 3), label)
    return best


def _fmt_role(role):
    return role or "sound"


def check_item(edl, item, words, events=None, protected=None, talking=None,
               index=None):
    """Advisory placement findings for one sfx item:
    [{'code', 'message', ...}] with codes no_visual_partner, on_payoff_word,
    literal_pun, opening_whoosh."""
    events = visual_events(edl, index) if events is None else events
    protected = protected_words(edl, words) if protected is None else protected
    if talking is None:
        talking = talking_short(edl, words)
    role = role_of(item)
    hit = hit_time(item)
    sid = item.get("id") or "?"
    name = f"sfx {sid} ({_fmt_role(role)})"
    found = []
    ev, ok = partner(events, role, hit)
    if not ok:
        near = (f"; nearest: {ev[1]} at {ev[0]:.2f}s" if ev else "")
        found.append({
            "code": "no_visual_partner", "id": sid, "at": round(hit, 2),
            "message": (
                f"{name} hits at {hit:.2f}s with no visual event within "
                f"{int(round((SWEEP_PARTNER_S if role in SWEEP_ROLES else PARTNER_S) * 1000))}"
                f" ms{near} — a sound with nothing on screen to belong to is "
                "decoration. Land it on the frame where something changes "
                "(move_sfx) or remove it (remove_sfx).")})
    w = collides(role, hit, protected)
    if w:
        found.append({
            "code": "on_payoff_word", "id": sid, "at": round(hit, 2),
            "word": say(w["w"]), "word_t0": round(float(w["t0"]), 2),
            "message": (
                f"{name} hits on the onset of '{say(w['w'])}' at "
                f"{float(w['t0']):.2f}s ({w['why']}) — a bright sound over "
                "the first 300 ms of a payoff or emphasis word competes "
                "with the word it should sell. Remove it, or move it into "
                "the speech gap after the line where the picture changes.")})
    p = pun(edl, role, hit, words)
    if p:
        found.append({
            "code": "literal_pun", "id": sid, "at": round(hit, 2),
            "word": say(p["w"]),
            "message": (
                f"{name} lands on the spoken word '{say(p['w'])}' with nothing on "
                f"screen showing that action — a literal sound pun (a "
                "shutter on 'pictures', a cash register on 'money') reads "
                "as childish. Remove it unless the real action is shown.")})
    if (talking and role in SWEEP_ROLES and hit <= OPENING_S
            and not str(sid).startswith("mg_")):
        found.append({
            "code": "opening_whoosh", "id": sid, "at": round(hit, 2),
            "message": (
                f"{name} at {hit:.2f}s is the reflexive opening whoosh — a "
                "podcast short opens on the title and the voice; keep one "
                "only if the opening has a real entrance that earns it.")})
    return found


def check_edl(edl, index=None, words=None, ids=None):
    """{'items': {sfx id: [findings]}, 'budget': finding or None,
    'talking': bool} for every sfx (or those in `ids`)."""
    words = program_words(edl, index) if words is None else words
    events = visual_events(edl, index)
    protected = protected_words(edl, words)
    talking = talking_short(edl, words)
    items = {}
    for it in edl.get("sfx") or []:
        if ids is not None and it.get("id") not in ids:
            continue
        got = check_item(edl, it, words, events, protected, talking)
        if got:
            items[it.get("id")] = got
    return {"items": items, "budget": budget(edl, talking), "talking": talking}


def sound_events(sfx):
    """Distinct sound moments: a motion graphic's own cue stack and sounds
    within 50 ms of each other are one moment."""
    moments, owners = [], set()
    for it in sorted(sfx or [], key=hit_time):
        sid = str(it.get("id") or "")
        owner = sid.rsplit("_sfx", 1)[0] if sid.startswith("mg_") else None
        if owner and owner in owners:
            continue
        if owner:
            owners.add(owner)
        t = hit_time(it)
        if moments and t - moments[-1] <= 0.05:
            continue
        moments.append(t)
    return moments


def budget(edl, talking):
    """The talking-short sound budget finding, or None."""
    if not talking:
        return None
    n = len(sound_events(edl.get("sfx") or []))
    if n <= TALKING_SHORT_MAX:
        return None
    return {"code": "over_sound_budget", "count": n,
            "message": (
                f"{n} separate sound moments in this talking short — podcast "
                "shorts default to zero and carry at most 1-2, each on a "
                "structural moment (the payoff, a real section change) with "
                "a visual partner. Keep the one or two that earn it and "
                "remove the rest.")}
