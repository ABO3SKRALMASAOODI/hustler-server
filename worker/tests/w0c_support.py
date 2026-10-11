"""Shared fixtures for the track-0C tests (tests/test_w0c_*.py).

Fixture device cards (realistic Wave 1 shapes, never shipped as live cards),
a tolerant fake database and a real ToolContext over it.
"""

import copy
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

import db as dbx  # noqa: E402

_BUDGET = {"calm": {"per_short": 1, "min_gap_s": 4},
           "designed": {"per_short": 3, "min_gap_s": 2},
           "charged": {"per_short": 5, "min_gap_s": 1}}
_COST = {"ms_per_frame": {"interactive": 4.0, "batch": 2.5}, "fixed_ms": 300}


def card(card_id, **over):
    """A valid card (the ROADMAP §0C example) with fields overridden."""
    base = {
        "id": card_id, "family": "matte_fx", "verb": "add_subject_effect",
        "fx_type": "subject", "stage": "world",
        "params": {"kind": "fill", "colour": ["white", "black", "accent"],
                   "frames": [1, 2]},
        "needs": {"sidecars": ["matte.person"],
                  "lane_features": ["ffmpeg:maskedmerge"],
                  "footage": "person, coverage >= 0.4%"},
        "binds_to": ["beat.kick", "beat.downbeat", "cut", "word.payoff"],
        "budget": copy.deepcopy(_BUDGET),
        "never": ["on a face-dependent payoff onset",
                  "within 0.5 s of another matte_fx"],
        "owner_flags": {"adds_sound": False, "adds_zoom": False,
                        "flash": True},
        "rungs": ["full", {"substitute": "full_frame_flash",
                           "intensity": 0.6}, "omit"],
        "cost": copy.deepcopy(_COST),
        "exemplars": ["22@≈8.43", "40@≈4.67", "20@9.10"],
        "verify": ["land_err_f<=1", "fill_iou>=0.95", "flash_risk"],
        "status": "live",
        "description": "subject flashes white on a hit",
    }
    base.update(over)
    return base


def six_matte_cards():
    """Six matte devices (ROADMAP §9 degrade ladder), each with a substitute
    every lane can draw: AT-0C-1's 'short with 6 matte devices'."""
    return {
        "subject_fill_flash": card("subject_fill_flash"),
        "subject_glow": card(
            "subject_glow", params={"kind": "glow"},
            owner_flags={"adds_sound": False, "adds_zoom": False,
                         "flash": False},
            rungs=["full", {"substitute": "halation"}, "omit"],
            verify=["matte_flicker<=0.035"]),
        "poster_silhouette": card(
            "poster_silhouette", params={"kind": "poster"},
            owner_flags={"adds_sound": False, "adds_zoom": False,
                         "flash": False},
            rungs=["full", "reduced", {"substitute": "colour_field_card"},
                   "omit"], verify=["matte_iou>=0.92"]),
        "text_behind_head": card(
            "text_behind_head", family="type_depth",
            verb="add_text_behind", fx_type=None, stage="motion",
            needs={"sidecars": ["matte.person"],
                   "lane_features": ["chromium"]},
            owner_flags={"adds_sound": False, "adds_zoom": False,
                         "flash": False},
            rungs=["full", {"substitute": "face_safe_slam"}, "omit"],
            verify=["glyph_visible>=0.65"]),
        "background_swap": card(
            "background_swap", fx_type="background",
            params={"kind": "swap"},
            owner_flags={"adds_sound": False, "adds_zoom": False,
                         "flash": False},
            rungs=["full", {"substitute": "colour_field_card"}, "omit"],
            verify=["matte_iou>=0.92"]),
        "subject_clone": card(
            "subject_clone", params={"kind": "clone"},
            owner_flags={"adds_sound": False, "adds_zoom": False,
                         "flash": False},
            rungs=["full", {"substitute": "static_placement"}, "omit"],
            verify=["matte_iou>=0.92"]),
    }


