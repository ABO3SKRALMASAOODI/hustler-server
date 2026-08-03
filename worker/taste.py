"""The editorial critic: deterministic taste checks over a finished EDL.

Round 52. Every audit that already existed asks "is this edit CORRECT?" — no
mid-word boundary, no repeated phrase, captions that actually have words to
burn. Nothing asked "is this edit any GOOD?", and a day of real customer
transcripts is what that costs: a preacher's reel, a Valorant montage, a plant
timelapse, a soda taste-test and a gym reel all came back with the SAME edit —
cinematic grade, vignette, 1s fade in from black, punch zooms on the loudest
words, dip-to-black at the cuts, music ducked, -14 LUFS. Two of those briefs
said in writing "no black screens at the start" and got a fade from black
anyway. That is not a model that lacks capability; it is a model with no taste
and nothing telling it so.

So this module is the reviewer for craft, in the same shape as audit.py: pure
functions over plain data, no LLM, no network, no I/O. render_preview stamps
the findings into the tool result, exactly like the mid-word and repetition
audits, and the system prompt makes acting on them non-optional. A finding is
never a hard block — the user's own instruction always wins, and every finding
says what to do instead in one clause, so the agent can fix it in one call or
tell the user why it kept it.

Each check earns its place by having actually shipped as a defect:

  * fade-in from black on a short-form vertical  (3 real edits, 2 of which
    explicitly forbade it in the brief)
  * a punch zoom landing at 0.3s / 0.8s / 1.0s — a shove, not an emphasis
  * 45 whip transitions through one continuous shot (round 48) and its
    sequel, 10 whips on jump cuts inside one talking-head take
  * 9 slurp sound effects the user removed as mistimed, placed one per sip
  * "cinematic" grade (crushed, desaturated) on a bright kitchen taste-test
  * captions burned into the bottom 6% of a 9:16 frame, i.e. under the
    TikTok/Reels UI
  * a 4:50 "reel"
  * every zoom identical: same 35%, same mode, five times
"""

from timeline import program_blocks, transition_junctions

# What counts as short-form: a vertical or square output, which on every
# platform that takes one is a feed video watched thumb-down and muted.
SHORT_FORM_MAX_S = 180.0

# Platform chrome eats the bottom of a vertical frame (caption text, the
# username, the action rail). Anything burned below this is under the UI.
VERTICAL_UNSAFE_BOTTOM_FRAC = 0.13

# One zoom per this many seconds of programme is already a lot. Past it the
# picture never sits still and nothing reads as emphasis any more.
ZOOM_MIN_SPACING_S = 6.0
ZOOM_PER_S = 11.0
# A zoom that lands before the viewer has seen the shot is a shove.
ZOOM_MIN_START_S = 1.2

SFX_PER_S = 8.0
SFX_MIN_SPACING_S = 0.35

# Programme seconds per junction effect, below which transitions stop marking
# anything and become the thing being watched. Defined HERE and imported by
# agent_tools (which already imports this module), so the sentence
# set_transitions prints and the sentence this audit prints cannot disagree
# about what "too often" means. 5s is deliberately permissive — a hype
# montage lives at the fast end — and the real defect it catches sat at 3.3s.
TRANSITION_MIN_SPACING_S = 5.0

# EVERY attention-grabbing device counted together, per programme second.
# 'scene' scope bounds transitions correctly and bounds nothing else: the
# edit that produced this check had 9 transitions + 3 zooms + 3 stylize
# windows + 5 sfx over 30 seconds — 20 devices, one every 1.5s — while every
# individual rule was either satisfied or only mildly over. Users do not
# experience the categories separately; they experience the rate.
DEVICE_MIN_SPACING_S = 2.5

