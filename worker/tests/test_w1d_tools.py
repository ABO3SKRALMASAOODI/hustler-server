"""Track 1D's department verbs (``rhythm``): add_accent, add_strobe,
set_junction, remove_punctuation (INTERFACES_W1.md §4).

Every verb solves its arguments into apply_edit_batch operations and
commits them through ``tools_ext._write.commit`` (edit_batch.apply_batch):
a verb and a stale client's batch are validated identically. These tests
run the verbs on a fake tool context (no database, no proxy) and pin the
owner rules they hold: moments come from the speaker or the user's own
track (never music added for a clock), flash safety thins automatic
placements unless the user asked for strobing, junctions land only on real
cuts, one device per cut.
"""

import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import agent_tools                                         # noqa: E402
import schemas                                             # noqa: E402
import tools_ext                                           # noqa: E402
from tools_ext import punctuation_tools as P               # noqa: E402

WORDS = [{"w": w, "t0": round(0.4 + 0.42 * i, 2),
          "t1": round(0.4 + 0.42 * i + 0.34, 2), "p": 0.95}
         for i, w in enumerate(
             ("they promised us flying cars and instead we got one hundred "
              "forty characters which is not nothing but it is not the "
              "future we were promised at all").split())]


class _DB:
    def run(self, fn, *a):
        return {"media/p1/a.png": {"duration_s": None},
                "media/p1/b.png": {"duration_s": None}}


class Ctx:
    def __init__(self, edl, user_message="", words=WORDS):
        self.db, self.project_id, self.duration = _DB(), 1, 20.0
        self.index = {"words": words, "video": {"fps": 25.0,
                                                "duration": 20.0}}
        self.user_message = user_message
        self._asset_perception = {}
        self.version = 3
        self.edl = schemas.validate_edl(copy.deepcopy(edl),
                                        20.0).model_dump()
        self.written = []

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self.edl)}

    def write_edl(self, edl, desc):
        self.edl = edl
        self.written.append(desc)
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"

    def proxy_path(self):
        raise RuntimeError("no proxy in tests")


@pytest.fixture(autouse=True)
def _no_perception(monkeypatch):
    def nope(ctx):
        raise RuntimeError("no audio analysis in tests")
    monkeypatch.setattr(agent_tools, "_get_perception", nope)


def _fx(ctx, t=None):
    return [i for i in ctx.edl.get("fx") or []
            if t is None or i["type"] == t]


TALK = {"keep": [[0.0, 20.0]]}
CUTS = {"keep": [[0.0, 5.0], [6.0, 10.0], [10.0, 14.0]]}


def test_the_department_is_installed_with_its_pinned_verbs():
    assert set(tools_ext.EXT_DEPARTMENTS["rhythm"]) == set(P.VERBS)
    for v in P.VERBS:
        assert v in agent_tools.TOOLS and v in agent_tools.WRITE_TOOLS
        assert len(agent_tools.TOOLS[v][1]) <= 900


def test_add_accent_at_writes_frame_exact_device_items():
    ctx = Ctx(TALK)
    out = P.add_accent(ctx, "negative", at=[2.0, 5.48], scope="all")
    assert out.startswith("EDL v3 -> v4"), out
    items = _fx(ctx, "accent")
    assert [(i["start"], i["end"]) for i in items] == [(2.0, 2.04),
                                                       (5.48, 5.52)]
    assert all(i["stage"] == "over_type" and i["device"] ==
               "accent_negative" and i["bind"]["on"] == "time"
               for i in items)
    assert "ids:" in out


def test_on_stress_lands_on_word_onsets_with_their_binding():
    ctx = Ctx(TALK)
    out = P.add_accent(ctx, "white_hit", on="stress", count=3)
    assert out.startswith("EDL"), out
    items = _fx(ctx, "accent")
    assert 1 <= len(items) <= 3
    onsets = {round(w["t0"], 2) for w in WORDS}
    for it in items:
        assert it["bind"]["on"] == "stress" and it["bind"]["rhythm"] == \
            "speech"
        w = WORDS[it["bind"]["ref"]]
        import fxprims
        # on the word onset's own frame
        assert fxprims.frame_at(it["start"], 25.0) == \
            fxprims.frame_at(w["t0"], 25.0)
        assert round(w["t0"], 2) in onsets
        assert it["decay_f"] == 5 and it["kind"] == "white_hit"
    gaps = [b["start"] - a["start"] for a, b in zip(items, items[1:])]
    assert all(g >= P.SPARSE_GAP_S - 1e-6 for g in gaps)


