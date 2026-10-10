"""One type system per short: the Look's type roles and its single accent.

Judged Oct 2026 (round 4): Elon's short mixed a serif/sans hook, a broadcast
clean_bar lower third, red condensed stat slabs and a yellow highlighter with
two accents (#FF3B30, #FFD84D); Thiel's ran five type roles. The human
references hold one typographic system per short: a backbone, at most two
more roles, and ONE accent colour on the words that matter.

The short's Look, as the motion tools read it (``short_look``):

- accent: the captions' highlight colour when one is set (a Look applied
  with apply_look, or set_caption_style), else the accent its graphics
  already wear (most graphics, earliest on a tie), else none yet;
- ink: the light type colour most of its type graphics wear (INK_TEMPLATES:
  a circle's stroke or an app card's brand colour is not type);
- roles: the type roles its graphics use, in first-use order (grotesk,
  condensed, serif, script, mono, hand).

``look_defaults`` gives a new graphic the Look's accent (and ink) when the
editor did not pass one — a template's own default never introduces a
second accent — and swaps a default font role that would be a fourth role
for one the short already uses. ``coherence_notes`` is the write-time
advisory: NOTE (look) lines naming a second accent, a role past the third,
or a style outside an editorial Look, each with its fix. Nothing here ever
rejects a write.
"""

import re

MAX_ROLES = 3
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_STAR = re.compile(r"\*[^*]+\*")

# font families (CSS names, as the templates' enums spell them) -> type role
FAMILY_ROLE = {
    "inter display": "grotesk", "inter": "grotesk", "inter tight": "grotesk",
    "manrope": "grotesk", "space grotesk": "grotesk", "montserrat": "grotesk",
    "poppins": "grotesk", "plus jakarta sans": "grotesk", "syne": "grotesk",
    "archivo black": "grotesk", "unbounded": "grotesk",
    "anton": "condensed", "bebas neue": "condensed", "archivo": "condensed",
    "instrument serif": "serif", "playfair display": "serif",
    "dm serif display": "serif", "bodoni moda": "serif",
    "pinyon script": "script", "great vibes": "script", "yellowtail": "script",
    "jetbrains mono": "mono", "caveat": "hand",
}
# template role / font enum values -> type role
WORD_ROLE = {"sans": "grotesk", "grotesk": "grotesk", "condensed": "condensed",
             "serif": "serif", "script": "script", "mono": "mono"}
ROLE_ORDER = ("grotesk", "condensed", "serif", "script", "mono", "hand")

# Templates that always wear their accent (a marker band, an accent bar, a
# number's bloom and symbols); any other wears it only on a *starred* word
# or an accent row.
ALWAYS_ACCENT = frozenset(("marker_text", "lower_third", "counter", "progress_ring",
                           "stat_card", "bar_compare", "line_chart", "timeline_steps",
                           "checklist", "chapter_title"))
# Captions motion looks that are editorial systems (no broadcast furniture)
EDITORIAL_LOOKS = frozenset(("editorial", "serif", "clean", "stack"))
# Templates whose ``color`` is the TYPE ink. Elsewhere ``color`` is a stroke
# (circle_highlight), a flash (flash_transition) or an app's brand colour
# (notification): it neither votes for the Look's ink nor takes it.
INK_TEMPLATES = frozenset(("chapter_title", "counter", "glow_title", "headline", "hook_title",
                           "marker_text", "phrase_build", "quote_card", "text_scramble",
                           "typewriter", "word_slam"))
# The Look's ink is a LIGHT type colour (WCAG relative luminance): a dark ink
# one graphic set for a paper card is not the short's ink over footage.
INK_MIN_LUMINANCE = 0.3


