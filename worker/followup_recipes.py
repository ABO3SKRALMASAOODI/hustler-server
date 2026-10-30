"""Follow-up request recipes for the new Claude lanes (WAVE_C, 31 Oct 2026).

The in-product editor on the trial and new-subscriber lanes (Haiku) made
weak edits on the short, vague follow-ups real users type after the First
cut (REAL_USER_BRUTE, production first prompts):

  * "cut the boring parts" came back LONGER (screen +0.5 s, HEVC +1.5 s,
    talk 50.5 -> 51.3 s): the model restored time while "fixing" a finding;
  * edits nobody asked for rode along (a graphic moved behind the speaker,
    a zoom removed, the framing re-aimed on "cut the boring parts");
  * a captions / title / pacing ask spent 8-12 model calls (reads, renders,
    justifying review findings about other parts of the edit), which is
    what ends a $0.12 trial after three requests on a 10-minute talk.

This module names the request (classify), gives the model ONE recipe for it
(note: the exact chain, its tool defaults and its done bar, computed from
the current edit), refuses writes outside what was asked (refusal before a
tool runs, check_write on the EDL a write would save), refuses a write that
moves the length the wrong way, and appends a REQUEST CHECK to every
preview render: the before/after EDL compared against the request, so the
model verifies the edit against the ask before it replies.

Only a short, single-purpose follow-up classifies (a brief, a URL, a
timestamp or a long message gets nothing), and only the Claude trial and
new-subscriber lanes call it (agent_loop gates on ctx.claude_trial /
ctx.claude_paid): grandfathered subscribers never reach this code.
"""
import copy
import json
import re

# ── request interpretation ────────────────────────────────────────────────

_WORDS_MAX = 16
_URL = re.compile(r"https?://|www\.", re.I)
_TIMESTAMP = re.compile(r"\b\d+\s*:\s*\d\d\b|\bat\s+\d|\b\d+(?:\.\d+)?\s*(?:s|sec|secs|seconds?|minutes?|mins?)\b", re.I)
_NEGATED = re.compile(r"\b(?:don'?t|do\s+not|never|without|keep\s+the\s+same)\b", re.I)

_CAPTIONS = r"(?:captions?|subtitles?|subs)"
_TITLE = r"(?:title|hook(?:\s+title)?|headline|heading|opening\s+text)"
_BIGGER = r"(?:bigger|larger|huge|large|big|increase|more\s+visible|easier\s+to\s+read|readable|too\s+small|too\s+tiny)"
_SMALLER = r"(?:smaller|tinier|less\s+big|too\s+big|reduce|shrink|decrease)"

PATTERNS = (
    ("captions_bigger", re.compile(
        rf"\b{_CAPTIONS}\b.*\b{_BIGGER}\b|\b{_BIGGER}\s+{_CAPTIONS}\b", re.I)),
    ("captions_smaller", re.compile(
        rf"\b{_CAPTIONS}\b.*\b{_SMALLER}\b|\b{_SMALLER}\s+(?:the\s+)?{_CAPTIONS}\b", re.I)),
    ("title_bigger", re.compile(rf"\b{_TITLE}\b.*\b{_BIGGER}\b", re.I)),
    ("title_smaller", re.compile(rf"\b{_TITLE}\b.*\b{_SMALLER}\b", re.I)),
    ("title_change", re.compile(
        rf"\b(?:change|rename|reword|rewrite|replace|edit|fix|swap)\s+(?:the\s+|my\s+|its\s+|that\s+)?"
        rf"(?:opening\s+)?{_TITLE}\b|\b{_TITLE}\s+(?:to|should|that\s+says|saying|reads?|says)\b"
        rf"|\b{_TITLE}\b[^\"“”']*[\"“][^\"”]+[\"”]", re.I)),
    ("title_add", re.compile(
        rf"\b(?:add|put|give\s+(?:it|me)|i\s+want|need|include|place)\b.*\b(?:a\s+|an\s+|the\s+)?"
        rf"(?:opening\s+|big\s+|bold\s+)?{_TITLE}\b", re.I)),
    ("boring", re.compile(
        r"\b(?:boring|dull|slow|draggy|unnecessary|useless)\s+(?:parts?|bits?|sections?|stuff|moments?|bits)\b"
        r"|\bcut\s+(?:out\s+)?(?:the\s+)?(?:fluff|filler\s+content)\b|\bkeep\s+(?:only\s+)?the\s+(?:good|best)\s+(?:parts?|bits?)\b"
        r"|\bit'?s\s+(?:too\s+)?boring\b", re.I)),
    ("ums", re.compile(
        r"\b(?:ums?|umm+|uhs?|uhm+|erm+|ahs?|filler\s+words?|fillers?|stutters?|stammers?)\b", re.I)),
    ("silences", re.compile(
        r"\b(?:cut|remove|trim|delete|get\s+rid\s+of|take\s+out|tighten)\b.*\b(?:silences?|pauses?|dead\s+air|gaps?|quiet\s+parts?)\b"
        r"|\b(?:silences?|pauses?|dead\s+air)\b.*\b(?:cut|removed?|trimmed?|out)\b", re.I)),
    ("punchier", re.compile(
        r"\bpunch(?:ier|y)\b|\bsnappier\b|\bmore\s+(?:energy|energetic|dynamic|engaging|exciting|fast[\s-]?paced)\b"
        r"|\b(?:faster|quicker|better)\s+pac(?:e|ing)\b|\bpick\s+up\s+the\s+pace\b|\bspeed\s+up\s+the\s+pacing\b"
        r"|\bit\s+drags\b|\btighter\b", re.I)),
    ("shorter", re.compile(
        r"\bshorter\b|\bshorten\b|\btrim\s+it(?:\s+down)?\b|\bcut\s+it\s+down\b|\btoo\s+long\b", re.I)),
    ("longer", re.compile(r"\blonger\b|\blengthen\b|\bextend\s+(?:it|the\s+video)\b|\btoo\s+short\b", re.I)),
    ("broll", re.compile(
        r"\bb[\s-]?roll\b|\bcut[\s-]?aways?\b|\bstock\s+(?:footage|clips?|images?|videos?|photos?)\b"
        r"|\b(?:add|put|show)\s+(?:some\s+)?(?:visuals|images|pictures|photos|clips)\b", re.I)),
    ("zooms", re.compile(
        r"\bzoom(?:s|ing|[\s-]?ins?)?\b|\bpunch[\s-]?ins?\b|\bcamera\s+(?:moves?|movement)\b", re.I)),
)