# Words that mean the user actually wants sound effects. Sfx are OPT-IN: they
# are the loudest unrequested thing an edit can do, they are what people
# notice first when they are wrong, and an editor who adds them uninvited to
# someone's footage is not showing taste.
SFX_REQUEST_HINTS = (
    "sfx", "sound effect", "sound fx", "soundeffect", "whoosh", "swoosh",
    "impact", "boom", "riser", "sound design", "sounddesign", "efecto de "
    "sonido", "efectos de sonido", "efek suara", "sonido", "sfx-", "swoosh",
    "add sound", "sounds on", "punchy sound", "hit sound", "click sound",
    "مؤثرات", "مؤثر صوتي",
)

# More than this many whole-programme finishing effects and the footage is
# wearing the look rather than the look serving the footage.
MAX_GLOBAL_STYLIZE = 2

# The same bound, applied to an INSTANT instead of to the whole programme.
# DEVICE_MIN_SPACING_S divides devices by RUNTIME, so anything fired at the
# same moment averages itself away: a real user asked for six things on a
# 17.7s clip and got flash + shake + glow inside ONE 0.7-second window, with
# fifteen untouched seconds around it. Five devices over 17.7s reads as "one
# every 3.5s", every category rule passed, and what shipped was one moment
# that detonates and a video that is otherwise raw. Asking for six devices is
# asking for six MOMENTS — a pile is what a user means by "it did everything
# at once".
MAX_SIMULTANEOUS_DEVICES = 2

# Speech that starts later than this into the programme is a cold open the
# viewer did not ask for.
HOOK_DEAD_AIR_S = 1.5

_AGGRESSIVE_STYLIZE = ("flash", "chromatic", "vhs", "shake", "glitch")


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _densest_moment(items):
    """The instant covered by the most WINDOWED devices, as
    (start, end, [labels]) — or None when nothing overlaps anything.

    `items` are (start, end, label) in programme seconds. Only devices that
    fire AT a moment belong here: a whole-programme grain or vignette is a
    LOOK, live everywhere, and counting it would report every edit that wears
    one as a pile-up.

    Sweeping the interval STARTS is exact — the maximum overlap of a set of
    intervals is always reached at one of their start points — and needs no
    event sort for the handful of devices an edit ever carries."""
    best = []
    for t, _end, _label in items:
        live = [it for it in items if it[0] <= t < max(it[1], it[0] + 1e-6)]
        if len(live) > len(best):
            best = live
    if not best:
        return None
    lo = max(i[0] for i in best)
    return lo, max(lo, min(i[1] for i in best)), [i[2] for i in best]


def _ratio_wh(edl, src_w, src_h):
    """(w, h) of the OUTPUT frame — the EDL's ratio when it sets one, else the
    source's own shape. Returns (None, None) when neither is knowable."""
    ratio = ((edl.get("frame") or {}).get("ratio")) or None
    if ratio and ratio != "source" and ":" in str(ratio):
        try:
            a, b = str(ratio).split(":")
            return float(a), float(b)
        except (TypeError, ValueError):
            pass
    if src_w and src_h:
        return float(src_w), float(src_h)
    return None, None


def describe_format(edl, index, out_duration, src_w=None, src_h=None):
    """What KIND of video this is, from measurable facts only.

    The agent gets this at the top of the audit because most taste rules are
    conditional on it: a fade from black is right for a 3-minute cinematic
    landscape piece and wrong for a 40-second reel, and no amount of prompt
    prose makes that distinction reliably without the numbers in front of it.
    """
    w, h = _ratio_wh(edl, src_w, src_h)
    vertical = bool(w and h and h > w * 1.05)
    square = bool(w and h and abs(w - h) <= w * 0.05)
    words = index.get("words") or []
    n_words = len(words)
    shots = index.get("shots") or []
    has_music = bool(edl.get("music"))
    wpm = (n_words / out_duration * 60.0) if out_duration > 0 else 0.0
    short_form = (vertical or square) and out_duration <= SHORT_FORM_MAX_S

    if n_words == 0:
        kind = "no-speech piece (montage / b-roll / music-led)"
    elif wpm >= 60 and len(shots) <= 3:
        kind = "talking head"
    elif wpm >= 40:
        kind = "narrated piece"
    else:
        kind = "sparse-speech piece (visual-led)"
    return {"vertical": vertical, "square": square, "short_form": short_form,
            "kind": kind, "n_words": n_words, "n_shots": len(shots),
            "has_music": has_music, "duration": out_duration}


