"""Defects from the Gates (w1022-gates-A_156w0aYtU) and Diamandis
(w1022-diamandis-akXMYvKjUxM) podcast runs, 22-23 Oct 2026.

* audit_motion / critique_short predicted "would render at rung omit:
  matte.person absent" for every matte device (world_negative on 4365,
  room_dims on 4383/4387, colour_kept on 4396) because the browserless audit
  decided the rung with no sidecar status at all, while every render of
  those versions drew the device at rung full.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import w1a_support as sup  # noqa: E402


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    """A synthetic source with a cut at 4.0 s, its person matte measured."""
    import media
    import sidecars
    d = tmp_path_factory.mktemp("gd_matte")
    src = sup.make_source(str(d / "src.mp4"), cut_at=4.0)
    info = media.probe(src)
    sha = media.sha256_file(src)
    ref = sidecars.source_from(sha, src, info, 0, [4.0])
    store = str(d / "store")
    sup.write_chunks(store, ref, cut_at=4.0)
    index = {"video": {"duration": info["duration"], "width": info["width"],
                       "height": info["height"], "fps": info["fps"],
                       "sha256": sha},
             "shots": [{"start": 4.0}]}
    return {"store": store, "index": index, "dir": str(d)}


def _negate(start=1.0, end=2.0):
    return {"id": "rc_world_negative_1", "type": "subject", "kind": "negate",
            "stage": "world", "start": start, "end": end, "scope": "subject",
            "device": "subject_negate"}


def _edl(**kw):
    edl = {"keep": [[0.0, 3.0], [4.5, 8.0]], "fx": [_negate()]}
    edl.update(kw)
    return edl


def test_audit_reads_the_measured_matte_instead_of_predicting_an_omit(measured):
    import fxgraph
    import motion_qa
    import sidecars
    sidecars.use_store(sidecars.LocalStore(measured["store"]))
    try:
        rows = fxgraph.static_report(_edl(), measured["index"])
        assert rows[0]["rung"] == "full", rows
        found = motion_qa.audit(_edl(), measured["index"])["findings"]
        assert not [f for f in found if f["id"] == "rc_world_negative_1"
                    and f["code"] in ("device_degraded", "device_measuring")], found
    finally:
        sidecars.use_store(None)


def test_audit_says_measuring_where_the_matte_is_not_measured_yet(measured, tmp_path):
    import motion_qa
    import sidecars
    sidecars.use_store(sidecars.LocalStore(str(tmp_path / "empty")))
    try:
        found = [f for f in motion_qa.audit(_edl(), measured["index"])["findings"]
                 if f["id"] == "rc_world_negative_1"]
        assert found and found[0]["code"] == "device_measuring", found
        assert "would render at rung omit" not in found[0]["message"]
        assert "a render measures it" in found[0]["message"]
    finally:
        sidecars.use_store(None)


def test_audit_without_a_store_keeps_the_plain_static_rows(measured):
    import fxgraph
    import sidecars
    sidecars.use_store(False)
    try:
        assert fxgraph.static_report(_edl(), measured["index"]) == \
            fxgraph.job_report(_edl(), [])
    finally:
        sidecars.use_store(None)


class _Db:
    def __init__(self, assets):
        self.assets = assets

    def run(self, fn, *args, **kw):
        import db as dbx
        if fn is dbx.latest_asset:
            return self.assets.get(args[1])
        return None


class _Ctx:
    def __init__(self, measured):
        idx = measured["index"]
        self.project_id = 7
        self.index = dict(idx, words=[], sentences=[])
        self.has_main_video = True
        self._orig_sha = idx["video"]["sha256"]
        # no proxy: read-only (nothing is launched from a test)
        self.db = _Db({"original": {"sha256": idx["video"]["sha256"]}})


def test_a_matte_device_write_says_measured_or_impossible(measured):
    import agent_tools
    import sidecars
    sidecars.use_store(sidecars.LocalStore(measured["store"]))
    try:
        ctx = _Ctx(measured)
        notes = agent_tools._matte_device_notes(ctx, {"keep": [[0.0, 8.0]]}, _edl())
        assert len(notes) == 1 and "measured over 1.00-2.00s" in notes[0], notes
        # a window that plays only an insert: no matte can exist there
        over = _edl(inserts=[{"id": "i1", "kind": "image", "asset_key": "a.png",
                              "at_output_s": 3.0, "duration_s": 2.0}],
                    fx=[_negate(3.2, 4.8)])
        notes = agent_tools._matte_device_notes(ctx, {"keep": [[0.0, 8.0]]}, over)
        assert notes and notes[0].startswith("WARNING (person matte)"), notes
        assert "no main footage" in notes[0]
        # an unchanged item says nothing
        assert agent_tools._matte_device_notes(ctx, _edl(), _edl()) == []
    finally:
        sidecars.use_store(None)


# ── the corner mark on a picture-card layout (Gates s04, 4385 v26) ────────

def _card_ctx():
    from schemas import default_edl
    from test_keepout import _Ctx
    edl = default_edl(30.0)
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_x": 0.6}
    edl["effects"] = dict(edl.get("effects") or {})
    edl["effects"]["picture_cards"] = [{
        "id": "c1", "start": 0.0, "end": 30.0, "box": [0.0, 0.24, 1.0, 0.794],
        "fit": "crop", "source": [0.33, 0.0, 0.9, 1.0]}]
    return _Ctx(edl, {"video": {"width": 1920, "height": 1080}}, 30.0)


def _browserless(monkeypatch):
    import motion_tools
    monkeypatch.setattr(motion_tools, "_probe_item",
                        lambda item, W, H, fps=30.0: None)


def test_a_band_graphic_on_the_corner_mark_moves_or_is_warned(monkeypatch):
    import keepout
    import motion_tools
    _browserless(monkeypatch)
    zone = keepout.mark_zone(1080, 1920)
    ctx = _card_ctx()
    # s04's payoff: a word_slam at y 0.215 drew from y 0.065 (estimate 0.0665)
    out = motion_tools.add_motion_graphic(
        ctx, "word_slam", 20.0, 22.0,
        params={"text": "THE *DISTANCE*", "tier": "payoff", "y": 0.215,
                "kicker": "because of"}, id="payoff")
    assert out.startswith("EDL v1"), out
    item = ctx.latest_edl()["json"]["motion"][0]
    box = item["footprint"]["box"]
    moved = "KEEP-OUT (corner mark" in out
    warned = "WARNING (corner mark)" in out
    assert moved or warned, out
    if moved:
        assert not keepout.on_mark(box, zone), (box, zone)


def test_the_counter_in_the_band_is_named_on_the_mark(monkeypatch):
    import keepout
    import motion_tools
    _browserless(monkeypatch)
    zone = keepout.mark_zone(1080, 1920)
    ctx = _card_ctx()
    # s04 'fifteen': a counter at y 0.2 estimated 0.08-0.295, into the mark
    out = motion_tools.add_motion_graphic(
        ctx, "counter", 10.0, 14.0,
        params={"value": "15×", "y": 0.2, "label":
                "as many children die before the age of five"}, id="fifteen")
    item = ctx.latest_edl()["json"]["motion"][0]
    box = item["footprint"]["box"]
    assert ("WARNING (corner mark)" in out) or not keepout.on_mark(box, zone), (out, box)


def test_audit_flags_a_graphic_whose_box_is_on_the_mark():
    import motion_qa
    ar = round(1080 / 1920, 4)
    edl = {"keep": [[0.0, 30.0]], "motion": [
        {"id": "fifteen", "template": "counter", "start": 10.0, "end": 14.0,
         "params": {"value": "15×", "y": 0.2},
         "footprint": {"box": [0.2188, 0.08, 0.7812, 0.2953], "ar": ar,
                       "estimated": True}},
        {"id": "low", "template": "counter", "start": 15.0, "end": 18.0,
         "params": {"value": "2", "y": 0.6},
         "footprint": {"box": [0.3, 0.55, 0.7, 0.65], "ar": ar}}]}
    found = [f for f in motion_qa.graphics_on_mark(edl)]
    assert [f["id"] for f in found] == ["fifteen"], found
    assert "y 0.080 (estimated box)" in found[0]["message"]


# ── the phone-call card (Diamandis s07, 4419 final 95992) ─────────────────

def test_a_call_window_that_ends_at_the_face_is_advice_not_a_blocking_finding():
    import render_qc
    plan = {"cards": [(0.0, 27.83, [[0.0, 0.24, 1.0, 0.79]])], "fps": 30.0}
    res = {"cut": [[6.75, 7.75, "right", 0.056, "card", 1],
                   [20.25, 22.25, "right", 0.058, "card", 1]],
           "clipped": [[0.75, 2.25, "right", 0.0011, 1],
                       [3.25, 13.75, "right", 0.0, 1]]}
    found = render_qc.findings(res, plan)
    assert not [f for f in found if f.startswith("FACE")], found
    adv = render_qc.source_limit_advice(res, plan)
    assert len(adv) == 2, adv
    assert adv[0].startswith("SOURCE LIMIT, FACE AT THE CARD EDGE 6.8-7.8s, 20.2-22.2s")
    assert "not blocking" in adv[0] and "up to 6%" in adv[0]
    assert adv[1].startswith("SOURCE LIMIT, FACE NEAR THE RIGHT EDGE 0.8-2.2s")
    # a face the edit cut (the source has picture past the edge) still blocks
    res["cut"].append([24.0, 25.0, "left", 0.05, "card", 0])
    assert any(f.startswith("FACE CUT BY THE CARD EDGE 24.0-25.0s")
               for f in render_qc.findings(res, plan))
    # a source-limited cut deeper than a tenth of the card still blocks
    deep = {"cut": [[1.0, 2.0, "right", 0.14, "card", 1]]}
    assert render_qc.findings(deep, plan)[0].startswith("FACE CUT BY THE CARD EDGE")
    assert render_qc.source_limit_advice(deep, plan) == []


def test_a_caption_anchored_past_the_safe_foot_keeps_off_the_card_edge():
    """Diamandis s12 (4424 v24): anchor_y 0.89 under a 4:5 card ending at
    0.795 rendered clamped to the template's safe foot (0.80), across the
    card's bottom edge, and no anchor change moved it."""
    import captions
    import caption_place
    import test_caption_place as tcp
    from timeline import Timeline
    card = {"id": "card_elon", "start": 0.0, "end": 9.0,
            "box": [0.092, 0.13, 0.908, 0.795], "fit": "crop",
            "source": [0.3852, 0.195, 0.6148, 0.7864]}
    edl = tcp._edl(cards=[card], look="serif")
    edl["captions"]["style"]["anchor_y"] = 0.89
    ix = tcp._index()
    for smp in ix["spatial"]["samples"]:
        smp["faces"] = [[0.40, 0.25, 0.70, 0.62]]
    p = captions.caption_plan(edl, ix, Timeline(edl["keep"]), canvas=(1080, 1920))
    assert p.placed, "the clamped block crosses the card's foot: it must be placed"
    for pl in p.placed.values():
        z0, z1 = pl["z"]
        assert z1 <= 0.795 - caption_place.EDGE_PAD + 1e-6 or z0 >= 0.795 + caption_place.EDGE_PAD


