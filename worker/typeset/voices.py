"""Type voices: named typographic personalities (ROADMAP 2A/2F).

A voice is NOT a house style. It is one of many typographic personalities
the planner chooses per story beat — and the variety rule (creative.py)
keeps a project from hearing the same voice twice in a row unless it is a
deliberate callback. Each voice sets:

  rungs     the face, case and tracking of each ladder rung:
            hero (payload, number, negation, accent, profanity),
            content (other content words), whisper (connectors), and
            optional ``emotion`` / ``number`` overrides
  ladder    cap-height ratio of content and whisper to the hero
  layouts   the layout families it reads best in, best first
  verbs     the entrance verb per rung (MG.typefx)
  material  the hero's surface (flat, neon, crt, outline, ghost, emboss)
  palette   ink and accent ("accent" = the Look's accent at render)
  accent_on which roles take the accent colour (one accent per lockup)
  depth     front | behind | auto
  beat      when it earns its place (the planner's reason)
  exemplars internal reference moments only (never shown to users)
"""

VOICES = {
    "magazine": {
        "hero_share": 0.75,
        "label": "magazine lockup behind the head",
        "rungs": {
            "hero": {"family": "Noto Serif Display", "weight": 800,
                     "stretch": 62.5, "case": "upper", "tracking": -0.005},
            "content": {"family": "Noto Serif Display", "weight": 400,
                        "stretch": 62.5, "style": "italic", "case": "keep",
                        "tracking": 0.0},
            "whisper": {"family": "Inter", "weight": 600, "case": "upper",
                        "tracking": 0.22},
        },
        "ladder": {"content": 0.46, "whisper": 0.105},
        "layouts": ["magazine", "stack", "flank"],
        "verbs": {"hero": "settle", "content": "blur_resolve",
                  "whisper": "cut"},
        "material": "flat",
        "palette": {"ink": "#f4efe6", "accent": "accent"},
        "accent_on": ["accent", "negation", "number"],
        "depth": "behind", "fit": "width",
        "beat": "a payoff line over a portrait shot with headroom: the "
                "words become the set, the head passes in front",
        "exemplars": ["09@1.27", "05@0.23"],
    },
    "whisper_shout": {
        "hero_share": 0.3,
        "label": "whisper connectors, shouted payload",
        "rungs": {
            "hero": {"family": "Inter Display", "weight": 900,
                     "case": "upper", "tracking": -0.035},
            "content": {"family": "Inter Display", "weight": 700,
                        "case": "upper", "tracking": -0.02},
            "whisper": {"family": "Instrument Serif", "weight": 400,
                        "style": "italic", "case": "lower",
                        "tracking": 0.0},
        },
        "ladder": {"content": 0.5, "whisper": 0.24},
        "layouts": ["stack", "staircase", "constellation"],
        "verbs": {"hero": "cut", "content": "cut", "whisper": "fade"},
        "material": "flat",
        "palette": {"ink": "#ffffff", "accent": "accent"},
        "accent_on": ["payload", "accent", "negation", "number"],
        "depth": "front", "fit": "width",
        "beat": "a manifesto line or a turn of the argument: small "
                "connectors set up one shouted word",
        "exemplars": ["04@0.5", "13@0.2"],
    },
    "staircase_red": {
        "hero_share": 0.6,
        "label": "condensed staircase, red and white",
        "rungs": {
            "hero": {"family": "League Gothic", "weight": 400,
                     "stretch": 100, "case": "upper", "tracking": 0.01},
            "content": {"family": "League Gothic", "weight": 400,
                        "stretch": 88, "case": "upper", "tracking": 0.02},
            "whisper": {"family": "Inter", "weight": 700, "case": "upper",
                        "tracking": 0.16},
        },
        "ladder": {"content": 0.62, "whisper": 0.2},
        "layouts": ["staircase", "stack_up", "stack"],
        "verbs": {"hero": "drop", "content": "drop", "whisper": "cut"},
        "material": "flat",
        "palette": {"ink": "#ffffff", "accent": "#ff2a1f"},
        "accent_on": ["payload", "accent", "number", "negation"],
        "depth": "front", "fit": "width",
        "beat": "a build that climbs: each step lands on its word, the last "
                "step is the payoff",
        "exemplars": ["15@2.67", "19@3.13"],
    },
    "slot_blackletter": {
        "hero_share": 0.4,
        "label": "blackletter on slot baselines with an extended red italic",
        "rungs": {
            "hero": {"family": "UnifrakturMaguntia", "weight": 400,
                     "case": "keep", "tracking": 0.0},
            "content": {"family": "Anybody", "weight": 800, "stretch": 140,
                        "style": "italic", "case": "upper",
                        "tracking": -0.01},
            "whisper": {"family": "Anybody", "weight": 600, "stretch": 120,
                        "style": "italic", "case": "upper",
                        "tracking": 0.04},
        },
        "ladder": {"content": 0.5, "whisper": 0.26},
        "layouts": ["slots", "stack", "flank"],
        "verbs": {"hero": "strobe_flip", "content": "cut",
                  "whisper": "cut"},
        "material": "glow",
        "palette": {"ink": "#f6f2ea", "accent": "#ff1d25"},
        "accent_on": ["content", "whisper"],
        "depth": "auto", "fit": "slot",
        "beat": "a trend-desk quote with attitude; best on a drop or a "
                "punchline",
        "exemplars": ["20@3.0"],
    },
    "neon_script": {
        "hero_share": 0.3,
        "label": "neon script with a hand-lettered aside",
        "rungs": {
            "hero": {"family": "Yellowtail", "weight": 400, "case": "keep",
                     "tracking": 0.0},
            "content": {"family": "Gloock", "weight": 400, "case": "upper",
                        "tracking": 0.02},
            "whisper": {"family": "Permanent Marker", "weight": 400,
                        "case": "lower", "tracking": 0.0},
        },
        "ladder": {"content": 0.55, "whisper": 0.32},
        "layouts": ["constellation", "flank", "stack"],
        "verbs": {"hero": "lamp", "content": "settle", "whisper": "cut"},
        "material": "neon",
        "palette": {"ink": "#fff6ee", "accent": "#ff3b3b"},
        "accent_on": ["payload", "accent"],
        "depth": "front", "fit": "width",
        "beat": "a night, a loop, a feeling: one word glows like a sign",
        "exemplars": ["29@1.3"],
    },
    "editorial_serif": {
        "hero_share": 0.2,
        "label": "quiet editorial serif column",
        "rungs": {
            "hero": {"family": "Fraunces", "weight": 600, "style": "italic",
                     "case": "keep", "tracking": -0.01,
                     "axes": {"SOFT": 50, "WONK": 1, "opsz": 144}},
            "content": {"family": "Fraunces", "weight": 400,
                        "case": "keep", "tracking": -0.005,
                        "axes": {"opsz": 96}},
            "whisper": {"family": "Inter Tight", "weight": 500,
                        "case": "upper", "tracking": 0.14},
        },
        "ladder": {"content": 0.7, "whisper": 0.3},
        "layouts": ["side_column", "justified", "stack"],
        "verbs": {"hero": "blur_resolve", "content": "blur_resolve",
                  "whisper": "fade"},
        "material": "flat",
        "palette": {"ink": "#f3ede2", "accent": "accent"},
        "accent_on": ["accent"],
        "depth": "front", "fit": "tracking",
        "beat": "a reflective sentence beside the speaker; pauses stay "
                "empty",
        "exemplars": ["10@0.47", "12@0.417"],
    },
    "quote_window": {
        "hero_share": 0.5,
        "label": "one word at a time, sized by meaning",
        "rungs": {
            "hero": {"family": "Inter Display", "weight": 900,
                     "case": "keep", "tracking": -0.03},
            "content": {"family": "Inter Display", "weight": 700,
                        "case": "keep", "tracking": -0.02},
            "whisper": {"family": "Inter Display", "weight": 700,
                        "case": "keep", "tracking": -0.01},
            "emotion": {"family": "Instrument Serif", "weight": 400,
                        "style": "italic", "case": "keep",
                        "tracking": 0.0},
        },
        "ladder": {"content": 0.62, "whisper": 0.4},
        "layouts": ["quote_window"],
        "verbs": {"hero": "settle", "content": "cut", "whisper": "cut"},
        "material": "flat",
        "palette": {"ink": "#ffffff", "accent": "#ffb03a"},
        "accent_on": ["emotion", "accent"],
        "depth": "front", "fit": "width",
        "beat": "a short quote over a held shot or a card on black; one "
                "word owns the frame at a time",
        "exemplars": ["02@0.03", "35@0.3"],
    },
    "condensed_poster": {
        "hero_share": 0.25,
        "label": "ultra-condensed poster, one heavy red word",
        "rungs": {
            "hero": {"family": "Six Caps", "weight": 400, "case": "upper",
                     "tracking": 0.0},
            "content": {"family": "Noto Serif Display", "weight": 500,
                        "stretch": 62.5, "case": "upper",
                        "tracking": 0.01},
            "whisper": {"family": "Noto Serif Display", "weight": 400,
                        "stretch": 62.5, "style": "italic",
                        "case": "lower", "tracking": 0.0},
        },
        "ladder": {"content": 0.38, "whisper": 0.2},
        "layouts": ["spread", "stack", "tilt"],
        "verbs": {"hero": "echo_stack", "content": "cut",
                  "whisper": "fade"},
        "material": "flat",
        "palette": {"ink": "#ece7de", "accent": "#d7141a"},
        "accent_on": ["payload", "accent", "profanity"],
        "depth": "auto", "fit": "width",
        "beat": "a single defiant word that should fill the frame",
        "exemplars": ["34@0.3", "37@0.3"],
    },
    "lamp_karaoke": {
        "hero_share": 0.4,
        "label": "the line waits dim; each word ignites as it is said",
        "rungs": {
            "hero": {"family": "Big Shoulders Display", "weight": 850,
                     "case": "upper", "tracking": 0.0},
            "content": {"family": "Big Shoulders Display", "weight": 700,
                        "case": "upper", "tracking": 0.01},
            "whisper": {"family": "Big Shoulders Display", "weight": 500,
                        "case": "upper", "tracking": 0.04},
        },
        "ladder": {"content": 0.62, "whisper": 0.42},
        "layouts": ["justified", "stack"],
        "verbs": {"hero": "lamp", "content": "lamp", "whisper": "lamp"},
        "material": "flat",
        "palette": {"ink": "#fff4dc", "accent": "#ffb21f"},
        "accent_on": ["payload", "accent"],
        "depth": "front", "fit": "tracking",
        "beat": "a line the viewer should read ahead of the voice, then "
                "hear land word by word",
        "exemplars": [],
    },
    "hairline_wide": {
        "hero_share": 0.2,
        "label": "hairline extended capitals",
        "rungs": {
            "hero": {"family": "Archivo", "weight": 100, "stretch": 125,
                     "case": "upper", "tracking": 0.04},
            "content": {"family": "Archivo", "weight": 200, "stretch": 125,
                        "case": "upper", "tracking": 0.12},
            "whisper": {"family": "Michroma", "weight": 400,
                        "case": "upper", "tracking": 0.28},
        },
        "ladder": {"content": 0.32, "whisper": 0.12},
        "layouts": ["spread", "flank", "stack"],
        "verbs": {"hero": "duration_wipe", "content": "duration_wipe",
                  "whisper": "cut"},
        "material": "flat",
        "palette": {"ink": "#ffffff", "accent": "accent"},
        "accent_on": ["negation", "accent"],
        "depth": "auto", "fit": "width",
        "beat": "one enormous quiet word (a NO, a name) that the frame "
                "breathes around",
        "exemplars": ["09@15.6"],
    },
    "marker_note": {
        "hero_share": 0.35,
        "label": "hand-lettered margin note",
        "rungs": {
            "hero": {"family": "Permanent Marker", "weight": 400,
                     "case": "upper", "tracking": 0.0},
            "content": {"family": "Gochi Hand", "weight": 400,
                        "case": "keep", "tracking": 0.0},
            "whisper": {"family": "Gochi Hand", "weight": 400,
                        "case": "lower", "tracking": 0.0},
        },
        "ladder": {"content": 0.62, "whisper": 0.48},
        "layouts": ["tilt", "constellation", "flank"],
        "verbs": {"hero": "typewriter", "content": "typewriter",
                  "whisper": "typewriter"},
        "material": "flat",
        "palette": {"ink": "#fffdf6", "accent": "#ffdd33"},
        "accent_on": ["payload", "accent"],
        "depth": "front", "fit": "width",
        "beat": "an aside, a correction, the editor talking back",
        "exemplars": ["16@2.8"],
    },
    "swiss_grid": {
        "hero_share": 0.3,
        "label": "flush-left grid, the number is the picture",
        "rungs": {
            "hero": {"family": "Inter Tight", "weight": 800,
                     "case": "keep", "tracking": -0.045},
            "content": {"family": "Inter Tight", "weight": 600,
                        "case": "keep", "tracking": -0.02},
            "whisper": {"family": "JetBrains Mono", "weight": 500,
                        "case": "upper", "tracking": 0.06},
        },
        "ladder": {"content": 0.36, "whisper": 0.16},
        "layouts": ["stack", "slots", "side_column"],
        "verbs": {"hero": "settle", "content": "cut", "whisper": "cut"},
        "material": "flat",
        "palette": {"ink": "#ffffff", "accent": "accent"},
        "accent_on": ["number", "accent"],
        "depth": "front", "fit": "width",
        "beat": "a figure or a list that should read like a data sheet",
        "exemplars": ["21@11.7"],
    },
    "techno_crt": {
        "hero_share": 0.4,
        "label": "monospaced type on a CRT surface",
        "rungs": {
            "hero": {"family": "Michroma", "weight": 400, "case": "upper",
                     "tracking": 0.02},
            "content": {"family": "JetBrains Mono", "weight": 700,
                        "case": "upper", "tracking": 0.02},
            "whisper": {"family": "JetBrains Mono", "weight": 400,
                        "case": "upper", "tracking": 0.08},
        },
        "ladder": {"content": 0.48, "whisper": 0.24},
        "layouts": ["stack", "justified"],
        "verbs": {"hero": "scramble_lock", "content": "scramble_lock",
                  "whisper": "typewriter"},
        "material": "crt",
        "palette": {"ink": "#d9ffe8", "accent": "#ff4a3d"},
        "accent_on": ["accent", "number"],
        "depth": "front", "fit": "width",
        "beat": "machines, code, systems, archives: the type is a screen",
        "exemplars": ["14@0.6"],
    },
}

DEFAULT_VOICE = "whisper_shout"
NAMES = tuple(VOICES)
RUNGS = ("hero", "content", "whisper")


def voice(name):
    return VOICES.get(name) or VOICES[DEFAULT_VOICE]


def rung_of(role):
    from . import roles
    if roles.is_hero(role):
        return "hero"
    if role == "connector":
        return "whisper"
    return "content"


def rung_spec(v, role):
    """The face/case/tracking dict a role is set in under voice ``v``."""
    r = v["rungs"]
    if role in r:
        return r[role]
    return r[rung_of(role)]


def ladder_ratio(v, role):
    rung = rung_of(role)
    if rung == "hero":
        return 1.0
    return float(v["ladder"].get(rung, 0.5))


def cased(text, case):
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    return text