LABELS = {
    "boring": "cut the boring parts",
    "shorter": "make it shorter",
    "longer": "make it longer",
    "punchier": "make it punchier",
    "ums": "remove the ums and filler words",
    "silences": "cut the silences",
    "captions_bigger": "make the captions bigger",
    "captions_smaller": "make the captions smaller",
    "title_add": "add a title",
    "title_change": "change the title",
    "title_bigger": "make the title bigger",
    "title_smaller": "make the title smaller",
    "broll": "add B-roll",
    "zooms": "add zooms",
}

# what each request may change (lanes below), and which way the length moves
ALLOWED = {
    "boring": {"cuts"}, "shorter": {"cuts"}, "longer": {"cuts"},
    "punchier": {"cuts", "zooms"}, "ums": {"cuts"}, "silences": {"cuts"},
    "captions_bigger": {"captions"}, "captions_smaller": {"captions"},
    "title_add": {"title"}, "title_change": {"title"},
    "title_bigger": {"title"}, "title_smaller": {"title"},
    "broll": {"broll"}, "zooms": {"zooms"},
}
DIRECTION = {"boring": "shorter", "shorter": "shorter", "longer": "longer",
             "punchier": "not_longer", "ums": "not_longer",
             "silences": "not_longer"}
# A request is DONE only past this bar (REQUEST CHECK): the share of the
# current length a "boring"/"shorter" cut removes at least (a short cut
# under SHORT_FLOOR_S may stay as it is, said so).
CUT_SHARE = {"boring": 0.10, "shorter": 0.15}
SHORT_FLOOR_S = 12.0

LANE_LABELS = {
    "cuts": "cut (what is kept and its length)",
    "captions": "captions",
    "title": "opening title",
    "graphics": "graphics and word slams",
    "zooms": "zooms and framing steps",
    "frame": "framing and crop",
    "broll": "B-roll and inserts",
    "grade": "look and colour grade",
    "audio": "music, sound and loudness",
    "transitions": "transitions and fades",
}

# tools a recipe may run whatever their side effects (their internal remaps
# move graphics, captions mutes and B-roll with the cut, by design)
EXEMPT_TOOLS = {
    "cuts": {"cut_silences", "remove_filler_words", "keep_segments",
             "cut_range", "cut_output_range", "restore_range"},
    "broll": {"source_broll", "add_stock_media", "insert_media"},
    "zooms": {"add_zoom", "add_zoom_path", "punch_in_on_emphasis"},
    "captions": {"set_caption_style"},
}