def _luminance(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _ink(m):
    """The light type ink a motion item wears (#RRGGBB upper case), or None."""
    if m.get("template") not in INK_TEMPLATES:
        return None
    c = (m.get("params") or {}).get("color")
    if not (isinstance(c, str) and _HEX.match(c)) or _luminance(c) < INK_MIN_LUMINANCE:
        return None
    return c.upper()


def _role(v):
    v = str(v or "").strip().lower()
    return WORD_ROLE.get(v) or FAMILY_ROLE.get(v)


def _has_star(v):
    return bool(_STAR.search(str(v or "")))


def item_roles(item):
    """The type roles a motion item sets, in reading order (a set of role
    names from ROLE_ORDER; empty when the template is not type or unknown)."""
    t = item.get("template")
    p = item.get("params") or {}
    out = []

    def add(r):
        if r and r not in out:
            out.append(r)
    if t == "phrase_build":
        for row in p.get("rows") or []:
            if isinstance(row, dict) and str(row.get("text") or "").strip():
                add(_role(row.get("role") or "sans") or "grotesk")
    elif t == "word_slam":
        if str(p.get("kicker") or "").strip():
            add("grotesk")
        add(_role(p.get("role") or "grotesk") or "grotesk")
    elif t == "hook_title":
        add(_role(p.get("font") or "Inter Display") or "grotesk")
        if p.get("treatment", "serif") == "serif" and _has_star(p.get("text")):
            add("serif")
    elif t == "marker_text":
        add(_role(p.get("font") or "sans") or "grotesk")
    elif t in ("typewriter", "text_scramble", "glow_title"):
        add(_role(p.get("font") or ("grotesk" if t == "glow_title" else "mono")) or "grotesk")
    elif t == "counter":
        add(_role(p.get("font") or "Inter Display") or "grotesk")
        if str(p.get("label") or "").strip():
            add("grotesk")
            if _has_star(p.get("label")):
                add("serif")
    elif t == "headline":
        add(_role(p.get("style") or "sans") or "grotesk")
        if str(p.get("kicker") or "").strip():
            add("grotesk")
        if p.get("accent_style") == "serif" and _has_star(p.get("text")):
            add("serif")
    elif t == "versus_split":
        add("grotesk")
        if p.get("vs") == "serif":
            add("serif")
    elif t in ("lower_third", "stat_card", "checklist", "timeline_steps", "quote_card",
               "chapter_title"):
        add("grotesk")
    return out


def item_accent(item):
    """The accent colour a motion item visibly wears (#RRGGBB upper case), or
    None (no accent param, or nothing on it takes the accent)."""
    t = item.get("template")
    p = item.get("params") or {}
    if t == "versus_split":
        c = p.get("right_color")
        return c.upper() if isinstance(c, str) and _HEX.match(c) else None
    c = p.get("accent")
    if not (isinstance(c, str) and _HEX.match(c)):
        return None
    # a payoff lockup sets every hero line in the accent (word_slam tier)
    worn = t in ALWAYS_ACCENT or (t == "word_slam" and p.get("tier") == "payoff")
    if not worn:
        for k in ("text", "label", "kicker", "left", "right"):
            if _has_star(p.get(k)):
                worn = True
                break
    if not worn and t == "phrase_build":
        worn = any(isinstance(r, dict) and (_has_star(r.get("text"))
                                            or str(r.get("accent") or "").strip().lower()
                                            in ("1", "true", "yes", "y", "on"))
                   for r in p.get("rows") or [])
    return c.upper() if worn else None


def _graphics(edl, skip_id=None):
    out = []
    for m in edl.get("motion") or []:
        if not isinstance(m, dict) or m.get("_synthetic") or m.get("id") == skip_id:
            continue
        if str(m.get("template") or "").startswith("caption"):
            continue
        out.append(m)
    return sorted(out, key=lambda m: (float(m.get("start") or 0.0), str(m.get("id"))))


def _chromatic(c):
    """A colour with a hue (not a white, grey or black)."""
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return max(r, g, b) - min(r, g, b) >= 48


def caption_accent(edl):
    """The captions' highlight colour when it is an accent (a hue: a white
    or grey highlight over grey captions is no accent for the graphics)."""
    caps = edl.get("captions")
    if isinstance(caps, dict):
        c = (caps.get("style") or {}).get("highlight_color")
        if isinstance(c, str) and _HEX.match(c) and _chromatic(c):
            return c.upper()
    return None


def short_look(edl, skip_id=None):
    """{"accent", "accent_from", "ink", "roles", "motion_look"} of the short
    (see the module docstring); ``skip_id`` leaves one graphic out (the one
    being written)."""
    items = _graphics(edl, skip_id)
    accent, src = caption_accent(edl), "the captions' highlight colour"
    if not accent:
        votes = {}
        for k, m in enumerate(items):
            a = item_accent(m)
            if a:
                n, first = votes.get(a, (0, k))
                votes[a] = (n + 1, first)
        if votes:
            accent = min(votes, key=lambda a: (-votes[a][0], votes[a][1]))
            src = "the accent its graphics wear"
        else:
            src = None
    inks = {}
    for k, m in enumerate(items):
        c = _ink(m)
        if c:
            n, first = inks.get(c, (0, k))
            inks[c] = (n + 1, first)
    ink = min(inks, key=lambda c: (-inks[c][0], inks[c][1])) if inks else None
    roles = []
    for m in items:
        for r in item_roles(m):
            if r not in roles:
                roles.append(r)
    caps = edl.get("captions")
    ml = (caps.get("style") or {}).get("motion_look") if isinstance(caps, dict) else None
    return {"accent": accent, "accent_from": src, "ink": ink, "roles": roles,
            "motion_look": ml}


def _role_param(template, spec):
    """(param name, {role: value}) of a template's single font-role knob."""
    pspec = (spec or {}).get("params") or {}
    for key in ("role", "font"):
        p = pspec.get(key) or {}
        if p.get("type") != "enum":
            continue
        values = {}
        for v in p.get("values") or []:
            r = _role(v)
            if r and r not in values:
                values[r] = v
        if values:
            return key, values
    return None, {}


def look_defaults(edl, template, spec, given, skip_id=None):
    """Params the short's Look fills in for a new graphic: {param: value}.

    - ``accent`` (a colour param the template has, not passed): the Look's
      accent, so a template default never adds a second accent;
    - ``color`` (a type template's ink, INK_TEMPLATES): the Look's ink, when
      at least two graphics already wear it;
    - the font-role knob (``role``/``font``), not passed, whose default
      would be a role past the third: the closest role the short already
      uses that the template offers.
    """
    if not isinstance(spec, dict) or template == "html":
        return {}
    given = given or {}
    pspec = spec.get("params") or {}
    look = short_look(edl, skip_id)
    out = {}
    if look["accent"] and (pspec.get("accent") or {}).get("type") == "color" \
            and "accent" not in given:
        out["accent"] = look["accent"]
    if look["ink"] and template in INK_TEMPLATES \
            and (pspec.get("color") or {}).get("type") == "color" and "color" not in given:
        items = _graphics(edl, skip_id)
        wear = sum(1 for m in items if _ink(m) == look["ink"])
        if wear >= 2:
            out["color"] = look["ink"]
    key, values = _role_param(template, spec)
    if key and key not in given and len(look["roles"]) >= MAX_ROLES:
        cur = _role((pspec.get(key) or {}).get("default"))
        if cur and cur not in look["roles"]:
            for r in look["roles"]:
                if r in values:
                    out[key] = values[r]
                    break
    return out


def coherence_notes(edl, item):
    """NOTE (look) lines for ``item`` against the rest of the short (advisory;
    [] when it fits the Look)."""
    notes = []
    look = short_look(edl, skip_id=item.get("id"))
    mid = item.get("id")
    acc = item_accent(item)
    if acc and look["accent"] and acc != look["accent"]:
        key = "right_color" if item.get("template") == "versus_split" else "accent"
        notes.append(
            f"NOTE (look): '{mid}' wears accent {acc}, a second accent in a short whose "
            f"accent is {look['accent']} ({look['accent_from']}). One accent per short: "
            f"set_motion_graphic('{mid}', params={{'{key}': '{look['accent']}'}}).")
    mine = item_roles(item)
    have = list(look["roles"])
    extra = [r for r in mine if r not in have]
    if extra and len(have) + len(extra) > MAX_ROLES:
        over = have + extra
        notes.append(
            f"NOTE (look): '{mid}' sets {', '.join(extra)} type, making "
            f"{len(over)} type roles in this short ({', '.join(over)}); the references "
            f"hold {MAX_ROLES} or fewer. Set it in a role the short already uses "
            f"({', '.join(have[:MAX_ROLES])}).")
    t = item.get("template")
    p = item.get("params") or {}
    if look["motion_look"] in EDITORIAL_LOOKS:
        if t == "lower_third" and p.get("style", "clean_bar") == "clean_bar":
            notes.append(
                f"NOTE (look): '{mid}' is a broadcast clean_bar lower third in an editorial "
                "Look. Name the speaker in the hook's kicker or the headline band, or use "
                "style 'minimal'.")
        if t == "marker_text" and p.get("style", "marker") == "marker":
            notes.append(
                f"NOTE (look): '{mid}' is a highlighter marker in an editorial Look. Use "
                "style 'underline' in the short's accent, or a phrase_build lockup.")
    return notes


# ── series: parallel graphics share one size ──────────────────────────────
# Judged Oct 2026 (round 4): Elon's stat run (32% FEWER ERRORS / 24% FASTER /
# 26% BETTER OVERALL) was three word_slams each justified on its own, so
# 'FASTER' rendered at about twice the cap height of the other labels and
# the block's top jumped ~40 px between stats. A run of parallel slams —
# the same template, role, fit, case and line count, each starting within
# SERIES_GAP_S of the previous one's end — is ONE series: the page sizes
# every line by the smallest fit of that line across the members (one size
# per line, one baseline, one column). The series is derived, never
# authored: attach_series writes each member's ``series`` (MotionItem.series;
# motion_templates.build_job passes it to the page as ``params._series``)
# at write time and before every render.
SERIES_GAP_S = 0.75
SERIES_TEMPLATES = frozenset(("word_slam",))
SERIES_MAX = 8
_LINES_RE = re.compile(r"\s*(?:/|\n)\s*")


def _series_key(m):
    p = m.get("params") or {}
    text = str(p.get("text") or "").strip()
    lines = [ln for ln in _LINES_RE.split(text) if ln.strip()]
    return (m.get("template"), p.get("role"), p.get("fit"), bool(p.get("uppercase", True)),
            str(p.get("tier") or "display"), len(lines), bool(str(p.get("kicker") or "").strip()))


def series_runs(items):
    """[[item, ...], ...]: the parallel runs (two or more members) among
    ``items`` (program-clock motion item dicts)."""
    cand = sorted((m for m in items or [] if isinstance(m, dict)
                   and m.get("template") in SERIES_TEMPLATES and not m.get("_synthetic")
                   and not m.get("full_duration_s")),
                  key=lambda m: (float(m.get("start") or 0.0), str(m.get("id"))))
    runs, cur = [], []
    for m in cand:
        if cur and _series_key(m) == _series_key(cur[-1]) \
                and float(m["start"]) - float(cur[-1]["end"]) <= SERIES_GAP_S \
                and float(m["start"]) >= float(cur[-1]["start"]) \
                and len(cur) < SERIES_MAX:
            cur.append(m)
            continue
        if len(cur) >= 2:
            runs.append(cur)
        cur = [m]
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def attach_series(items):
    """Set (or clear) each item's ``series`` = {"texts": the
    members' texts in order, "i": its index, "ids": the members' ids}.
    A windowed piece of a stitched preview (``full_duration_s``) keeps the
    series its full program gave it. Mutates the dicts; returns the runs."""
    runs = series_runs(items)
    member = {}
    for run in runs:
        texts = [str((m.get("params") or {}).get("text") or "") for m in run]
        ids = [str(m.get("id")) for m in run]
        for k, m in enumerate(run):
            member[id(m)] = {"texts": texts, "i": k, "ids": ids}
    for m in items or []:
        if not isinstance(m, dict) or m.get("full_duration_s"):
            continue
        if id(m) in member:
            m["series"] = member[id(m)]
        else:
            m.pop("series", None)
    return runs
