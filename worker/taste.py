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
audits. A finding is advisory evidence, never a hard block — the user's own
instruction and the editor's judgment win, and every finding says what to do
instead in one clause so it can be fixed, investigated, or deliberately kept.

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

Round 2026-10 (premium motion). Findings here are ADVISORY craft notes: they
are shown in the render result labelled "advisory: keep if intentional" and
never become blocking verification findings, repair loops or an export gate.
The density limits are per FORMAT: a vertical/square reel of REEL_MAX_S or
less is measured against premium short-form references (a camera, type or
sound event every few seconds, a whoosh layered into an impact, a hook punch
at 0s), not against long-form restraint. The guards that caught real
failures stay: transitions on every jump cut, identical-zoom repetition,
fade from black on a reel, captions under the platform UI band.
"""

import re

import sound_library
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

# Jump-cut coverage (cut hygiene, Oct 2026). A jump cut inside one take reads
# as an edit only when the framing changes across it: the owner's references
# step the scale by at least ~8% (alternating tight and wide) or move the
# crop. The judged showcase shorts left cuts bare, or "covered" them with 5%
# punches that read as the same frame with the head popping.
JUMP_CUT_MIN_SCALE = 0.08
# Viewport centre travel (fraction of the frame) that reads as a reframe.
JUMP_CUT_MIN_SHIFT = 0.08
# A crop aim moving this much (source fractions) is a new framing.
JUMP_CUT_MIN_AIM = 0.03
# Bare cuts named one by one in the note; the rest are counted.
JUMP_CUT_LIST = 5
# Framing x zoom never enlarges the source past this (agent_tools caps zoom
# writes there): below an 8% step of room a punch cannot cover a cut.
_ZOOM_UPSCALE_MAX = 3.0

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

# ── the premium reel profile (Oct 2026) ─────────────────────────────────
# The limits above were tuned against long-form restraint, and on a reel they
# fired on exactly what the owner's premium references do: a simulated 45s
# podcast reel with 10 eased, aimed zooms, 13 purposeful SFX, one whip and a
# flash+shake hit got seven findings ("restraint is the look") and could not
# ship. Frame-level measurement of 28 reference reels: something visual
# changes every 0.3-0.6s, framing alternates on sentence turns every 2-5s, a
# hook pattern interrupt lands in the first 0.1-0.6s, and SFX are layered
# (whoosh pre-rolled into an impact, sub under a hit). A vertical or square
# output of REEL_MAX_S or less is judged against THAT density. 120s matches
# the podcast_reel editorial contract, so the contract and the audit agree.
REEL_MAX_S = 120.0
REEL_ZOOM_PER_S = 3.0           # flag only past one camera move every 3s
REEL_ZOOM_MIN_SPACING_S = 1.5   # two pushes closer than this fight
REEL_ZOOM_MIN_START_S = 0.0     # a punched-in hook at 0s is a device
# Owner policy: sound like a professional editor, never "whoosh wars" — at
# most about one sound EVENT every 4-5 s (a layered stack is one event).
REEL_SFX_PER_S = 4.5
REEL_DEVICE_MIN_SPACING_S = 0.8
REEL_MAX_SIMULTANEOUS_DEVICES = 4   # punch + flash + shake + zoom = one hit

# Two sounds closer than SFX_MIN_SPACING_S are a muddy flam only when they
# play the SAME role. Different roles are a designed layer (a whoosh whose
# peak lands on an impact, a sub drop under a hit); sounds within this window
# of each other are one stacked hit rather than two mistimed ones.
SFX_LAYER_WINDOW_S = 0.05

_AGGRESSIVE_STYLIZE = ("flash", "chromatic", "vhs", "shake", "glitch")

# Role vocabulary for a sound, matched against its storage key (kit sounds
# are stored as ``kit-<kind>-<fingerprint>.wav``) and then its purpose. Order
# matters: a sub drop is not a generic hit, a riser is not a whoosh.
_SFX_ROLES = (
    ("riser", ("riser", "rise", "build", "buildup", "swell", "tension")),
    ("sub", ("sub", "subdrop", "bass", "808")),
    ("whoosh", ("whoosh", "woosh", "swoosh", "swish", "swipe", "swoop",
                "passby", "transition")),
    ("hit", ("impact", "hit", "boom", "thud", "kick", "slam", "punch",
             "stomp", "bang", "drum")),
    ("ui", ("pop", "click", "tick", "typing", "keyboard", "tap", "bubble",
            "blip", "typewriter")),
    ("tone", ("ding", "chime", "bell", "notification", "coin", "cash",
              "ping", "sparkle", "shimmer")),
    ("glitch", ("glitch", "static", "digital", "error")),
    ("shutter", ("shutter", "camera", "photo")),
    ("scratch", ("scratch", "rewind")),
)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def sfx_time(item):
    """Program second a sound effect lands: an approved library recording
    starts early so its peak HITS then (sound_library.hit_at); any other
    sound lands where it starts. Spacing and flams are heard at the hit."""
    try:
        return sound_library.hit_at(item)
    except (TypeError, ValueError):
        return _num(item.get("at"))


def _role_in(text):
    words = set(re.split(r"[^a-z0-9]+", str(text or "").lower()))
    for role, vocabulary in _SFX_ROLES:
        if words.intersection(vocabulary):
            return role
    return None


def sfx_role(item):
    """Coarse sound-design role of one SFX item, or None when unknowable.

    The storage key wins (kit sounds carry their kind in the key); the
    authored purpose is the fallback for web/library sounds."""
    return (_role_in(item.get("storage_key"))
            or _role_in(item.get("purpose")))


def sfx_owner(item):
    """The motion graphic that owns this sound cue ('mg_<id>_sfxN'), or None.

    A motion graphic's cue stack is part of ONE designed moment: it moves
    and dies with its graphic, so it is judged as that graphic's sound, not
    as N independent accents."""
    sid = str(item.get("id") or "")
    if sid.startswith("mg_") and "_sfx" in sid:
        return sid.rsplit("_sfx", 1)[0]
    return None


def sfx_muddy_pair(a, b):
    """True when two SFX land as one mistimed flam rather than a designed
    layer. Only meaningful for sounds closer than SFX_MIN_SPACING_S."""
    gap = abs(sfx_time(b) - sfx_time(a))
    if gap >= SFX_MIN_SPACING_S:
        return False
    owner = sfx_owner(a)
    if owner and owner == sfx_owner(b):
        return False
    if gap <= SFX_LAYER_WINDOW_S:
        # Stacked on one instant: one thicker hit. Only the SAME file fired
        # twice is an accident rather than a layer.
        return bool(a.get("storage_key")) and \
            a.get("storage_key") == b.get("storage_key")
    ra, rb = sfx_role(a), sfx_role(b)
    return not (ra and rb and ra != rb)


def sfx_clash(a, b):
    """Why two sounds closer than SFX_MIN_SPACING_S read as one muddy flam
    (see sfx_muddy_pair), worded from what is actually known about them."""
    if a.get("storage_key") and a.get("storage_key") == b.get("storage_key"):
        return "fire the same sound file twice"
    ra, rb = sfx_role(a), sfx_role(b)
    if ra and ra == rb:
        return f"play the same role ({ra})"
    return ("have no distinguishable role (name each sound's role in its "
            "purpose, e.g. whoosh vs impact)")


def sfx_events(sfx):
    """Sound EVENTS in program order: a motion graphic's owned cue stack, or
    a layered stack (different roles / one instant), counts once.

    A layered stack is bounded to ONE instant: every layer lands within
    SFX_MIN_SPACING_S of the stack's FIRST sound and clashes with none of
    its layers. Chaining from the last sound instead let a carpet of
    alternating whoosh/impact cues every 0.3s count as a single event."""
    events = []
    for item in sorted(sfx or [], key=sfx_time):
        if events:
            event = events[-1]
            owner = sfx_owner(item)
            same_owner = owner and any(sfx_owner(x) == owner for x in event)
            loose = [x for x in event if not (owner and sfx_owner(x) == owner)]
            layered = (bool(loose)
                       and sfx_time(item) - sfx_time(loose[0])
                       < SFX_MIN_SPACING_S
                       and not any(sfx_muddy_pair(x, item) for x in loose))
            if same_owner or layered:
                event.append(item)
                continue
        events.append([item])
    return events


def density_limits(fmt):
    """The device-density limits for this format (see REEL_* above)."""
    reel = fmt.get("reel")
    if reel is None:
        reel = bool((fmt.get("vertical") or fmt.get("square"))
                    and 0 < _num(fmt.get("duration")) <= REEL_MAX_S)
    if reel:
        return {"profile": "reel", "zoom_per_s": REEL_ZOOM_PER_S,
                "zoom_min_spacing_s": REEL_ZOOM_MIN_SPACING_S,
                "zoom_min_start_s": REEL_ZOOM_MIN_START_S,
                "sfx_per_s": REEL_SFX_PER_S,
                "device_min_spacing_s": REEL_DEVICE_MIN_SPACING_S,
                "max_simultaneous": REEL_MAX_SIMULTANEOUS_DEVICES}
    return {"profile": "default", "zoom_per_s": ZOOM_PER_S,
            "zoom_min_spacing_s": ZOOM_MIN_SPACING_S,
            "zoom_min_start_s": ZOOM_MIN_START_S,
            "sfx_per_s": SFX_PER_S,
            "device_min_spacing_s": DEVICE_MIN_SPACING_S,
            "max_simultaneous": MAX_SIMULTANEOUS_DEVICES}


def _motion_moments(edl, out_dur):
    """EDL.motion items that are MOMENTS (a title, card, counter, sticker),
    not a whole-programme layer such as a browser caption track or texture."""
    out = []
    for item in edl.get("motion") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("template") or "").startswith("caption"):
            continue
        start, end = _num(item.get("start")), _num(item.get("end"))
        if out_dur > 0 and end - start >= 0.8 * out_dur:
            continue
        out.append(item)
    return out


def _separate_authored_text(a, b, duration, play_res):
    """Explicit, non-intersecting labels can form one designed composition.

    Count alone cannot judge a diagram, metric or two-colour lockup. Reuse
    the renderer's text bounds for pinned text, including its translation
    and scale envelope while both are visible. Rotation needs rendered review.
    A phrase settling by a few pixels is not a pile of competing title cards.
    """
    if not all(t.get("x") is not None and t.get("y") is not None for t in (a, b)):
        return False
    from graphics import _compile_item, _overlaps
    from schemas import anim_bounds, anim_value
    overlap_start = max(_num(a.get("start")), _num(b.get("start")))
    overlap_end = min(_num(a.get("end")), _num(b.get("end")))
    if overlap_end <= overlap_start:
        return True
    bounds = []
    for t in (a, b):
        motion = t.get("motion") or {}
        if motion.get("rotation"):
            return False
        layout = dict(t)
        def window_bounds(curve):
            if not isinstance(curve, list):
                return anim_bounds(curve)
            start = overlap_start - _num(t.get("start"))
            end = overlap_end - _num(t.get("start"))
            times = [start, end] + [
                _num(k.get("t")) for k in curve
                if start < _num(k.get("t")) < end]
            values = [anim_value(curve, at) for at in times]
            return min(values), max(values)
        if motion.get("scale"):
            layout["size_scale"] = _num(t.get("size_scale"), 1) * max(
                0.05, window_bounds(motion["scale"])[1])
        box = _compile_item(layout, duration, play_res,
                            size_scale_bounds=(0.02, 12.0) if motion
                            else (0.4, 3.0))
        if not box:
            return False
        for axis, low_edge, high_edge, dimension in (
                ("x", "left", "right", play_res[0]),
                ("y", "top", "bottom", play_res[1])):
            if motion.get(axis) is not None:
                lo, hi = window_bounds(motion[axis])
                base = box[f"base_{axis}_frac"]
                box[low_edge] += (lo-base)*dimension - 1
                box[high_edge] += (hi-base)*dimension + 1
        bounds.append(box)
    return bool(all(bounds) and not _overlaps(*bounds))


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
            "reel": bool(short_form and 0 < out_duration <= REEL_MAX_S),
            "kind": kind, "n_words": n_words, "n_shots": len(shots),
            "has_music": has_music, "duration": out_duration}


def _speech_starts_at(index, tl):
    """Programme second of the first surviving spoken word, or None."""
    # kept_words returns {'w','t0','t1'} already mapped to PROGRAMME time, so
    # the first entry's t0 is the second the viewer first hears a word.
    words = tl.kept_words(index.get("words") or []) if tl else []
    return float(words[0]["t0"]) if words else None


def _shot_id_at(shots, t):
    for sh in shots:
        try:
            if float(sh["start"]) - 1e-6 <= t < float(sh["end"]) + 1e-6:
                return sh.get("id", sh["start"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _crop_room(edl, index):
    """Zoom strength left before the source passes _ZOOM_UPSCALE_MAX under
    this framing, or None when the source size is unknown."""
    video = (index or {}).get("video") or {}
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    try:
        import renderer
        sw, sh = float(video.get("width") or 0), float(video.get("height")
                                                      or 0)
        if sw <= 0 or sh <= 0:
            return None
        W, H = renderer.frame_dims(int(sw), int(sh),
                                   str((frame or {}).get("ratio") or "source"),
                                   delivery=True)
        if (frame or {}).get("picture"):
            _x, _y, W, H = renderer.picture_pixels(W, H, frame["picture"])
    except Exception:
        return None
    mode = (frame or {}).get("mode") or "crop"
    base = (min(W / sw, H / sh) if mode in ("pad", "pad_blur")
            else max(W / sw, H / sh))
    return _ZOOM_UPSCALE_MAX / base - 1.0


def uncovered_jump_cuts(edl, index, tl, fps=None):
    """Jump cuts the picture leaves bare, in programme order:
    [{"t", "before", "after", "fix"}] (before/after = (zoom, cx, cy)).

    A jump cut is a keep join that skips source time with no insert between
    and — when the index has shots — the same shot on both sides (a camera
    change is a real cut, not a jump). It is COVERED when the camera framing
    across it (renderer.camera_zooms: the zooms as rendered, held through
    cuts) steps the scale by JUMP_CUT_MIN_SCALE or moves the viewport by
    JUMP_CUT_MIN_SHIFT of the frame, when the crop's aim or mode changes
    (focus_track), when a transition fires on it, or when a full-frame
    overlay hides it. `fix` is one line of what to do, written so taking
    the fixes in order alternates tight and wide."""
    segs = list(getattr(tl, "segs", None) or [])
    if len(segs) < 2:
        return []
    import renderer
    video = (index or {}).get("video") or {}
    try:
        fps = float(fps or video.get("fps") or 30.0)
    except (TypeError, ValueError):
        fps = 30.0
    out_dur = float(tl.out_duration)
    zooms = renderer.camera_zooms(edl, index, tl, fps)
    shots = (index or {}).get("shots") or []
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    track = [sp for sp in (frame or {}).get("focus_track") or []
             if isinstance(sp, dict)]
    base_aim = ((frame or {}).get("focus_x"), (frame or {}).get("focus_y"),
                (frame or {}).get("mode") or "crop")
    fx = edl.get("effects") or {}
    junctions = set()
    if fx.get("transition"):
        try:
            junctions = transition_junctions(edl, index)
        except Exception:
            junctions = set()
    covers = []
    for ov in edl.get("overlays") or []:
        if ov.get("fit") == "cover" or ov.get("screen"):
            a = _num(ov.get("start"))
            covers.append((a, a + _num(ov.get("duration_s"))))
    try:
        cuts = renderer.camera_cuts(edl, index, tl, fps)
    except Exception:
        cuts = []
    room = _crop_room(edl, index)
    dt = 0.5 / fps
    # Junction k sits between render blocks k and k+1; an insert at a keep
    # boundary is a block of its own, so walk the blocks to number them.
    block_of_seg, ins_j, pre = [], 0, 0.0
    ins_at = [at for at, _d in getattr(tl, "ins", []) or []]
    for i, L in enumerate(tl.seg_out_len):
        while ins_j < len(ins_at) and ins_at[ins_j] <= pre + 1e-6:
            ins_j += 1
        block_of_seg.append(i + ins_j)
        pre += L

    def vcentre(z, c):
        return (1.0 - 1.0 / z) * c + 0.5 / z

    def aim(t):
        for sp in track:
            try:
                if float(sp.get("t0", 0)) <= t <= float(sp.get("t1", 0)):
                    return (sp["x"] if sp.get("x") is not None
                            else base_aim[0],
                            sp["y"] if sp.get("y") is not None
                            else base_aim[1],
                            sp.get("mode") or base_aim[2])
            except (TypeError, ValueError):
                continue
        return base_aim

    def aim_moved(p, q):
        if p[2] != q[2]:
            return True
        return any(abs(_num(x, 0.5) - _num(y, 0.5)) >= JUMP_CUT_MIN_AIM
                   for x, y in zip(p[:2], q[:2]))

    found, released_at = [], None
    for i in range(len(segs) - 1):
        e0, s1 = float(segs[i][1]), float(segs[i + 1][0])
        end = tl.offsets[i] + tl.seg_out_len[i]
        c = tl.offsets[i + 1]
        if c - end > 1e-6 or s1 - e0 <= 1e-3:
            continue                    # an insert between, or one run
        if shots:
            sa = _shot_id_at(shots, e0 - 0.04)
            sb = _shot_id_at(shots, s1 + 0.04)
            if sa is None or sb is None or sa != sb:
                continue                # a camera change, not a jump
        if block_of_seg[i] in junctions:
            continue
        if any(a <= c - dt and b >= c + dt for a, b in covers):
            continue
        if aim_moved(aim(e0 - 1e-3), aim(s1 + 1e-3)):
            continue
        zb = renderer.zoom_state_at(zooms, c - dt, out_dur)
        za = renderer.zoom_state_at(zooms, c + dt, out_dur)
        scale = max(za[0], zb[0]) / max(1e-6, min(za[0], zb[0])) - 1.0
        shift = max(abs(vcentre(za[0], za[1]) - vcentre(zb[0], zb[1])),
                    abs(vcentre(za[0], za[2]) - vcentre(zb[0], zb[2])))
        if scale >= JUMP_CUT_MIN_SCALE - 1e-6 or \
                shift >= JUMP_CUT_MIN_SHIFT - 1e-6:
            continue
        if released_at is not None and abs(released_at - c) < 1e-3:
            # The previous fix's punch releases back to the wide here.
            released_at = None
            found.append({"t": round(c, 2), "before": zb, "after": za,
                          "fix": "covered by the punch above releasing "
                                 "to the wide here"})
            continue
        released_at = None
        nxt = next((x for x in cuts if x > c + 0.2), None)
        nxt = min(out_dur, nxt if nxt is not None else c + 3.0)
        across = [z for z in zooms
                  if _num(z.get("start")) < c - dt and _num(z.get("end"))
                  > c + dt and (z.get("mode") or "punch") != "shake"]
        edge = [z.get("id") for z in zooms
                if abs(_num(z.get("start")) - c) <= 2 * dt
                or abs(_num(z.get("end")) - c) <= 2 * dt]
        if room is not None and room < JUMP_CUT_MIN_SCALE:
            fix = ("the source is already enlarged to the zoom limit, so no "
                   "punch can step it — re-aim the crop on the cut "
                   "(focus_track), land a graphic or caption-block change "
                   "on it, or trim the jump")
        elif max(za[0], zb[0]) < 1.02:
            st = 0.12 if room is None else round(min(0.12, room), 2)
            fix = (f"punch in to {1 + st:.2f}x until {nxt:.2f}: add_zoom "
                   f"start={c:.2f} end={nxt:.2f} strength={st:g} "
                   f"mode='punch' ramp_s=0, aimed at the face")
            released_at = nxt
        elif across and not edge:
            z = across[0]
            fix = (f"zoom {z.get('id')} ({z.get('mode') or 'punch'}) runs "
                   f"through it at {zb[0]:.2f}x — end it on the cut "
                   f"(end={c:.2f}) so the cut steps back to the wide")
        else:
            who = f" (zoom {', '.join(map(str, edge))})" if edge else ""
            fix = (f"the framing steps only {zb[0]:.2f}x → {za[0]:.2f}x "
                   f"({scale * 100:.0f}%){who} — make that step ≥8%, or "
                   "release to the wide on the cut")
        found.append({"t": round(c, 2), "before": zb, "after": za,
                      "fix": fix})
    return found


def jump_cut_line(bare):
    """The advisory sentence for uncovered_jump_cuts' rows, or ''."""
    if not bare:
        return ""
    groups = []                         # [[times], fix] in order
    for r in bare:
        if groups and groups[-1][1] == r["fix"]:
            groups[-1][0].append(r["t"])
        else:
            groups.append([[r["t"]], r["fix"]])
    head = groups[:JUMP_CUT_LIST]
    more = sum(len(g[0]) for g in groups[JUMP_CUT_LIST:])
    return (f"{len(bare)} jump cut{'s' if len(bare) != 1 else ''} inside one "
            "take keep the same framing on both sides (no ≥8% scale step or "
            "crop move), so the head visibly pops: "
            + "; ".join(", ".join(f"{t:g}s" for t in ts) + f" — {fix}"
                        for ts, fix in head)
            + (f"; (+{more} more)" if more else "")
            + ". Alternate tight and wide across consecutive jump cuts; a 5% "
              "punch reads as no change.")


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
    limits = density_limits(fmt)
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
    # Spoken-hook dead air is a talking-head / narrated-piece rule.
    # A gameplay VOD with three leftover "Thanks for watching!" words is
    # not a reel that forgot its hook — flagging it blocks export on
    # music-led montages (Aug 12, project 727).
    speech_led = fmt["kind"] in ("talking head", "narrated piece")
    if speech_at is not None and speech_at > HOOK_DEAD_AIR_S \
            and fmt["short_form"] and speech_led:
        add(f"the first words land at {speech_at:.1f}s — everything before "
            "that is dead air at the most expensive moment of the video. "
            "Cut into the strongest line, or move it to the front.")

    zooms = sorted((fx.get("zooms") or []),
                   key=lambda z: _num(z.get("start")))
    if zooms:
        first = _num(zooms[0].get("start"))
        # On a reel a punched-in or pushing hook at 0s IS the pattern
        # interrupt the references open on (limit 0); long-form keeps 1.2s.
        if first < limits["zoom_min_start_s"]:
            add(f"the first zoom fires at {first:.1f}s, before the viewer has "
                "read the shot — a push that early reads as a camera bump, "
                "not emphasis. Move it past 1.5s or drop it.")

    # ── zoom rhythm ──────────────────────────────────────────────────────
    if out_dur > 0 and len(zooms) > max(2, int(out_dur / limits["zoom_per_s"])):
        add(f"{len(zooms)} zooms across {out_dur:.0f}s is roughly one every "
            f"{out_dur / max(1, len(zooms)):.1f}s — when the frame never "
            "settles, no single move reads as emphasis. Keep the moves that "
            "land on sentence turns, jump cuts and payoffs, and let the "
            "frame hold between them.")
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
             < limits["zoom_min_spacing_s"]
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

    # ── jump cuts the framing leaves bare ────────────────────────────────
    # The cut-hygiene judges (Oct 2026): every jump cut inside one take wants
    # a framing change of at least ~8% (or a crop move) across it, or the
    # head pops in place. Named one by one, each with its fix.
    if not any(k in ask for k in ("no zoom", "without zoom", "no punch",
                                  "no camera move", "keep the jump cut")):
        try:
            bare = uncovered_jump_cuts(edl, index, tl)
        except Exception:
            bare = []
        if bare:
            add(jump_cut_line(bare))

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
    sfx = sorted((edl.get("sfx") or []), key=sfx_time)
    # Whether sound design serves the cut is an editorial judgment, not a
    # keyword permission check. Mechanical density and collision findings
    # below remain useful evidence regardless of how the request was phrased.
    # Density counts sound EVENTS: a motion graphic's owned cue stack
    # ('mg_<id>_sfxN') and a layered stack (whoosh pre-rolled into an
    # impact, a sub under a hit) are one designed moment each.
    sound_events = sfx_events(sfx)
    if out_dur > 0 and len(sound_events) > max(
            3, int(out_dur / limits["sfx_per_s"])):
        add(f"{len(sound_events)} sound events in {out_dur:.0f}s — accents "
            "stop being accents when they never stop. Tie each sound to an "
            "authored visual event (a graphic landing, a punch, a cut, a "
            "reveal) and drop the ones that are not.")
    stacked = [(a, b) for a, b in zip(sfx, sfx[1:]) if sfx_muddy_pair(a, b)]
    if stacked:
        a, b = stacked[0]
        add(f"two sound effects {sfx_time(b) - sfx_time(a):.2f}s "
            f"apart at {sfx_time(a):.1f}s {sfx_clash(a, b)} — they "
            "land as one flammed, muddy hit. Keep one, or layer DIFFERENT "
            "roles (a whoosh whose peak lands on an impact) on the same beat.")

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
        or (isinstance(caps, list) and bool(caps)) \
        or any(str(m.get("template") or "").startswith("caption")
               for m in edl.get("motion") or [] if isinstance(m, dict))
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
        # 'lyric' (round 99b) is the second sanctioned centre-holder: the
        # mixed-face lyric edit deliberately owns the middle of the frame —
        # that placement IS the look, exactly like spotlight's single word.
        multi = (cstyle.get("preset") or "") not in ("spotlight", "lyric") \
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
    from captions import effective_caption_mutes
    mutes = effective_caption_mutes(edl)
    ratio_w, ratio_h = _ratio_wh(edl, src_w, src_h)
    play_res = (1080, round(1080 * ratio_h / ratio_w)) \
        if ratio_w and ratio_h else (1920, 1080)
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
            if _separate_authored_text(a, b, out_dur, play_res):
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
            "need a composition check in the rendered frames. Keep an "
            "intentional readable hierarchy; move or simplify text only "
            "where it collides or competes. Simultaneous labels in one "
            "diagram are not inherently a defect.")
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
            # Explicit non-muting text is an authored headline/label beside
            # dialogue. Temporal coexistence cannot establish a collision;
            # actual caption/text geometry remains part of visual review.
            if t.get("mute_captions") is False:
                continue
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

    # ── too LITTLE design: flat, static, silent delivery (reels) ─────────
    # Every rule above flags too much. The owner's own shorts failed the
    # other way: a clean crop with captions, no camera move, no graphic and
    # a dry mix (audited at 2.6/10 against 7.5 for the reference reels).
    # Advisory like the rest; an explicit ask for static/silent wins.
    if fmt["reel"] and speech_led and out_dur >= 20:
        aims = {(sp.get("x"), sp.get("y"))
                for sp in frame.get("focus_track") or []
                if isinstance(sp, dict)}
        picture_moves = bool(
            zooms or fx.get("frame_shifts") or len(aims) > 1 or trans
            or edl.get("inserts") or edl.get("overlays") or texts
            or edl.get("vectors") or fx.get("picture_cards")
            or _motion_moments(edl, out_dur)
            or any(s.get("start") is not None for s in stylize))
        flat = not picture_moves and not any(w in ask for w in (
            "static", "no zoom", "no motion", "no graphic", "minimal",
            "keep it simple"))
        # A dry mix is legitimate: music is never the agent's choice (the
        # user supplies or asks for it) and zero SFX is fine when nothing on
        # screen earns one. Only a frame that never moves is flagged.
        if flat:
            add(f"static picture: {out_dur:.0f}s with no camera move, "
                "cutaway or graphic — the frame never moves, so nothing "
                "marks the sentence turns or the payoff. Add eased pushes or "
                "a reframe on the turns and one hook interrupt in the first "
                "two seconds.")

    # ── the RATE of everything, together ─────────────────────────────────
    # Every rule above bounds ONE category, and a viewer does not experience
    # categories. The edit this check exists for passed or barely tripped each
    # of them separately — 9 transitions, 3 zooms, 3 stylize windows, 5 sfx —
    # and landed as a device every 1.5 seconds. That is the video the user
    # described as "putting a sound and a transition every second".
    # Owned motion-graphic cue stacks and layered stacks count once (see
    # sfx_events) and a motion graphic counts as one device together with
    # its own sounds: one designed moment, one device.
    motion_moments = _motion_moments(edl, out_dur)
    if out_dur > 4:
        devices = (len(zooms)
                   + len([e for e in sound_events
                          if not all(sfx_owner(x) for x in e)])
                   + len([s for s in stylize if s.get("start") is not None])
                   + len(motion_moments))
        if trans:
            try:
                devices += len(transition_junctions(
                    edl, index,
                    n_blocks=len(edl.get("keep") or [])
                    + len(edl.get("inserts") or [])))
            except Exception:
                pass
        if devices >= 6 and \
                out_dur / devices < limits["device_min_spacing_s"]:
            add(f"{devices} attention-grabbing devices across {out_dur:.0f}s "
                f"(transitions, zooms, windowed stylize passes, motion "
                f"graphics and sound events together) — one every "
                f"{out_dur / devices:.1f}s. Each kind may look reasonable on "
                "its own; at this rate the picture never settles long enough "
                "for a peak to read as one. Group devices into fewer, "
                "stronger hits on the beats that carry the piece and let the "
                "frame breathe between them.")

    # ── the PEAK, not the average ────────────────────────────────────────
    # The rate check above is blind to simultaneity by construction, and a
    # list of requests ("zoom + flash + shake + glow + speed ramp + colour")
    # is exactly the input that invites one moment to carry all of them. See
    # MAX_SIMULTANEOUS_DEVICES. Windowed devices only — a global pass is a
    # look, not a moment — and no `ask` suppression: the user asked for the
    # devices, never for them to land on the same half-second. A reel allows
    # one declared composite hit (punch + flash + shake under a camera move)
    # up to REEL_MAX_SIMULTANEOUS_DEVICES.
    if out_dur > 4:
        pile = _densest_moment(
            [(_num(z.get("start")), _num(z.get("end")),
              f"zoom {z.get('id')}") for z in zooms]
            + [(_num(s.get("start")), _num(s.get("end")),
                f"{s.get('kind') or 'a'} stylize ({s.get('id')})")
               for s in stylize if s.get("start") is not None])
        if pile and len(pile[2]) > limits["max_simultaneous"]:
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
    """One compact ADVISORY string for a tool result, or '' when clean."""
    if not findings:
        return ""
    head = findings[:limit]
    more = len(findings) - len(head)
    line = (" TASTE NOTES (advisory: keep if intentional — craft "
            "observations, not defects; they never block completion or "
            "export): ") + "; ".join(head)
    if more:
        line += f"; (+{more} more)"
    return line + (" Change one only if you agree it hurts THIS edit. "
                   "Re-render only if you change something.")