# the lane a tool changes, refused before it runs when not asked for
TOOL_LANES = {
    "keep_segments": "cuts", "cut_range": "cuts", "cut_output_range": "cuts",
    "restore_range": "cuts", "cut_silences": "cuts",
    "remove_filler_words": "cuts", "set_speed": "cuts", "remove_speed": "cuts",
    "set_speed_ramp": "cuts",
    "add_captions": "captions", "set_caption_style": "captions",
    "set_caption_mutes": "captions",
    "set_frame": "frame", "auto_reframe": "frame",
    "add_aspect_shift": "frame", "remove_aspect_shift": "frame",
    "add_zoom": "zooms", "remove_zoom": "zooms", "add_zoom_path": "zooms",
    "remove_zoom_path": "zooms", "punch_in_on_emphasis": "zooms",
    "source_broll": "broll", "remove_broll": "broll",
    "add_stock_media": "broll", "insert_media": "broll",
    "remove_insert": "broll", "set_insert_window": "broll",
    "add_overlay": "broll", "remove_overlay": "broll", "move_overlay": "broll",
    "set_color_grade": "grade", "apply_look": "grade",
    "set_grade_custom": "grade", "set_grade_window": "grade",
    "add_stylize": "grade", "remove_stylize": "grade",
    "add_music": "audio", "add_library_music": "audio",
    "remove_music": "audio", "swap_music": "audio", "set_music_fit": "audio",
    "add_sfx": "audio", "remove_sfx": "audio", "move_sfx": "audio",
    "set_volume": "audio", "set_audio_gain": "audio",
    "set_master_loudness": "audio", "add_voiceover": "audio",
    "remove_voiceover": "audio",
    "set_transitions": "transitions", "set_fades": "transitions",
    "add_text": "graphics", "remove_text": "graphics",
    "add_kinetic_text": "graphics", "add_word_slam": "graphics",
    "set_picture_card": "graphics", "remove_picture_card": "graphics",
    "set_editorial_graphic": "graphics", "remove_editorial_graphic": "graphics",
    "add_vector_graphic": "graphics", "set_vector_graphic": "graphics",
    "remove_vector_graphic": "graphics",
}
_MOTION_TOOLS = {"add_motion_graphic", "set_motion_graphic",
                 "remove_motion_graphic"}
_NEVER_TOOLS = {"reset_edit"}


def classify(text):
    """The recipes a short follow-up asks for, in a stable order ([] when
    the message is a brief, names a time, carries a URL, negates, or asks
    for more than three things)."""
    t = " ".join(str(text or "").split())
    if not t or len(t.split()) > _WORDS_MAX or _URL.search(t):
        return []
    if _TIMESTAMP.search(t) or _NEGATED.search(t):
        return []
    keys = [k for k, rx in PATTERNS if rx.search(t)]
    if re.search(r"\btoo\s+(?:big|large|huge)\b", t, re.I):
        keys = [k for k in keys if not k.endswith("_bigger")]
    if re.search(r"\btoo\s+(?:small|tiny)\b", t, re.I):
        keys = [k for k in keys if not k.endswith("_smaller")]
    # one reading per subject: a title size ask is not also a title change,
    # a caption size ask is not a title, "punchier" covers its own pauses
    if "title_bigger" in keys or "title_smaller" in keys:
        keys = [k for k in keys if k not in ("title_change", "title_add")]
    if "title_change" in keys:
        keys = [k for k in keys if k != "title_add"]
    if any(k.startswith("captions_") for k in keys):
        keys = [k for k in keys if not k.startswith("title_")
                or re.search(rf"\b{_TITLE}\b", re.sub(rf"\b{_CAPTIONS}\b", "", t, flags=re.I), re.I)]
    if "captions_bigger" in keys and "captions_smaller" in keys:
        return []
    if "longer" in keys and any(DIRECTION.get(k) in ("shorter", "not_longer")
                                for k in keys if k != "longer"):
        return []
    if "boring" in keys and "shorter" in keys:
        keys.remove("shorter")
    if "punchier" in keys and "zooms" in keys and not re.search(r"\bzoom", t, re.I):
        keys.remove("zooms")
    return keys if 0 < len(keys) <= 3 else []


def label(keys):
    return " and ".join(LABELS[k] for k in keys)


def allowed(keys):
    out = set()
    for k in keys:
        out |= ALLOWED.get(k, set())
    return out


def direction(keys):
    dirs = {DIRECTION[k] for k in keys if k in DIRECTION}
    if "longer" in dirs:
        return "longer"
    if "shorter" in dirs:
        return "shorter"
    return "not_longer" if dirs else None


# ── the EDL, lane by lane ─────────────────────────────────────────────────

def program_s(edl):
    try:
        from timeline import Timeline
        return float(Timeline(edl.get("keep") or [], edl.get("inserts"),
                              edl.get("speed")).out_duration)
    except Exception:  # noqa: BLE001 — a plain sum is the fallback
        total = 0.0
        for span in (edl or {}).get("keep") or []:
            try:
                total += max(0.0, float(span[1]) - float(span[0]))
            except (TypeError, ValueError, IndexError):
                continue
        return total


