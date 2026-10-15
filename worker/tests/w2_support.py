"""Shared harness for the Wave 2 tests (tracks 2A-2F).

A ToolContext stand-in with a real transcript (V01's AI line, timings from
the fixture ASR, internal test data only as numbers), the parts of the
context the department verbs use, and EDL builders.
"""

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

import schemas  # noqa: E402

DUR = 60.0
# "And then I think perhaps AI is bigger than the internet and at least has
# the potential to reaccelerate growth" (source seconds 0-based)
LINE = [("And", 0.30), ("then", 0.34), ("I", 0.42), ("think", 0.52),
        ("perhaps", 0.74), ("AI", 1.20), ("is", 2.94), ("bigger", 4.64),
        ("than", 4.86), ("the", 5.10), ("internet", 5.22), ("and", 5.56),
        ("at", 6.18), ("least", 6.94), ("has", 7.08), ("the", 7.26),
        ("potential", 7.38), ("to", 7.72), ("reaccelerate", 8.58),
        ("growth.", 9.40), ("They", 11.0), ("promised", 11.3), ("us", 11.6),
        ("flying", 11.8), ("cars", 12.1), ("and", 12.4), ("all", 12.5),
        ("we", 12.6), ("got", 12.7), ("was", 12.8), ("140", 12.95),
        ("characters.", 13.4)]


def words():
    out = []
    for i, (w, t) in enumerate(LINE):
        nxt = LINE[i + 1][1] if i + 1 < len(LINE) else t + 0.5
        out.append({"w": w, "t0": t, "t1": round(min(nxt, t + 0.6), 3),
                    "p": 0.95})
    return out


def index(width=1920, height=1080, fps=25.0):
    return {"words": words(),
            "video": {"width": width, "height": height, "fps": fps,
                      "duration": DUR},
            "shots": [{"id": 0, "start": 0.0, "end": DUR}]}


def edl(keep=((0.0, 20.0),), ratio=None, **extra):
    d = {"keep": [list(k) for k in keep], "captions": {"enabled": False}}
    if ratio:
        d["frame"] = {"ratio": ratio}
    d.update(extra)
    return schemas.validate_edl(d, DUR).model_dump()


class _DB:
    def __init__(self, keys=None):
        self.keys = keys or {}

    def run(self, fn, *a, **k):
        return self.keys


class Ctx:
    """The parts of ToolContext the Wave 2 verbs use."""

    def __init__(self, edl_dict, idx=None, keys=None):
        self.db, self.project_id, self.duration = _DB(keys), 7, DUR
        self.version, self.edl, self.lines = 3, edl_dict, []
        self.index = idx if idx is not None else index()
        self.has_main_video = True
        self.workdir = None
        self.user_message = ""

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self.edl)}

    def write_edl(self, e, desc):
        self.edl = schemas.validate_edl(e, DUR).model_dump()
        self.version += 1
        self.lines.append(desc)
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"

    def proxy_path(self):
        return None


# ── Wave 2 pins (INTERFACES_W2.md §2, §5, §10) ──────────────────────────
W2_VERBS = {"add_typeset", "set_typeset", "remove_typeset", "import_font",
            "analyze_music", "get_syllables", "align_lyrics",
            "plan_to_music", "shape_music", "set_speed_ramp",
            "animate_still", "find_shots", "suggest_match_cuts",
            "pick_still", "plan_loop", "suggest_story", "apply_format_pack",
            "analyze_reference", "render_variants"}
W2_FX_TAGS = {"arrange"}
W2_SKILLS = {"typesetting", "type-fx", "rhythm", "montage", "structure",
             "creative-direction"}
W2_DEPARTMENTS = {"type", "direction"}
# Core compact catalog growth Wave 2 may add (department names and skill
# names in the core enums); the 82,700 ceiling is unchanged.
W2_CORE_GROWTH_MAX = 150