def test_a_motion_upsert_patches_params_key_by_key():
    """Gates run: an upsert with params={'y': ...} dropped the text, tier
    and kicker of a word_slam (the docs say an upsert patches by id)."""
    import edit_batch
    base = {"keep": [[0.0, 10.0]], "motion": [{
        "id": "hook", "template": "word_slam", "start": 0.0, "end": 2.0,
        "params": {"text": "ONE *UNIFORM* THING", "tier": "hook", "y": 0.64}}]}
    out = edit_batch.apply_batch(base, [{"action": "upsert", "layer": "motion",
                                         "id": "hook", "value": {"params": {"y": 0.7}}}],
                                 10.0, set())
    params = out["motion"][0]["params"]
    assert params["y"] == 0.7 and params["text"] == "ONE *UNIFORM* THING"
    assert params["tier"] == "hook"
    whole = edit_batch.apply_batch(base, [{"action": "upsert", "layer": "motion",
                                           "id": "hook", "value": {
                                               "params": {"text": "NEW"},
                                               "replace_params": True}}], 10.0, set())
    # replace_params: the old set goes (its tier falls back to the default)
    assert whole["motion"][0]["params"]["text"] == "NEW"
    assert whole["motion"][0]["params"].get("tier") != "hook"
    assert "replace_params" not in whole["motion"][0]