def owner_flag_cards():
    """A zoom card, a sound card and a dark card (restraint invariants)."""
    off = {"adds_sound": False, "adds_zoom": False, "flash": False}
    return {
        "punch_zoom_hit": card(
            "punch_zoom_hit", family="camera", verb="add_zoom",
            fx_type=None, stage="world", needs={},
            owner_flags=dict(off, adds_zoom=True), rungs=["full", "omit"],
            verify=["land_err_f<=1"]),
        "whoosh_on_turn": card(
            "whoosh_on_turn", family="sfx", verb="add_sfx", fx_type=None,
            stage="music_bus", needs={},
            owner_flags=dict(off, adds_sound=True), rungs=["full", "omit"],
            verify=["land_err_f<=1"]),
        "strobe_section": card(
            "strobe_section", family="punctuation", verb="add_accent",
            fx_type="strobe", stage="over_type", needs={},
            owner_flags=dict(off, flash=True), rungs=["full", "omit"],
            verify=["flash_risk"], status="dark",
            flag="W0C_TEST_STROBE_FLAG"),
    }


def write_cards(tmp_dir, cards, compiled=True, yaml_too=False):
    os.makedirs(tmp_dir, exist_ok=True)
    from devices import compiler
    for cid, c in cards.items():
        if compiled:
            with open(os.path.join(tmp_dir, cid + ".json"), "w",
                      encoding="utf-8") as f:
                f.write(compiler.dumps(c))
        if yaml_too:
            import yaml
            with open(os.path.join(tmp_dir, cid + ".yaml"), "w",
                      encoding="utf-8") as f:
                yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
    return tmp_dir


def use_cards(monkeypatch, cards, tmp_dir):
    """Point devices.load_cards (and everything built on it) at fixture
    cards for one test."""
    import devices
    write_cards(str(tmp_dir), cards)
    monkeypatch.setattr(devices, "CARDS_DIR", str(tmp_dir))
    devices._cached_cards.cache_clear()
    return devices.load_cards()


class FakeDb:
    """Tolerant fake: latest/insert EDL, assets, app_kv; anything else
    answers None (as an absent table would)."""

    def __init__(self, edl, version=7):
        self.rows = [{"version": version, "json": copy.deepcopy(edl)}]
        self.assets = {}
        self.kv = {}
        self.inserts = []
        self.history_keys = set()

    def run(self, fn, *a, **k):
        if fn is dbx.latest_edl:
            return self.rows[-1]
        if fn is dbx.get_edl_version:
            for r in self.rows:
                if r["version"] == a[1]:
                    return r
            return None
        if fn is dbx.insert_edl:
            v = self.rows[-1]["version"] + 1
            self.rows.append({"version": v, "json": copy.deepcopy(a[1])})
            self.inserts.append(v)
            return v
        if fn is dbx.asset_by_key:
            return self.assets.get(a[1])
        if fn is dbx.insert_asset:
            _pid, kind, key = a
            self.assets[key] = {"kind": kind, "storage_key": key,
                                "duration_s": k.get("duration_s"),
                                "meta": k.get("meta") or {}}
            return len(self.assets)
        if fn is dbx.kv_get:
            return self.kv.get(a[0])
        if fn is dbx.kv_put:
            self.kv[a[0]] = a[1]
            return None
        if fn is dbx.kv_merge_json_map:
            cur = json.loads(self.kv.get(a[0]) or "{}")
            cur.update(json.loads(a[1]))
            self.kv[a[0]] = json.dumps(cur)
            return None
        if getattr(fn, "__name__", "") == "q":   # music_gate history probe
            return any(key in json.dumps(r["json"]) for r in self.rows
                       for key in self.history_keys)
        return None


def tool_context(edl, duration=20.0, user_message="", mcp=False,
                 job_id=9):
    """A real agent_tools.ToolContext over FakeDb (the real write_edl)."""
    import agent_tools
    from schemas import validate_edl
    norm = validate_edl(edl, duration).model_dump()
    fake = FakeDb(norm)
    ctx = agent_tools.ToolContext(
        fake, {"id": job_id, "user_id": 3},
        {"id": 7, "chat_session_id": 11},
        {"video": {"duration": duration, "width": 1920, "height": 1080,
                   "fps": 30.0}, "words": []},
        tempfile.mkdtemp())
    ctx.user_message = user_message
    if mcp:
        ctx.sight_out = True
    return ctx, fake


def upload_asset(fake, key, **meta):
    fake.assets[key] = {"kind": "music", "storage_key": key,
                        "duration_s": 120.0, "meta": dict(meta)}
    return key