def _speech_starts_at(index, tl):
    """Programme second of the first surviving spoken word, or None."""
    # kept_words returns {'w','t0','t1'} already mapped to PROGRAMME time, so
    # the first entry's t0 is the second the viewer first hears a word.
    words = tl.kept_words(index.get("words") or []) if tl else []
    return float(words[0]["t0"]) if words else None


def critique(edl, index, tl, src_w=None, src_h=None, user_asked=""):
    """Craft findings for a rendered EDL. Returns a list of one-line strings.

    `user_asked` is the user's own message for this turn, lowercased by the
    caller or not — it is only ever used to SUPPRESS a finding, never to raise
    one, so that asking for a thing is always allowed. "Do only what the user
    asked" is the standing rule; a critic that argued with an explicit
    instruction would be worse than no critic.
    """
    ask = (user_asked or "").lower()
    out_dur = float(getattr(tl, "out_duration", 0.0) or 0.0)
    fmt = describe_format(edl, index, out_dur, src_w, src_h)
    fx = edl.get("effects") or {}
    found = []

    def add(msg):
        found.append(msg)

    # ── the opening ──────────────────────────────────────────────────────
    fade_in = _num(fx.get("fade_in_s"))
    if fade_in > 0.05 and fmt["short_form"] and "fade in" not in ask:
        add(f"opens with a {fade_in:.1f}s fade from BLACK on a "
            f"{'vertical' if fmt['vertical'] else 'square'} short-form edit — "
            "the first second is the only one every viewer watches and this "
            "spends it on nothing. Drop it (set_fades fade_in_s=0) and open "
            "on the picture, unless the user asked for the fade.")

    speech_at = _speech_starts_at(index, tl) if fmt["n_words"] else None
    if speech_at is not None and speech_at > HOOK_DEAD_AIR_S \
            and fmt["short_form"]:
        add(f"the first words land at {speech_at:.1f}s — everything before "
            "that is dead air at the most expensive moment of the video. "
            "Cut into the strongest line, or move it to the front.")

    zooms = sorted((fx.get("zooms") or []),
                   key=lambda z: _num(z.get("start")))
    if zooms:
        first = _num(zooms[0].get("start"))
        if first < ZOOM_MIN_START_S:
            add(f"the first zoom fires at {first:.1f}s, before the viewer has "
                "read the shot — a push that early reads as a camera bump, "
                "not emphasis. Move it past 1.5s or drop it.")

    # ── zoom rhythm ──────────────────────────────────────────────────────
    if out_dur > 0 and len(zooms) > max(2, int(out_dur / ZOOM_PER_S)):
        add(f"{len(zooms)} zooms across {out_dur:.0f}s is roughly one every "
            f"{out_dur / max(1, len(zooms)):.0f}s — when the frame is always "
            "moving, none of the moves mean anything. Keep the 2-3 that land "
            "on the real turns and remove the rest.")
    # A push that lands ON a cut is not fighting the one before it: the cut
    # resets the eye, and cut-plus-punch is a deliberate, standard move.
    # Round 75: without this exemption, a scene-3 message zoom ending at
    # 8.85s and a scene-4 zoom starting at the 8.88s scene boundary read as
    # "back-to-back pushes", and the agent obeyed the audit over the user —
    # deleting a zoom the user had EXPLICITLY asked to keep.
    try:
        cut_points = [float(bl["out_start"])
                      for bl in program_blocks(edl)[1:]]
    except Exception:
        cut_points = []
    tight = [(a, b) for a, b in zip(zooms, zooms[1:])
             if _num(b.get("start")) - _num(a.get("start"))
             < ZOOM_MIN_SPACING_S
             and not any(_num(a.get("end")) - 0.15 <= c
                         <= _num(b.get("start")) + 0.15
                         for c in cut_points)]
    if tight:
        a, b = tight[0]
        add(f"two zooms {_num(b.get('start')) - _num(a.get('start')):.1f}s "
            f"apart (at {_num(a.get('start')):.1f}s and "
            f"{_num(b.get('start')):.1f}s) — back-to-back pushes fight each "
            "other. Space them out or keep the stronger one.")
    # ABRUPT zooms (round 67). The owner traced "very weird and bad" edits
    # to hard 30%+ snaps used as the routine move — modern emphasis is a
    # gentle 8-18% push. ONE hard punch on the single peak is a legitimate
    # device, so this fires only when hard snaps are the PATTERN, and never
    # when the user asked for punchy/hard zooms.
    hard = [z for z in zooms if _num(z.get("strength")) >= 0.3
            and (z.get("mode") or "punch") == "punch"]
    if len(hard) >= 2 and not any(h in ask for h in (
            "punch", "hard zoom", "aggressive", "snap")):
        add(f"{len(hard)} hard punch zooms at "
            f"{int(_num(hard[0].get('strength')) * 100)}%+ — abrupt snaps "
            "used as the routine move read as amateur editing. Keep at most "
            "ONE hard punch on the single biggest peak and make the rest "
            "gentle eased pushes (strength 0.08-0.18, mode 'ease' or "
            "'push_in').")
    if len(zooms) >= 3:
        shapes = {(round(_num(z.get("strength")), 2), z.get("mode") or "punch")
                  for z in zooms}
        if len(shapes) == 1:
            s, m = next(iter(shapes))
            add(f"all {len(zooms)} zooms are the identical {int(s * 100)}% "
                f"'{m}' — repetition with no variation reads as an automated "
                "pass, not an edit. Vary strength and mode (a slow push_in "
                "under a line, one hard punch on the peak).")

    # ── junction effects ─────────────────────────────────────────────────
    trans = fx.get("transition") or None
    if trans:
        n_blocks = len(edl.get("keep") or []) + len(edl.get("inserts") or [])
        try:
            juncs = transition_junctions(edl, index, n_blocks=n_blocks)
        except Exception:
            juncs = set()
        scope = (trans.get("scope") or "scene")
        if scope == "every_cut" and len(juncs) > 6 and fmt["n_shots"] <= 3:
            add(f"a '{trans.get('style')}' transition fires on all "
                f"{len(juncs)} cuts, but the index sees only "
                f"{fmt['n_shots']} shot(s) — those cuts are jump cuts inside "
                "one continuous take and are supposed to be INVISIBLE. A "
                "full-screen effect every few seconds through footage that "
                "never changed scene is the single most common 'this looks "
                "broken' complaint. Use scope='scene'.")
        # CADENCE, independent of scope. 'scene' asks whether a junction has
        # earned an effect and says nothing about how often that comes round:
        # a montage cut from nine far-apart source spans has nine REAL scene
        # changes, so all nine qualified and a 30s edit fired a whip every
        # 3.3s. "Look at how fast the scene transitions are — it's literally
        # putting one every second" is a rate complaint, and only a rate
        # check catches it.
        if out_dur > 0 and len(juncs) >= 3 \
                and out_dur / len(juncs) < TRANSITION_MIN_SPACING_S:
            add(f"{len(juncs)} '{trans.get('style')}' transitions across "
                f"{out_dur:.0f}s — one every "
                f"{out_dur / len(juncs):.1f}s. Even where every junction is a "
                "real scene change, a full-screen effect at that rate is what "
                "the viewer watches instead of the video. A montage is "
                "carried by HARD cuts; keep the effect for the two or three "
                "changes that need to be felt, or drop them entirely "
                "(set_transitions('none')).")

    # ── the ending ───────────────────────────────────────────────────────
    fade_out = _num(fx.get("fade_out_s"))
    if fade_out > 0.05 and fmt["short_form"] and "fade out" not in ask \
            and "fade to black" not in ask:
        add(f"ends on a {fade_out:.1f}s fade to BLACK — short-form loops, so "
            "the fade plays into the restart and reads as a stall. End on "
            "the last word or beat (set_fades fade_out_s=0) unless the user "
            "wants the fade.")

    # ── sound ────────────────────────────────────────────────────────────
    sfx = sorted((edl.get("sfx") or []), key=lambda s: _num(s.get("at")))
    # SFX ARE OPT-IN (round 55). Five whooshes and booms went onto a montage
    # nobody had asked for sound effects on, and the user's instruction was
    # unambiguous: "it is adding sound effects badly, it has no taste at all,
    # it should not add them unless requested explicitly." The rule that
    # follows is therefore about CONSENT, not about count — and like every
    # rule here it is suppressed the moment the user does ask.
    if sfx and not any(h in ask for h in SFX_REQUEST_HINTS):
        add(f"{len(sfx)} sound effect{'s' if len(sfx) != 1 else ''} "
            "were added and the user did not ask for any. Sound effects are "
            "the loudest uninvited thing an edit can do and the first thing "
            "people notice when they are wrong — they belong on a moment the "
            "viewer can SEE, at the user's request, not sprinkled through a "
            "cut. Remove them (remove_sfx) unless a specific one is landing "
            "on a real hit you can point at, and offer them instead of "
            "adding them.")
    if out_dur > 0 and len(sfx) > max(3, int(out_dur / SFX_PER_S)):
        add(f"{len(sfx)} sound effects in {out_dur:.0f}s — accents stop being "
            "accents when they are constant. 3-6 placed on the real moments "
            "beat one on every cut.")
    stacked = [(a, b) for a, b in zip(sfx, sfx[1:])
               if _num(b.get("at")) - _num(a.get("at")) < SFX_MIN_SPACING_S]
    if stacked:
        a, b = stacked[0]
        add(f"two sound effects {_num(b.get('at')) - _num(a.get('at')):.2f}s "
            f"apart at {_num(a.get('at')):.1f}s — they land as one muddy hit. "
            "Keep one.")

    music = edl.get("music") or []
    if music and fmt["n_words"] > 20:
        loud = [m for m in music
                if _num(m.get("gain_db"), -18.0) > -10.0 and m.get("duck")
                is not True]
        if loud:
            add("music sits at "
                f"{_num(loud[0].get('gain_db'), -18.0):.0f}dB with no ducking "
                "under a video that has speech — the voice is what people "
                "came for. Duck it (set_music_fit duck_mode='smooth') or drop "
                "the bed to around -18dB.")
    for m in music:
        m_end = _num(m.get("end"), out_dur)
        if out_dur - m_end > max(2.0, out_dur * 0.12):
            add(f"the music stops at {m_end:.0f}s but the video runs to "
                f"{out_dur:.0f}s — the last "
                f"{out_dur - m_end:.0f}s play dry, which sounds like a "
                "mistake. Extend it (set_music_fit) or fade it out on "
                "purpose.")
            break

    # ── the look ─────────────────────────────────────────────────────────
    stylize = fx.get("stylize") or []
    global_kinds = {s.get("kind") for s in stylize
                    if s.get("start") is None and s.get("end") is None}
    if len(global_kinds) > MAX_GLOBAL_STYLIZE:
        add(f"{len(global_kinds)} finishing effects run over the WHOLE video "
            f"({', '.join(sorted(k for k in global_kinds if k))}) — stacked "
            "full-length passes read as a broken TV, not a look. Keep one, "
            "maybe two.")
    aggressive = sorted({s.get("kind") for s in stylize
                         if s.get("kind") in _AGGRESSIVE_STYLIZE})
    t_style = (trans or {}).get("style")
    if t_style in ("flash", "glitch") and aggressive:
        add(f"'{t_style}' transitions AND {', '.join(aggressive)} stylize are "
            "both running — pick ONE aggressive device for the whole video. "
            "Two is where an edit stops looking deliberate.")

    grade = fx.get("grade")
    gc = fx.get("grade_custom") or {}
    if grade == "cinematic" and _num(gc.get("saturation"), 1.0) > 1.15:
        add("a 'cinematic' grade (which desaturates and crushes) is being "
            "re-saturated by grade_custom — the two cancel out and the result "
            "is muddy. Pick one: cinematic for mood, or 'vibrant' plus "
            "contrast for punch.")
    # No blanket rule against any one grade: 'cinematic' is genuinely right
    # for a lot of footage, and a critic that fires on a defensible choice
    # teaches the agent to ignore the whole audit.

    # ── captions ─────────────────────────────────────────────────────────
    caps = edl.get("captions")
    caps_on = (isinstance(caps, dict)
               and caps.get("mode") == "from_transcript") \
        or (isinstance(caps, list) and bool(caps))
    if fmt["short_form"] and fmt["n_words"] >= 25 and not caps_on \
            and "no caption" not in ask and "sin subtítulo" not in ask:
        add("a talking short-form video with no captions — most of the feed "
            "watches muted, so uncaptioned speech is watched by nobody. "
            "add_captions('from_transcript') with a premium preset unless the "
            "user asked for none.")
    # MULTI-WORD CAPTIONS ACROSS THE FACE (round 67). The placement law:
    # multi-word captions live at the bottom; only a single-word-at-a-time
    # look may hold the middle of the frame, because one word shares the
    # frame with a face and a paragraph does not. Presets default correctly
    # now, so this only fires when position='middle' was explicitly written.
    if isinstance(caps, dict) and caps.get("mode") == "from_transcript":
        cstyle = caps.get("style") or {}
        multi = (cstyle.get("preset") or "") != "spotlight" \
            and int(caps.get("max_words_per_caption") or 99) > 1
        if multi and cstyle.get("position") == "middle" \
                and "middle" not in ask and "center" not in ask \
                and "centre" not in ask:
            add("multi-word captions are anchored mid-frame — a block of "
                "text across the speaker's face. Multi-word captions belong "
                "at the BOTTOM (set_caption_style position 'bottom'); only "
                "a single-word-at-a-time look ('spotlight', or "
                "max_words_per_caption=1) may sit centred.")

    # ── overlapping text ────────────────────────────────────────────────
    texts = edl.get("texts") or []
    mutes = edl.get("caption_mutes") or []
    # TEXT ON TOP OF TEXT. graphics._stack_concurrent now pushes colliding
    # graphics apart so this can no longer RENDER as a pile-up, but a stack
    # of three simultaneous cards is still a composition nobody chose: the
    # edit that prompted this had a title, a subtitle and a callout all
    # burning over the last 1.5 seconds. Report the intent, not just the
    # collision — the renderer has already saved the frame.
    concurrent = []
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            # A title card's own title + subtitle share its anchor and ARE one
            # designed graphic. Flagging that pair would fire on every correct
            # card and teach the agent to ignore the whole audit.
            ins = a.get("anchor_insert")
            if ins and ins == b.get("anchor_insert"):
                continue
            a0, a1 = _num(a.get("start")), _num(a.get("end"))
            b0, b1 = _num(b.get("start")), _num(b.get("end"))
            if min(a1, b1) - max(a0, b0) > 0.25:
                concurrent.append((a, b))
    if concurrent:
        a, b = concurrent[0]
        add(f"{len(concurrent)} pair(s) of text cards are on screen at the "
            f"same time (e.g. '{str(a.get('text'))[:20]}' and "
            f"'{str(b.get('text'))[:20]}' around "
            f"{max(_num(a.get('start')), _num(b.get('start'))):.1f}s). They "
            "are laid out so they cannot overlap, but two or three lines "
            "competing in one frame is still one message too many — the "
            "viewer reads none of them. Keep the one that matters and "
            "remove_text the rest, or space them out. (A title card's own "
            "title + subtitle pair is fine; a third line on top of it is "
            "not.)")
    # ── one type system per video (round 62) ────────────────────────────
    # A real 26s architecture reel shipped with a title in one template, two
    # callouts and a subtitle, entrances mixed fade/pop — four text styles in
    # four sentences reads as a slide deck, not an edit. Templates and
    # entrances are a TYPE SYSTEM: pick one and reuse it. Standalone cards
    # only — a title card's own title+subtitle pair is one designed graphic,
    # same exemption as the overlap check above.
    loose = [t for t in texts if not t.get("anchor_insert")]
    if len(loose) >= 3 and "template" not in ask:
        tpls = {str(t.get("template") or "title") for t in loose}
        # "unset" (template default), not "none": since round 71 "none" is a
        # real entrance (instant text) and must count as its own style.
        ents = {str(t.get("entrance") or "unset") for t in loose}
        if len(tpls) >= 3 or (len(tpls) >= 2 and len(ents) >= 3):
            add(f"{len(loose)} text cards use {len(tpls)} different templates "
                f"and {len(ents)} different entrances — four styles in four "
                "sentences is a slide deck, not a type system. Re-set them "
                "with ONE template and ONE entrance (vary only size or "
                "accent), unless the user asked for the mix.")
    if caps_on and texts:
        clashes = []
        for t in texts:
            ts, te = _num(t.get("start")), _num(t.get("end"))
            if te <= ts:
                continue
            covered = any(_num(ms) <= ts + 0.05 and _num(me) >= te - 0.05
                          for ms, me in mutes)
            if not covered:
                clashes.append((ts, te, t.get("text") or ""))
        if clashes:
            ts, te, txt = clashes[0]
            add(f"a text card ('{txt[:28]}') runs {ts:.1f}-{te:.1f}s while "
                "spoken-word captions are still burning over the same frames "
                f"({len(clashes)} such windows) — two layers of text on top "
                "of each other. set_caption_mutes those windows.")

    # ── pacing ───────────────────────────────────────────────────────────
    speed = edl.get("speed") or []
    slow = [s for s in speed if 0 < _num(s.get("factor"), 1.0) < 0.6]
    if slow:
        add(f"a {_num(slow[0].get('factor'), 1.0):.2f}x slow-motion span — "
            "this pipeline duplicates frames rather than synthesizing them, "
            "so below 0.6x the motion visibly steps. Use 0.6-0.8x, or tell "
            "the user the tradeoff.")
    if fmt["vertical"] and out_dur > SHORT_FORM_MAX_S:
        add(f"a vertical edit running {out_dur / 60:.1f} minutes — vertical "
            "feeds are watched thumb-down and the drop-off is brutal past a "
            "minute or two. Say so and offer a tighter cut, or a series.")

    # ── the frame ────────────────────────────────────────────────────────
    # A crop is a CHOICE to lose the sides, right only when the frame has a
    # subject to follow. auto_reframe measures that in the pixels; this module
    # is pure and cannot, so it checks the one thing visible in the EDL: was
    # the crop AIMED at anything? A crop with no focus point on a big aspect
    # change is a dead-centre window nobody verified — the shape that lands as
    # "it just cut my video down the middle". A crop WITH a focus came from a
    # measurement and is left alone, which is also why the audit stays quiet
    # on the ordinary talking-head reel.
    frame = edl.get("frame") or {}
    aimed = frame.get("focus_x") is not None or frame.get("focus_y") is not None
    if frame.get("mode") == "crop" and not aimed and src_w and src_h:
        rw, rh = _ratio_wh(edl, None, None)
        if rw and rh:
            src_ar, out_ar = float(src_w) / float(src_h), rw / rh
            keep_frac = min(src_ar, out_ar) / max(src_ar, out_ar)
            if keep_frac < 0.7 and "crop" not in ask:
                add(f"the output is centre-CROPPED from "
                    f"{int(src_w)}x{int(src_h)} to {frame.get('ratio')}, "
                    f"throwing away {(1 - keep_frac) * 100:.0f}% of the "
                    "picture, and nothing measured whether the part being "
                    "kept is the part that matters. On a game capture, a "
                    "screen recording or a wide scene the score, the HUD and "
                    "the action at the edges ARE the content, and cutting "
                    "them off is what users call 'it truncated my video "
                    "instead of adjusting it'. Call auto_reframe(ratio) — it "
                    "measures the footage and either aims the crop at a real "
                    "subject or fits the whole frame in over a blurred "
                    "backdrop.")

    # ── the RATE of everything, together ─────────────────────────────────
    # Every rule above bounds ONE category, and a viewer does not experience
    # categories. The edit this check exists for passed or barely tripped each
    # of them separately — 9 transitions, 3 zooms, 3 stylize windows, 5 sfx —
    # and landed as a device every 1.5 seconds. That is the video the user
    # described as "putting a sound and a transition every second".
    if out_dur > 4:
        devices = len(zooms) + len(sfx) + len([s for s in stylize
                                               if s.get("start") is not None])
        if trans:
            try:
                devices += len(transition_junctions(
                    edl, index,
                    n_blocks=len(edl.get("keep") or [])
                    + len(edl.get("inserts") or [])))
            except Exception:
                pass
        if devices >= 6 and out_dur / devices < DEVICE_MIN_SPACING_S:
            add(f"{devices} attention-grabbing devices across {out_dur:.0f}s "
                f"(transitions, zooms, windowed stylize passes and sound "
                f"effects together) — one every "
                f"{out_dur / devices:.1f}s. Each kind may look reasonable on "
                "its own; stacked they are a video that never sits still, and "
                "nothing in it can register as emphasis because everything "
                "is. Strip it back to the few beats that carry the piece — "
                "restraint is the look.")

    # ── the PEAK, not the average ────────────────────────────────────────
    # The rate check above is blind to simultaneity by construction, and a
    # list of requests ("zoom + flash + shake + glow + speed ramp + colour")
    # is exactly the input that invites one moment to carry all of them. See
    # MAX_SIMULTANEOUS_DEVICES. Windowed devices only — a global pass is a
    # look, not a moment — and no `ask` suppression: the user asked for the
    # devices, never for them to land on the same half-second.
    if out_dur > 4:
        pile = _densest_moment(
            [(_num(z.get("start")), _num(z.get("end")),
              f"zoom {z.get('id')}") for z in zooms]
            + [(_num(s.get("start")), _num(s.get("end")),
                f"{s.get('kind') or 'a'} stylize ({s.get('id')})")
               for s in stylize if s.get("start") is not None])
        if pile and len(pile[2]) > MAX_SIMULTANEOUS_DEVICES:
            lo, hi, labels = pile
            add(f"{len(labels)} full-frame devices are live at the same time "
                f"({', '.join(sorted(labels))}) over {lo:.1f}-{hi:.1f}s — "
                f"one {hi - lo:.1f}s moment carrying all of them while the "
                f"other {out_dur - (hi - lo):.0f}s of the video carry none. "
                "Simultaneous effects do not add up, they cancel: the viewer "
                "sees one blown-out instant, not five ideas. SPREAD them "
                "across the moments that earn them — the request was for "
                "several devices, not for several at once — or keep the one "
                "that carries this beat and remove the rest.")

    return found


def audit_line(findings, limit=4):
    """One compact string for a tool result, or '' when the edit is clean."""
    if not findings:
        return ""
    head = findings[:limit]
    more = len(findings) - len(head)
    line = " TASTE AUDIT (craft, not correctness — fix what the user did not "
    line += "explicitly ask for): " + "; ".join(head)
    if more:
        line += f"; (+{more} more)"
    return line + " Re-render after fixing."