def _is_title(item, first_id=None):
    tpl = str(item.get("template") or "").lower()
    if "title" in tpl or "hook" in tpl or "headline" in tpl:
        return True
    return first_id is not None and str(item.get("id")) == str(first_id)


def _first_title_id(edl):
    motion = [m for m in (edl or {}).get("motion") or [] if isinstance(m, dict)]
    for m in motion:
        if _is_title(m):
            return m.get("id")
    return None


def title_item(edl):
    tid = _first_title_id(edl)
    for m in (edl or {}).get("motion") or []:
        if isinstance(m, dict) and tid is not None and m.get("id") == tid:
            return m
    return None


_TIME_KEYS = {"start", "end", "at", "bind", "t0", "t1", "at_output_s",
              "start_output_s", "follow", "k", "keys", "seed", "moment",
              "target_evidence_ids", "target_measured", "src_t", "offset_s"}


_EMPTY = (None, "", 0, 0.0, False)


def _strip_time(value):
    """The item without its program-time placement, and without keys at an
    empty default: a schema that grows a param (WAVE_A's hook_title
    base_size/band_top/band_bottom = 0) re-saves every title with it, which
    is not a change anybody made."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _TIME_KEYS:
                continue
            v = _strip_time(v)
            if (not isinstance(v, (dict, list)) and v in _EMPTY
                    and not isinstance(v, bool) or v is False) \
                    or v == {} or v == []:
                continue
            out[k] = v
        return out
    if isinstance(value, list):
        return [_strip_time(v) for v in value]
    return value


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _by_id(items):
    out = {}
    for i, it in enumerate(items or []):
        if isinstance(it, dict):
            out[str(it.get("id") or f"#{i}")] = _strip_time(it)
    return out


def lanes(edl):
    """Each lane's design identity, without the program-time placement a cut
    legitimately moves (so a cut that slides a slam is not a graphics
    change; a slam restyled, added or moved behind the speaker is)."""
    edl = edl or {}
    eff = edl.get("effects") or {}
    tid = _first_title_id(edl)
    motion = [m for m in edl.get("motion") or [] if isinstance(m, dict)]
    title = [m for m in motion if tid is not None and m.get("id") == tid]
    graphics = [m for m in motion if not (tid is not None and m.get("id") == tid)]
    frame = edl.get("frame") or {}
    caps = copy.deepcopy(edl.get("captions") or {})
    if isinstance(caps, dict):
        caps = _strip_time({k: caps.get(k) for k in (
            "mode", "style", "enabled", "emphasis_words", "emphasis_mode",
            "text_fixes", "corrections")})
    return {
        "cuts": {"keep": edl.get("keep") or [], "speed": edl.get("speed") or []},
        "captions": caps,
        "title": _by_id(title),
        "graphics": {"motion": _by_id(graphics),
                     "vectors": _by_id(edl.get("vectors")),
                     "texts": _by_id(edl.get("texts")),
                     "cards": _by_id(eff.get("picture_cards"))},
        "zooms": {"zooms": _by_id(eff.get("zooms")),
                  "paths": _by_id(eff.get("zoom_paths")),
                  "shifts": _by_id(eff.get("frame_shifts"))},
        "frame": ({k: frame.get(k) for k in ("mode", "ratio", "focus_x",
                                              "focus_y", "zoom", "picture")}
                  if isinstance(frame, dict) else frame),
        "broll": {"fx": _by_id(edl.get("fx")),
                  "inserts": _by_id(edl.get("inserts")),
                  "overlays": _by_id(edl.get("overlays"))},
        "grade": {"grade": eff.get("grade"), "custom": eff.get("grade_custom"),
                  "stylize": _by_id(eff.get("stylize")),
                  "look": edl.get("look")},
        "audio": {"music": _by_id(edl.get("music")), "sfx": _by_id(edl.get("sfx")),
                  "voiceover": _by_id(edl.get("voiceover")),
                  "volume": edl.get("volume") or [],
                  "master": edl.get("master")},
        "transitions": {"transitions": eff.get("transitions"),
                        "fades": eff.get("fades") or edl.get("fades")},
    }


def _only_removals(before, after):
    """True when `after` is `before` minus whole items (what a cut does to
    a graphic or a B-roll on the span it removed)."""
    if isinstance(before, dict) and isinstance(after, dict):
        if set(after) - set(before):
            return False
        return all(_canon(before[k]) == _canon(after[k])
                   or _only_removals(before[k], after[k])
                   for k in after)
    return False


def changed_lanes(prev, new):
    a, b = lanes(prev), lanes(new)
    timeline_moved = _canon(a["cuts"]) != _canon(b["cuts"])
    out = []
    for lane in a:
        if _canon(a[lane]) == _canon(b[lane]):
            continue
        if timeline_moved and lane != "cuts" and _only_removals(a[lane], b[lane]):
            # an item whose whole span was cut away went with it
            continue
        out.append(lane)
    return out


# ── the turn's state (agent_loop.start) ───────────────────────────────────

def start(ctx, text):
    """The follow-up state for this turn, or None. Sets ctx.followup."""
    keys = classify(text)
    if not keys:
        ctx.followup = None
        return None
    try:
        edl = copy.deepcopy(ctx.latest_edl()["json"])
    except Exception:  # noqa: BLE001 — no state, no recipe
        ctx.followup = None
        return None
    state = {"keys": keys, "label": label(keys), "allowed": allowed(keys),
             "direction": direction(keys), "start_edl": edl,
             "start_s": round(program_s(edl), 2), "refusals": 0}
    ctx.followup = state
    return state


def active(ctx):
    st = getattr(ctx, "followup", None)
    return st if isinstance(st, dict) and st.get("keys") else None


# ── guards ────────────────────────────────────────────────────────────────

def _not_asked(st, what):
    st["refusals"] = int(st.get("refusals") or 0) + 1
    return ("REJECTED (not run): NOT ASKED — the user asked to "
            f"{st['label']}; this changes the {what}, which the request did "
            "not mention, so it stays exactly as it is (a review finding "
            "about it is not this turn's work). Make only the requested "
            "change, render once and reply. Do not retry this.")


def refusal(ctx, name, args):
    """A refusal text for a tool call outside the request, before it runs;
    None when the call may run (the EDL check still applies to its write)."""
    st = active(ctx)
    if not st:
        return None
    if name in _NEVER_TOOLS:
        return _not_asked(st, "whole edit (a reset)")
    lane = TOOL_LANES.get(name)
    args = args if isinstance(args, dict) else {}
    if name in _MOTION_TOOLS:
        try:
            edl = ctx.latest_edl()["json"]
        except Exception:  # noqa: BLE001
            edl = {}
        tid = _first_title_id(edl)
        if name == "add_motion_graphic":
            tpl = str(args.get("template") or "").lower()
            is_title = ("title" in tpl or "hook" in tpl or "headline" in tpl)
            if is_title and tid is not None and "title" in st["allowed"]:
                # one opening title: a second one is a duplicate layer
                return ("REJECTED (not run): the edit already has its opening "
                        f"title ({tid}); change it with set_motion_graphic("
                        f"id='{tid}', params={{...}}) instead of adding a "
                        "second one.")
            lane = "title" if is_title else "graphics"
        else:
            target = str(args.get("id") or "")
            lane = "title" if (tid is not None and target == str(tid)) else "graphics"
    if lane is None or lane in st["allowed"]:
        if lane == "cuts" and name == "restore_range" \
                and st.get("direction") in ("shorter", "not_longer"):
            return ("REJECTED (not run): the user asked to " + st["label"]
                    + "; restoring footage makes the video longer. Cut "
                    "instead (cut_output_range on the weakest sentence, "
                    "cut_silences, remove_filler_words).")
        return None
    return _not_asked(st, LANE_LABELS.get(lane, lane))


def check_write(ctx, prev, new, tool=None):
    """A REJECTED text for an EDL write outside the request or moving the
    length the wrong way; None to let it save."""
    st = active(ctx)
    if not st:
        return None
    version = None
    try:
        version = ctx.latest_edl()["version"]
    except Exception:  # noqa: BLE001
        pass
    head = f"REJECTED (EDL v{version} unchanged): " if version is not None \
        else "REJECTED: "
    before_s, after_s = program_s(prev), program_s(new)
    dirn = st.get("direction")
    if dirn in ("shorter", "not_longer") and after_s > before_s + 0.05:
        st["refusals"] = int(st.get("refusals") or 0) + 1
        return (head + f"this makes the video LONGER ({before_s:.1f} s -> "
                f"{after_s:.1f} s) and the user asked to {st['label']}. "
                "Only remove time on this request: cut_output_range on the "
                "weakest sentence, cut_silences, remove_filler_words.")
    if dirn == "longer" and after_s < before_s - 0.05:
        st["refusals"] = int(st.get("refusals") or 0) + 1
        return (head + f"this makes the video SHORTER ({before_s:.1f} s -> "
                f"{after_s:.1f} s) and the user asked to make it longer. "
                "Bring back the sentences next to the kept ones with "
                "restore_range instead.")
    exempt = set()
    for lane in st["allowed"]:
        exempt |= EXEMPT_TOOLS.get(lane, set())
    if tool in exempt:
        return None
    outside = [ln for ln in changed_lanes(prev, new) if ln not in st["allowed"]]
    if not outside:
        return None
    st["refusals"] = int(st.get("refusals") or 0) + 1
    return (head + "NOT ASKED — the user asked to " + st["label"]
            + "; this write also changes the "
            + ", ".join(LANE_LABELS.get(x, x) for x in outside)
            + ", which the request did not mention. Make only the requested "
              "change and leave everything else exactly as it is.")


# ── the recipe (one system note per turn) ─────────────────────────────────

def _caption_scale(edl):
    style = ((edl or {}).get("captions") or {}).get("style") or {}
    try:
        return float(style.get("size_scale") or 1.0)
    except (TypeError, ValueError):
        return 1.0


def _title_size(edl):
    item = title_item(edl) or {}
    try:
        return float((item.get("params") or {}).get("size") or 1.0)
    except (TypeError, ValueError):
        return 1.0


def _quoted(text):
    m = re.search(r"[\"“]([^\"”]{2,80})[\"”]", str(text or ""))
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(?:to|say|says|saying|reads?)\s*:?\s+'([^']{2,80})'", str(text or ""))
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(?:to|say|says|saying)\s*:?\s+(.{3,80})$", str(text or ""), re.I)
    return m.group(1).strip(" .!") if m else None


def _recipe(key, edl, prog, text):
    if key == "boring":
        target = max(SHORT_FLOOR_S, prog * (1 - CUT_SHARE["boring"]))
        return ("call get_kept_transcript once; pick the 1-3 weakest "
                "sentences (a repeat, a tangent, a slow setup, a 'so "
                "anyway') and remove each with cut_output_range on its exact "
                "program times; then cut_silences(min_silence_s=0.35, "
                "padding_s=0.1) and remove_filler_words. Keep the opening "
                "line, the payoff and every sentence the story needs. "
                f"Done bar: under about {target:.0f} s (now {prog:.1f} s) and "
                "it still opens and ends on complete sentences."
                if prog > SHORT_FLOOR_S else
                f"the video is only {prog:.1f} s: cut_silences("
                "min_silence_s=0.3, padding_s=0.08) and remove_filler_words "
                "only; if nothing is left to cut, say it is already tight.")
    if key == "shorter":
        target = max(SHORT_FLOOR_S * 0.8, prog * (1 - CUT_SHARE["shorter"]))
        return ("call get_kept_transcript once and remove whole weaker "
                "sentences with cut_output_range until the video is about "
                f"{target:.0f} s (now {prog:.1f} s); keep the opening line "
                "and the payoff; then cut_silences(min_silence_s=0.35, "
                "padding_s=0.1).")
    if key == "longer":
        return ("bring back the sentences right before or after the kept "
                "ones with restore_range (complete sentences only) until the "
                f"video is about {prog * 1.3:.0f} s (now {prog:.1f} s); keep "
                "the opening line first.")
    if key == "punchier":
        return ("cut_silences(min_silence_s=0.25, padding_s=0.06), then "
                "remove_filler_words, then remove the single slowest "
                "sentence with cut_output_range when the video is over "
                f"{SHORT_FLOOR_S:.0f} s (now {prog:.1f} s). Add at most one "
                "add_zoom punch-in (strength 0.2, 1.4-2.6 s) on the most "
                "important line if no zoom is within 3 s of it. Do not "
                "restyle captions or graphics.")
    if key == "ums":
        return ("remove_filler_words() once, then cut_silences("
                "min_silence_s=0.35, padding_s=0.1). Nothing else. If it "
                "finds no fillers, say the video already has none.")
    if key == "silences":
        return ("cut_silences(min_silence_s=0.35, padding_s=0.1) once. "
                "Nothing else.")
    if key in ("captions_bigger", "captions_smaller"):
        cur = _caption_scale(edl)
        new = round(min(2.6, cur * 1.25), 2) if key == "captions_bigger" \
            else round(max(0.6, cur * 0.8), 2)
        return (f"one call: set_caption_style(style={{'size_scale': {new}}}) "
                f"(now {cur:g}). Change nothing else about the captions or "
                "the edit.")
    if key in ("title_bigger", "title_smaller"):
        item = title_item(edl)
        if not item:
            return ("the edit has no opening title yet: add one with "
                    "add_motion_graphic(template='hook_title', start=0.1, "
                    "end=2.8, params={'text': <the opening line, 3-6 "
                    "words>, 'size': 1.4}).")
        cur = _title_size(edl)
        new = round(min(2.2, cur * 1.2), 2) if key == "title_bigger" \
            else round(max(0.6, cur * 0.82), 2)
        return (f"one call: set_motion_graphic(id='{item.get('id')}', "
                f"params={{'size': {new}}}) (size now {cur:g}). Keep its "
                "text, place and timing.")
    if key == "title_change":
        item = title_item(edl)
        want = _quoted(text)
        words = (f"'{want}'" if want else "the words the user gave (or, "
                 "when they gave none, a sharper 3-6 word line from the "
                 "opening sentence, with one *word* in asterisks)")
        if item:
            return (f"one call: set_motion_graphic(id='{item.get('id')}', "
                    f"params={{'text': {words}}}). Keep its size, place and "
                    "timing.")
        return ("add_motion_graphic(template='hook_title', start=0.1, "
                f"end=2.8, params={{'text': {words}}}).")
    if key == "title_add":
        item = title_item(edl)
        want = _quoted(text)
        if item:
            cur = ((item.get("params") or {}).get("text") or "").strip()
            if want:
                return (f"the edit already opens on a title ('{cur}'): set "
                        f"its words with one set_motion_graphic(id='"
                        f"{item.get('id')}', params={{'text': '{want}'}}).")
            return (f"the edit already opens on a title ('{cur}'): keep it "
                    "and say so in one sentence; add nothing.")
        return ("add_motion_graphic(template='hook_title', start=0.1, "
                "end=2.8, params={'text': "
                + (f"'{want}'" if want else "<the opening line, 3-6 words, "
                   "one *word* in asterisks>") + "}).")
    if key == "broll":
        return ("source_broll once for 1-3 cutaways on concrete things the "
                "speaker names (2-3 s each, never over the opening line or "
                "the last sentence, never stacked on an existing B-roll). "
                "Keep everything else.")
    if key == "zooms":
        return ("add 2-3 add_zoom punch-ins (strength 0.2-0.3, 1.4-2.6 s "
                "each) on the key lines, each at least 3 s from another zoom "
                "and never during a B-roll or the opening title. Keep "
                "everything else.")
    return ""


def note(ctx, text):
    """The per-turn recipe note ('' without an active follow-up)."""
    st = active(ctx)
    if not st:
        return ""
    edl = st.get("start_edl") or {}
    prog = float(st.get("start_s") or 0.0)
    steps = []
    for k in st["keys"]:
        r = _recipe(k, edl, prog, text)
        if r:
            steps.append(f"- {LABELS[k]}: {r}")
    keep = [LANE_LABELS[x] for x in ("cuts", "captions", "title", "graphics",
                                     "zooms", "frame", "broll", "grade",
                                     "audio")
            if x not in st["allowed"]]
    return (
        "[system: FOLLOW-UP REQUEST] The user asked to " + st["label"]
        + f". The video is {prog:.1f} s now. RECIPE (the whole job, in "
          "this order):\n" + "\n".join(steps) + "\n"
        "SCOPE: change only what this asks. The "
        + ", ".join(keep)
        + " stay exactly as they are; writes outside the request are "
          "refused. Do not read skills, do not reset, do not fix review "
          "findings about parts the user did not ask about. Then "
          "render_preview(complete=true) ONCE, read its REQUEST CHECK (the "
          "before/after edit against this request), fix only a NOT MET line, "
          "and reply in one or two sentences with the real numbers (the "
          "length, the size) from that check.")


# ── the verification step (on every preview render) ───────────────────────

def request_check(ctx):
    """'REQUEST CHECK — ...' comparing the turn's start EDL with the current
    one against the request ('' without an active follow-up)."""
    st = active(ctx)
    if not st:
        return ""
    try:
        now = ctx.latest_edl()["json"]
    except Exception:  # noqa: BLE001
        return ""
    before = st.get("start_edl") or {}
    a, b = float(st.get("start_s") or 0.0), program_s(now)
    changed = changed_lanes(before, now)
    lines = [f"REQUEST CHECK (the user asked to {st['label']}): length "
             f"{a:.1f} s -> {b:.1f} s; changed: "
             + (", ".join(LANE_LABELS.get(x, x) for x in changed) or "nothing")
             + "."]
    met = True
    dirn = st.get("direction")
    for k in st["keys"]:
        if k in CUT_SHARE and a > SHORT_FLOOR_S:
            need = a * (1 - CUT_SHARE[k])
            if b > need + 0.05:
                met = False
                lines.append(f"NOT MET: {LABELS[k]} needs the video under "
                             f"about {need:.1f} s; remove another weak "
                             "sentence with cut_output_range.")
        elif k == "longer" and b < a + 1.0:
            met = False
            lines.append("NOT MET: the video is not longer; restore_range "
                         "the sentences next to the kept ones.")
        elif k in ("captions_bigger", "captions_smaller"):
            x, y = _caption_scale(before), _caption_scale(now)
            if (k == "captions_bigger" and y <= x) or \
                    (k == "captions_smaller" and y >= x):
                met = False
                lines.append(f"NOT MET: caption size {x:g} -> {y:g}.")
            else:
                lines.append(f"caption size {x:g} -> {y:g}.")
        elif k in ("title_bigger", "title_smaller"):
            x, y = _title_size(before), _title_size(now)
            if (k == "title_bigger" and y <= x) or \
                    (k == "title_smaller" and y >= x):
                met = False
                lines.append(f"NOT MET: title size {x:g} -> {y:g}.")
            else:
                lines.append(f"title size {x:g} -> {y:g}.")
        elif k == "title_change":
            x = ((title_item(before) or {}).get("params") or {}).get("text")
            y = ((title_item(now) or {}).get("params") or {}).get("text")
            if x == y:
                met = False
                lines.append("NOT MET: the title text is unchanged.")
            else:
                lines.append(f"title now '{y}'.")
        elif k in ("broll", "zooms") and k not in changed:
            met = False
            lines.append(f"NOT MET: no {LABELS[k][4:]} was added.")
        elif k in ("ums", "silences") and "cuts" not in changed:
            lines.append("nothing to cut: say the video already has none.")
    if dirn in ("shorter", "not_longer") and b > a + 0.05:
        met = False
        lines.append("NOT MET: the video got LONGER; it must not.")
    outside = [x for x in changed if x not in st["allowed"]]
    if outside:
        met = False
        lines.append("NOT ASKED and changed: "
                     + ", ".join(LANE_LABELS.get(x, x) for x in outside) + ".")
    if met:
        lines.append("MET: reply now with these numbers; do not edit again.")
    st["last_check"] = {"start_s": a, "now_s": round(b, 2), "met": met,
                        "changed": changed}
    return " ".join(lines)


# ── the reply's lead (trial_reply.finish, when the draft has no summary) ──

def _pct(x, y):
    try:
        return int(round(abs(y / x - 1.0) * 100))
    except ZeroDivisionError:
        return 0


def reply_lead(ctx):
    """One sentence on what this follow-up actually did, in the user's
    words, from the before/after EDL ('' without an active follow-up)."""
    st = active(ctx)
    if not st:
        return ""
    try:
        now = ctx.latest_edl()["json"]
    except Exception:  # noqa: BLE001
        return ""
    before = st.get("start_edl") or {}
    a, b = float(st.get("start_s") or 0.0), program_s(now)
    changed = changed_lanes(before, now)
    bits = []
    for k in st["keys"]:
        if k in ("boring", "shorter", "punchier", "ums", "silences", "longer"):
            if "cuts" in changed and abs(b - a) >= 0.3:
                verb = "longer" if b > a else "shorter"
                bits.append(f"it is now {b:.0f} seconds, {abs(a - b):.0f} "
                            f"second{'s' if round(abs(a - b)) != 1 else ''} "
                            f"{verb} than before" if abs(a - b) >= 1 else
                            f"it is now {b:.1f} seconds, a little {verb}")
            elif k == "ums":
                bits.append("there were no ums or filler words to remove")
            elif k == "silences":
                bits.append("there were no long pauses left to cut")
        elif k in ("captions_bigger", "captions_smaller"):
            x, y = _caption_scale(before), _caption_scale(now)
            if y != x:
                bits.append(f"the captions are {_pct(x, y)}% "
                            f"{'bigger' if y > x else 'smaller'}")
        elif k in ("title_change", "title_add"):
            x = ((title_item(before) or {}).get("params") or {}).get("text")
            y = ((title_item(now) or {}).get("params") or {}).get("text")
            if y and y != x:
                bits.append("the opening title now reads \u201c"
                            + str(y).replace("*", "") + "\u201d")
        elif k == "broll" and "broll" in changed:
            n = (len(now.get("fx") or []) + len(now.get("inserts") or [])) - (
                len(before.get("fx") or []) + len(before.get("inserts") or []))
            if n > 0:
                bits.append(f"I added {n} B-roll cutaway{'s' if n != 1 else ''}")
        elif k == "zooms" and "zooms" in changed:
            n = len((now.get("effects") or {}).get("zooms") or []) - len(
                (before.get("effects") or {}).get("zooms") or [])
            if n > 0:
                bits.append(f"I added {n} zoom punch-in{'s' if n != 1 else ''}")
    bits = [x for i, x in enumerate(bits) if x not in bits[:i]]
    if not bits:
        return ""
    text = "; ".join(bits)
    if all(x.startswith("there were no") for x in bits):
        return text[0].upper() + text[1:] + "."
    return "Done: " + text + ("" if text.endswith(".") else ".")