def test_accelerate_spacing_shrinks_monotonically_into_the_payoff():
    ctx = Ctx(TALK, user_message="flash it on every word, strobe it")
    out = P.add_accent(ctx, "negative", on="word", density="accelerate",
                       start=2.0, end=14.0, count=8)
    assert out.startswith("EDL"), out
    t = [i["start"] for i in _fx(ctx, "accent")]
    assert len(t) >= 4
    gaps = [b - a for a, b in zip(t, t[1:])]
    assert all(g2 < g1 for g1, g2 in zip(gaps, gaps[1:])), gaps


def test_flash_safety_thins_automatic_hits_and_reports_it():
    ctx = Ctx(TALK)
    hits = [5.0, 5.12, 5.24, 5.36, 5.48, 5.6]
    out = P.add_accent(ctx, "negative", at=hits)
    assert out.startswith("EDL") and "flash safety held 3 back" in out, out
    assert len(_fx(ctx, "accent")) == 3
    import flash_safety
    assert flash_safety.assess(ctx.edl)["over_limit"] is False


def test_the_users_ask_for_strobing_keeps_every_hit_and_flags_it():
    ctx = Ctx(TALK, user_message="make it strobe on those words")
    hits = [5.0, 5.12, 5.24, 5.36, 5.48, 5.6]
    out = P.add_accent(ctx, "negative", at=hits)
    assert len(_fx(ctx, "accent")) == 6 and "flagged" in out
    import flash_safety
    got = flash_safety.assess(ctx.edl, request_text=ctx.user_message)
    assert got["over_limit"] and got["contains_flashing"]


def test_local_hits_are_not_flashes():
    ctx = Ctx(TALK)
    hits = [5.0, 5.12, 5.24, 5.36, 5.48, 5.6]
    region = {"shape": "rect", "box": [0.4, 0.4, 0.6, 0.6]}
    P.add_accent(ctx, "negative", at=hits, region=region)
    assert len(_fx(ctx, "accent")) == 6


@pytest.mark.parametrize("region", [
    {"shape": "face"}, {"shape": "rect", "box": [0.1, 0.1, 0.9, 0.7]}])
def test_hits_the_export_counts_as_flashes_are_thinned_like_them(region):
    """A face (solved at render) or a large box: the export gate counts
    those hits as full-frame flashes (AccentFx.flash_times), so the verb
    thins them by the same reading — never writes what the gate flags."""
    ctx = Ctx(TALK)
    hits = [5.0, 5.12, 5.24, 5.36, 5.48, 5.6]
    out = P.add_accent(ctx, "negative", at=hits, region=region)
    assert "flash safety held 3 back" in out, out
    import flash_safety
    assert flash_safety.assess(ctx.edl)["over_limit"] is False


def test_beats_need_a_track_the_user_supplied():
    out = P.add_accent(Ctx(TALK), "negative", on="beat")
    assert out.startswith("REJECTED") and "Never add music" in out
    out = P.add_accent(Ctx(TALK), "negative", on="word", density="8ths")
    assert out.startswith("REJECTED") and "beat grid" in out


def test_matte_flash_needs_subject_effects():
    out = P.add_accent(Ctx(TALK), "matte_flash", at=2.0)
    if "subject" in schemas.FX_TYPES:
        assert out.startswith("EDL"), out
    else:
        assert out.startswith("REJECTED") and "white_hit" in out


def test_colour_hit_card_region_and_bad_arguments():
    ctx = Ctx(TALK)
    out = P.add_accent(ctx, "colour_hit", at=3.0, colour="#ff2020",
                       region="card")
    assert out.startswith("EDL"), out
    it = _fx(ctx, "accent")[0]
    assert it["colour"] == "#ff2020" and it["region"] == {
        "shape": "card", "box": None, "points": None, "keys": None,
        "ref": None, "invert": False, "feather_px": 0.0}
    assert it["decay_f"] == 2 and it["end"] == 3.08
    assert P.add_accent(ctx, "sparkle", at=1.0).startswith("REJECTED")
    assert P.add_accent(ctx, "negative", at=99.0).startswith("REJECTED")
    assert P.add_accent(ctx, "negative", at=1.0,
                        scope="type").startswith("REJECTED")
    assert P.add_accent(Ctx(TALK, words=[]),
                        "negative").startswith("REJECTED")


