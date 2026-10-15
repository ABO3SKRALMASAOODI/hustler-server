"""Engines release integration (W1-W4 on one branch): the seams where the
waves met. One coherent design where two waves built the same thing."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import creative                                        # noqa: E402
import device_planner                                  # noqa: E402
import formats                                         # noqa: E402
import schemas                                         # noqa: E402
import world_planner                                   # noqa: E402
import w3_support as w3s                               # noqa: E402
from test_w3f_agent import BASE                        # noqa: E402
from tools_ext import format_tools                     # noqa: E402
from tools_ext import recipe_tools                     # noqa: E402


class _Cur:
    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a):
        self.calls += 1

    def fetchall(self):
        return self.rows


class _DB:
    def __init__(self, rows):
        self.cur = _Cur(rows)

    def run(self, q):
        db = self

        class Conn:
            def cursor(self):
                return db.cur
        return q(Conn())


class _Ctx:
    def __init__(self, rows, parent=5):
        self.project = {"parent_project_id": parent}
        self.project_id = 7
        self.db = _DB(rows)


def test_one_sibling_reader_serves_every_freshness_judge():
    rows = [{"motion": [{"template": "typeset",
                         "params": {"voice": "magazine"}}],
             "fx": [{"device": "subject_fill_white"}], "inserts": None}]
    ctx = _Ctx(rows)
    sib = recipe_tools.sibling_edls(ctx)
    assert sib == [{"motion": rows[0]["motion"], "fx": rows[0]["fx"],
                    "inserts": []}]
    assert creative.batch_ledger(ctx) == {"voices": ["magazine"],
                                          "devices": ["subject_fill_white"]}
    # one query per tool context, whoever asks first
    assert ctx.db.cur.calls == 1
    assert creative.sibling_edls(_Ctx(rows, parent=None)) == []


def test_batch_usage_counts_world_devices_on_anchored_items():
    sib = [{"fx": [{"device": "light_dissolve", "type": "light"}],
            "motion": [{"id": "w1", "template": "word_slam",
                        "anchor": {"to": "plane:pl1"}},
                       {"id": "o", "template": "el_orbit",
                        "anchor": {"to": "head"}}]},
           {"fx": [], "motion": [{"id": "w2", "template": "word_slam",
                                  "anchor": {"to": "plane:pl9"}}]}]
    use = device_planner.batch_usage(sib)
    assert use == {"light_dissolve": 1, "plane_type": 2,
                   "element_orbit": 1}


def _ending_plan(last_words):
    words = [{"w": w, "t0": 0.5 + 0.4 * i, "t1": 0.8 + 0.4 * i}
             for i, w in enumerate(("We built it. " + last_words).split())]
    return {"words": words, "moments": [
        {"t": words[-1]["t1"], "kind": "phrase_end"}]}


def test_the_world_planner_reads_the_batch(monkeypatch):
    src = w3s.source(w=1920, h=1080, dur=20.0)
    monkeypatch.setattr(world_planner, "_src", lambda index: src)
    monkeypatch.setattr(world_planner, "_valid", lambda e, ops, d: True)
    monkeypatch.setattr(world_planner, "_matte_ready", lambda *a: True)
    edl = schemas.validate_edl(dict(BASE), 20.0).model_dump()
    index = {"video": {"width": 1920, "height": 1080, "fps": 25.0,
                       "duration": 20.0}}
    plan = _ending_plan("And then it was gone.")
    devs = [c["device"] for c in world_planner.candidates(edl, index, plan)]
    assert "light_dissolve" in devs
    # a sibling short already ends in light: a second one is a house style
    batch = {"light_dissolve": 1}
    devs = [c["device"] for c in world_planner.candidates(
        edl, index, plan, batch=batch)]
    assert "light_dissolve" not in devs
    # unless the user asked for it
    devs = [c["device"] for c in world_planner.candidates(
        edl, index, plan, batch=batch, asked=("dissolve",))]
    assert "light_dissolve" in devs


def test_every_pack_the_loader_lists_is_valid_and_reachable():
    import devices
    cards = devices.load_cards()
    assert "D" in formats.IDS
    for p in formats.all_packs():
        assert formats.validate(p, cards) == [], p["id"]
    enum = format_tools.TOOL_SPECS["apply_format_pack"][2]["pack"]["enum"]
    assert enum == ["auto", *formats.IDS, "none"]
    assert format_tools.TOOL_SPECS["suggest_story"][2]["pack"]["enum"] == enum
    # B&W stays a device in every pack
    for p in formats.all_packs():
        assert "whole_short_mono" in p["colour_world"]["never"]


def test_main_footage_ramp_refuses_speech_unless_asked():
    import w2_support as S
    from tools_ext import montage_tools as MT
    ctx = S.Ctx(S.edl())
    out = MT.set_speed_ramp(ctx, 3.0, 7.0, low=0.6)
    assert out.startswith("REJECTED") and "allow_speech" in out
    spec = MT.TOOL_SPECS["set_speed_ramp"][2]
    assert spec["allow_speech"] == {"type": "boolean"}


@pytest.mark.parametrize("card_id", ["typeset_behind", "recall_shape_chain"])
def test_unproven_devices_ship_labelled_beta(card_id):
    import devices
    assert devices.load_cards()[card_id]["status"] == "beta"


# ── one pack schema, one loader, one verb (Wave 2 + Wave 3 + Wave 4) ────

def test_one_loader_lists_every_genre_in_one_schema():
    import format_packs
    assert formats.IDS == ("A", "B", "C", "D", "E", "F", "G", "H", "I",
                           "J")
    assert tuple(format_packs.CODES) == formats.IDS
    for pid in formats.IDS:
        p = formats.load(pid)
        assert p["id"] == pid
        for beat in p["structure"]:
            assert beat["beat"] and beat["at"] and beat["devices"]
        text = formats.brief(p)
        assert text.startswith(f"FORMAT {pid}") and "black-and-white" in text
        assert format_packs.budgets(pid)["zoom"] is None
        assert format_packs.budgets(pid)["sfx"] is None
    # D's policy fields stay in the pack, never become ledger budgets
    assert formats.load("D")["budgets"]["light_dissolve_max"] == 1
    assert "light_dissolve_max" not in format_packs.budgets("D")
    assert {formats.load(c)["apply"] for c in "EFGIJ"} == {"brief"}
    assert not any(formats.applies_brief(formats.load(c)) for c in "ABCDH")


def test_the_pack_verb_is_one_verb():
    from tools_ext import delivery_tools
    assert "apply_format" not in delivery_tools.TOOL_SPECS
    assert delivery_tools.VERBS == ("deliver_aspects", "retypeset_language")
    spec = format_tools.TOOL_SPECS["apply_format_pack"][2]
    assert spec["aspect"] == {"type": "boolean"}
    assert spec["pack"]["enum"] == ["auto", *formats.IDS, "none"]


def test_users_words_pick_the_later_genres_too():
    assert formats.select(None, None, "make a product explainer")[0][
        "id"] == "E"
    assert formats.select(None, None, "a visual essay to my song")[0][
        "id"] == "F"
    assert formats.select(None, None, "creator essay in my room")[0][
        "id"] == "D"
    assert formats.select(None, None, "an event recap sizzle")[0]["id"] == \
        "G"
    assert formats.select(None, None, "lyric video please")[0]["id"] == "H"