def test_strobe_needs_the_users_ask_above_three_changes_a_second():
    frames = ["media/p1/a.png", "media/p1/b.png", "src:3.0", "program"]
    out = P.add_strobe(Ctx(TALK), 2.0, 2.8, frames=frames, hold=2)
    assert out.startswith("REJECTED") and "Flash safety" in out
    ctx = Ctx(TALK, user_message="strobe the product shots")
    out = P.add_strobe(ctx, 2.0, 2.8, frames=frames, hold=2,
                       negate="alternate", fit="register")
    assert out.startswith("EDL"), out
    st = _fx(ctx, "strobe")[0]
    assert [f.get("negate", False) for f in st["frames"]] == \
        [False, True, False, True]
    assert st["frames"][2]["source"] == {
        "kind": "source", "offset_s": 0.0, "src_t": 3.0, "asset_key": None,
        "colour": None, "loop": False}
    assert st["device"] == "strobe_frames" and st["fit"] == "register"
    # three images held 8 frames in under a second: 3 changes, allowed
    slow = Ctx(TALK)
    assert P.add_strobe(slow, 2.0, 2.8, frames=frames[:3],
                        hold=8).startswith("EDL")


def test_strobe_refusals():
    ctx = Ctx(TALK)
    assert P.add_strobe(ctx, 2.0, 2.4, variant_set={"t": 3.0, "kinds": [
        "thermal"]}).startswith("REJECTED")
    assert P.add_strobe(ctx, 2.0, 2.4, frames=["white"],
                        as_insert=True).startswith("REJECTED")
    assert P.add_strobe(ctx, 2.0, 2.02, frames=["white"],
                        hold=4).startswith("REJECTED")
    out = P.add_strobe(ctx, 2.0, 2.4, frames=["media/p9/x.png"], hold=8)
    assert out.startswith("REJECTED") and "attached" in out


def test_set_junction_snaps_to_real_cuts_only():
    ctx = Ctx(CUTS)
    out = P.set_junction(ctx, "iris", at=5.02, origin="face")
    assert out.startswith("EDL"), out
    j = _fx(ctx, "junction")[0]
    assert j["at"] == 5.0 and j["origin"]["kind"] == "face"
    assert j["device"] == "junction_iris" and j["bind"] == {
        "on": "cut", "ref": 6.0, "rhythm": None, "offset_f": 0}
    out = P.set_junction(ctx, "iris", at=9.0)
    assert out.startswith("REJECTED") and "5.00" in out


def test_one_device_per_cut_and_style_none_removes():
    ctx = Ctx(CUTS)
    P.set_junction(ctx, "iris", at=5.0)
    out = P.set_junction(ctx, "box_steps", at=5.0)
    assert "replaced jn1" in out
    js = _fx(ctx, "junction")
    assert len(js) == 1 and js[0]["style"] == "box_steps"
    assert js[0]["duration_s"] == 0.8
    out = P.set_junction(ctx, "none", at=5.0)
    assert out.startswith("EDL") and _fx(ctx, "junction") == []
    assert P.set_junction(ctx, "none", at=5.0).startswith("NO CHANGE")


def test_scene_cuts_skip_jump_cuts_inside_one_shot():
    edl = {"keep": [[0.0, 4.0], [4.5, 8.0], [9.0, 12.0]]}
    ctx = Ctx(edl)
    ctx.index["shots"] = [{"id": 0, "start": 0.0, "end": 8.2},
                          {"id": 1, "start": 8.2, "end": 20.0}]
    out = P.set_junction(ctx, "dissolve")
    assert out.startswith("EDL"), out
    ats = [j["at"] for j in _fx(ctx, "junction")]
    assert ats == [7.5]                 # the shot change, not the jump cut
    ctx2 = Ctx(edl)
    ctx2.index["shots"] = list(ctx.index["shots"])
    P.set_junction(ctx2, "dissolve", cuts="every")
    assert [j["at"] for j in _fx(ctx2, "junction")] == [4.0, 7.5]


def test_set_junction_origins_and_dark_datamosh():
    edl = dict(CUTS, motion=[{"id": "m1", "template": "word_slam",
                              "start": 4.0, "end": 6.0,
                              "params": {"text": "NEXT"}}])
    ctx = Ctx(edl)
    out = P.set_junction(ctx, "letter_window", at=5.0, origin="m1")
    assert out.startswith("EDL"), out
    assert _fx(ctx, "junction")[0]["origin"] == {"kind": "glyph",
                                                 "ref": "m1",
                                                 "point": None}
    out = P.set_junction(ctx, "letter_window", at=5.0)
    assert out.startswith("REJECTED") and "names its letters" in out
    out = P.set_junction(ctx, "pixel_stretch", at=5.0, origin="point:0.5,0.3")
    assert out.startswith("EDL")
    out = P.set_junction(ctx, "datamosh", at=5.0)
    assert out.startswith("REJECTED") and "dark" in out
    assert P.set_junction(ctx, "sparkle", at=5.0).startswith("REJECTED")


def test_hit_flash_junctions_pass_flash_safety():
    edl = {"keep": [[0, 1], [2, 2.3], [3, 3.3], [4, 4.3], [5, 5.3],
                    [6, 9]]}
    ctx = Ctx(edl)
    out = P.set_junction(ctx, "hit_flash", cuts="every")
    assert out.startswith("EDL"), out
    import flash_safety
    assert flash_safety.assess(ctx.edl)["over_limit"] is False


def test_a_second_write_is_thinned_against_the_flashes_already_there():
    """The export gate counts every flash the edit plays; a later write is
    thinned against the ones already written, not only against itself."""
    import flash_safety
    ctx = Ctx(TALK)
    assert P.add_accent(ctx, "negative", at=[5.0, 5.2, 5.4]).startswith(
        "EDL")
    out = P.add_accent(ctx, "white_hit", at=[5.6, 5.8])
    assert out.startswith("REJECTED") and "flash safety" in out, out
    out = P.add_accent(ctx, "white_hit", at=[5.6, 6.5])
    assert "flash safety held 1 back" in out, out
    assert flash_safety.assess(ctx.edl)["over_limit"] is False
    # box steps flash on each large step; with a hit trio right at the cut
    # the junction is held back and says so
    ctx = Ctx(CUTS)
    P.add_accent(ctx, "negative", at=[4.7, 4.85])
    out = P.set_junction(ctx, "box_steps", at=5.0)
    assert out.startswith("REJECTED") and "flash" in out, out
    assert flash_safety.assess(ctx.edl)["over_limit"] is False
    # the user's ask keeps it (the export is flagged)
    ctx.user_message = "flash it hard on the cut, strobe it"
    assert P.set_junction(ctx, "box_steps", at=5.0).startswith("EDL")


def test_remove_punctuation():
    ctx = Ctx(CUTS)
    P.add_accent(ctx, "negative", at=[1.0, 3.0])
    P.set_junction(ctx, "iris", at=5.0)
    ids = [i["id"] for i in _fx(ctx)]
    out = P.remove_punctuation(ctx, ids[:1])
    assert out.startswith("EDL") and len(_fx(ctx)) == 2
    assert P.remove_punctuation(ctx, ["nope"]).startswith("REJECTED")
    assert P.remove_punctuation(ctx, "all").startswith("EDL")
    assert _fx(ctx) == []
    assert P.remove_punctuation(ctx, "all").startswith("NO CHANGE")


def test_a_keep_change_drops_junctions_whose_cut_is_gone():
    import hooks
    # built into hooks.on_keep_change (the backend's keep path runs it too)
    assert P._drop_orphan_junctions is hooks._drop_orphan_junctions
    old = schemas.validate_edl(dict(CUTS, fx=[
        {"id": "j1", "type": "junction", "stage": "junction", "at": 5.0,
         "style": "iris"}]), 20.0).model_dump()
    new = copy.deepcopy(old)
    new["keep"] = [[0.0, 14.0]]                      # the cut is gone
    edl, notes = hooks.on_keep_change(old, new)
    assert edl["fx"] == [] and any("j1" in n for n in notes)
    edl, notes = hooks.on_keep_change(old, copy.deepcopy(old))
    assert [i["id"] for i in edl["fx"]] == ["j1"]


def test_verb_ops_and_a_stale_clients_batch_are_the_same_items():
    """What a verb wrote, an apply_edit_batch carrying the same item
    accepts unchanged (and refuses the same way)."""
    import edit_batch
    ctx = Ctx(CUTS)
    P.set_junction(ctx, "columns", at=5.0, count=6, direction="left")
    item = _fx(ctx, "junction")[0]
    fresh = schemas.validate_edl(copy.deepcopy(CUTS), 20.0).model_dump()
    op = {"action": "upsert", "layer": "fx", "id": item["id"],
          "value": {k: v for k, v in item.items() if v is not None}}
    out = edit_batch.apply_batch(fresh, [op], 20.0, set())
    assert out["fx"][0] == item
