"""Track 1C: the compositing department's verbs and the stale-client path.

  * every verb SOLVES its arguments into apply_edit_batch operations and
    commits through tools_ext._write.commit, so whatever a verb writes, an
    MCP client that never saw the verb writes (and removes) through
    apply_edit_batch, validated identically (INTERFACES_W1.md §5);
  * every authoring rule is in fx_schemas/composite.py: a verb never refuses
    what a batch accepts, and a batch refuses what a verb refuses;
  * the department stays inside its compact share (1C <= 3,500 bytes) and
    every full description inside 900 characters;
  * the cards name live verbs and owner-safe flags; no card adds a zoom or a
    sound.
"""

import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import agent_tools                                     # noqa: E402
import devices                                         # noqa: E402
import edit_batch                                      # noqa: E402
import schemas                                         # noqa: E402
import tools_ext                                       # noqa: E402
from fx_schemas import composite as member             # noqa: E402
from tools_ext import composite_tools as ct            # noqa: E402

DUR = 30.0
ASSET = "media/p1/broll.mp4"


def _base(motion=True, inserts=False):
    edl = {"keep": [[0.0, 10.0], [12.0, 20.0]],
           "captions": {"enabled": False}}
    if motion:
        edl["motion"] = [
            {"id": "m1", "template": "word_slam", "start": 2.0, "end": 4.0,
             "params": {"text": "HELLO"}},
            {"id": "lk", "template": "light_leak", "start": 9.5, "end": 10.7,
             "params": {}}]
    if inserts:
        edl["inserts"] = [{"id": "b1", "kind": "video", "asset_key": ASSET,
                           "at_output_s": 10.0, "duration_s": 3.0,
                           "source_start_s": 2.0}]
    return schemas.validate_edl(edl, DUR).model_dump()


class _DB:
    def run(self, fn, *a):
        return {ASSET: {"storage_key": ASSET, "duration_s": 20.0}}


class Ctx:
    """The parts of ToolContext the department verbs use."""

    def __init__(self, edl):
        self.db, self.project_id, self.duration = _DB(), 1, DUR
        self.version, self.edl, self.lines = 3, edl, []

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self.edl)}

    def write_edl(self, edl, desc):
        self.edl = schemas.validate_edl(edl, DUR).model_dump()
        self.version += 1
        self.lines.append(desc)
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


@pytest.fixture(autouse=True)
def _no_executor(monkeypatch):
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: True)


def test_the_module_is_live_and_registers_its_pinned_types():
    assert member.LIVE is True
    assert "region" in schemas.FX_TYPES
    assert "MotionItem.composite" in schemas._FX_SLOT_TYPES
    assert set(ct.TOOL_SPECS) == set(ct.VERBS)
    for verb in ct.VERBS:
        assert verb in agent_tools.TOOLS
        assert verb in agent_tools.TOOL_DOMAINS["compositing"]
    assert set(ct.WRITE_TOOLS) == set(ct.VERBS)


def test_department_share_and_description_caps():
    specs = agent_tools.openai_tools("any-model", compact=True,
                                     names=list(ct.TOOL_SPECS))
    size = len(json.dumps(specs, separators=(",", ":")))
    assert size <= 3500, size
    for name, (_fn, desc, _props) in ct.TOOL_SPECS.items():
        assert len(desc) <= 900, (name, len(desc))
    full = agent_tools.openai_tools("any-model", compact=False,
                                    names=list(ct.TOOL_SPECS))
    assert all(f["function"]["description"] for f in full)


def test_set_composite_merges_and_clears_through_apply_batch():
    ctx = Ctx(_base())
    out = ct.set_composite(ctx, "m1", blend="difference")
    assert out.startswith("EDL v3 -> v4"), out
    m = [i for i in ctx.edl["motion"] if i["id"] == "m1"][0]
    assert m["composite"]["blend"] == "difference"
    # merges: a later ghost keeps the blend (and notes the layer rule)
    out = ct.set_composite(ctx, "m1", ghost="auto")
    m = [i for i in ctx.edl["motion"] if i["id"] == "m1"][0]
    assert m["composite"]["blend"] == "difference"
    assert m["composite"]["ghost"] == "auto"
    assert "ghost draws only on a behind_subject graphic" in out
    out = ct.set_composite(ctx, "m1", clear=True)
    m = [i for i in ctx.edl["motion"] if i["id"] == "m1"][0]
    assert m["composite"] is None
    sig = schemas.edl_signature(_base())
    assert schemas.edl_signature(ctx.edl) == sig
    assert ct.set_composite(ctx, "m1", clear=True).startswith("NO CHANGE")
    assert ct.set_composite(ctx, "nope", blend="screen").startswith(
        "REJECTED: no motion graphic 'nope'")


def test_light_templates_take_their_spec_light_blend():
    ctx = Ctx(_base())
    out = ct.set_composite(ctx, "lk")
    assert out.startswith("EDL"), out
    lk = [i for i in ctx.edl["motion"] if i["id"] == "lk"][0]
    assert lk["composite"]["blend"] == "screen"
    import motion_templates
    assert motion_templates.spec("flash_transition")["composite"] == {
        "blend": "add"}
    assert motion_templates.spec("film_burn")["composite"] == {
        "blend": "screen"}


def test_add_type_window_writes_one_item_with_its_fill():
    ctx = Ctx(_base(inserts=True))
    out = ct.add_type_window(ctx, "SOUNDS", "next", reveal="open",
                             start=8.8, end=10.0, field="#c8102e")
    assert out.startswith("EDL"), out
    tw = [i for i in ctx.edl["motion"] if i["template"] == "type_window"][0]
    assert tw["composite"]["fill"]["kind"] == "next"
    assert tw["composite"]["field"] == "#c8102e"
    assert tw["params"]["reveal"] == "open"
    out = ct.add_type_window(ctx, "YOU SHOULD KNOW I", ASSET + "+loop",
                             start=1.0, end=4.0, field="black")
    assert out.startswith("EDL"), out
    tw2 = [i for i in ctx.edl["motion"] if i["id"] == "tw2"][0]
    assert tw2["composite"]["fill"] == {
        "kind": "asset", "offset_s": 0.0, "src_t": None, "asset_key": ASSET,
        "colour": None, "loop": True}
    # a media key not attached to the project is refused like a batch's
    out = ct.add_type_window(ctx, "NO", "media/other/x.mp4", start=1, end=3)
    assert out.startswith("REJECTED") and "attached" in out


def test_add_region_effect_and_remove():
    ctx = Ctx(_base())
    out = ct.add_region_effect(ctx, "solarise", "ellipse:0.3,0.2,0.7,0.5",
                               1.0, 3.0, colour="#ff00ff")
    assert out.startswith("EDL"), out
    rg = ctx.edl["fx"][0]
    assert rg["type"] == "region" and rg["device"] == "region_tone"
    assert rg["stage"] == "pre_captions"
    out = ct.add_region_effect(ctx, "refraction", "glyph:m1", 2.0, 4.0)
    assert out.startswith("EDL"), out
    g = [i for i in ctx.edl["fx"] if i["effect"] == "refraction"][0]
    assert g["stage"] == "over_type" and g["device"] == "region_material"
    out = ct.add_region_effect(ctx, "blur", "face", 5.0, 6.0)
    assert out.startswith("EDL") and "face region draws once" in out
    # a glyph region naming a graphic that is not there: the member's check
    out = ct.add_region_effect(ctx, "negate", "glyph:zz", 1.0, 2.0)
    assert out.startswith("REJECTED") and "not in this edit" in out
    out = ct.remove_region_effect(ctx, ["rg1", "rg2"])
    assert out.startswith("EDL"), out
    assert [i["id"] for i in ctx.edl["fx"]] == ["rg3"]
    assert ct.remove_region_effect(ctx, "m1").startswith("REJECTED")


def test_flash_safety_refuses_a_fourth_flash_in_a_second():
    ctx = Ctx(_base())
    for k in range(3):
        out = ct.add_region_effect(ctx, "negate", "rect:0,0,1,1",
                                   1.0 + 0.25 * k, 1.1 + 0.25 * k)
        assert out.startswith("EDL"), out
    out = ct.add_region_effect(ctx, "negate", "rect:0,0,1,1", 1.8, 1.9)
    assert out.startswith("REJECTED") and "flashes in one second" in out
    # a small region is no flash
    out = ct.add_region_effect(ctx, "negate", "rect:0.4,0.4,0.5,0.5",
                               1.8, 1.9)
    assert out.startswith("EDL"), out


def test_executor_skew_refuses_writes(monkeypatch):
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: False)
    ctx = Ctx(_base())
    assert "older version" in ct.set_composite(ctx, "m1", blend="screen")
    assert "older version" in ct.add_region_effect(ctx, "mono", "card",
                                                   1, 2)
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: None)
    assert ct.set_composite(ctx, "m1", blend="screen").startswith("EDL")


@pytest.mark.parametrize("value,expect", [
    ("next", {"kind": "next"}), ("program:-1.5", {"kind": "program",
                                                 "offset_s": -1.5}),
    ("#FF00AA", {"kind": "colour", "colour": "#ff00aa"}),
    ("black", {"kind": "colour", "colour": "black"}),
    ("source:12.5", {"kind": "source", "src_t": 12.5}),
    ("asset:media/a.png", {"kind": "asset", "asset_key": "media/a.png"}),
    ("media/a.mp4+loop", {"kind": "asset", "asset_key": "media/a.mp4",
                          "loop": True})])
def test_source_shorthands(value, expect):
    assert ct.parse_source(value) == expect


def test_region_shorthands():
    assert ct.parse_region("face") == {"shape": "face"}
    assert ct.parse_region("face:1") == {"shape": "face", "ref": "1"}
    assert ct.parse_region("glyph:m1") == {"shape": "glyph", "ref": "m1"}
    assert ct.parse_region("rect:0.1, 0.2,0.3,0.4") == {
        "shape": "rect", "box": [0.1, 0.2, 0.3, 0.4]}
    with pytest.raises(ValueError):
        ct.parse_region("blob")


# ── the stale-client path: the very items, through apply_edit_batch ─────

OPS_OK = [
    [{"action": "upsert", "layer": "motion", "id": "m1", "value": {
        "id": "m1", "composite": {"blend": "auto", "opacity": 0.8}}}],
    [{"action": "upsert", "layer": "motion", "id": "m1", "value": {
        "id": "m1", "composite": {"fill": {"kind": "program",
                                           "offset_s": -1.0},
                                  "field": "black"}}}],
    [{"action": "upsert", "layer": "motion", "id": "tw9", "value": {
        "id": "tw9", "template": "type_window", "start": 1.0, "end": 4.0,
        "layer": "above_captions",
        "params": {"text": "YOU SHOULD KNOW I", "reveal": "words_open"},
        "composite": {"fill": {"kind": "asset", "asset_key": ASSET,
                               "loop": True}, "field": "black"}}}],
    [{"action": "upsert", "layer": "fx", "id": "rgk", "value": {
        "id": "rgk", "type": "region", "stage": "world", "start": 1.0,
        "end": 3.0, "effect": "solarise", "colour": "#ff00ff",
        "device": "region_tone", "region": {"shape": "polygon", "keys": [
            {"t": 0, "points": [[0.3, 0.4], [0.7, 0.4], [0.5, 0.7]]},
            {"t": 1.5, "points": [[0.2, 0.3], [0.8, 0.35], [0.5, 0.8]],
             "ease": "in_out"}]}}}],
    [{"action": "upsert", "layer": "fx", "id": "rgm", "value": {
        "id": "rgm", "type": "region", "stage": "pre_captions",
        "start": 5.0, "end": 6.0, "effect": "heat", "scale": 1.5,
        "device": "region_material",
        "region": {"shape": "rect", "box": [0, 0.5, 1, 1]}}}],
]

OPS_BAD = [
    ([{"action": "upsert", "layer": "motion", "id": "m1", "value": {
        "id": "m1", "composite": {"fill": {"kind": "program",
                                           "offset_s": 5}}}}],
     "at most 2 s away"),
    ([{"action": "upsert", "layer": "motion", "id": "m1", "value": {
        "id": "m1", "composite": {"fill": {"kind": "colour",
                                           "colour": "black"},
                                  "fill_mode": "outside",
                                  "field": "white"}}}],
     "field belongs to fill_mode inside"),
    ([{"action": "upsert", "layer": "motion", "id": "m1", "value": {
        "id": "m1", "composite": {"fill": {"kind": "next", "offset_s": 1}}}}],
     "takes no offset_s"),
    ([{"action": "upsert", "layer": "fx", "id": "r", "value": {
        "id": "r", "type": "region", "stage": "world", "start": 1.0,
        "end": 2.0, "effect": "blur", "colour": "#ffffff",
        "region": {"shape": "rect", "box": [0, 0, 1, 1]}}}],
     "colour belongs to tint or solarise"),
    ([{"action": "upsert", "layer": "fx", "id": "r", "value": {
        "id": "r", "type": "region", "stage": "junction", "at": 10.0,
        "effect": "blur", "region": {"shape": "rect", "box": [0, 0, 1, 1]}}}],
     "not junction"),
]


@pytest.mark.parametrize("ops", OPS_OK)
def test_stale_client_writes_and_removes_every_1c_shape(ops):
    base = _base()
    keys = {ASSET: {"storage_key": ASSET, "duration_s": 20.0}}
    out = edit_batch.apply_batch(base, ops, DUR, keys)
    op = ops[0]
    if op["layer"] == "fx":
        assert op["id"] in [i["id"] for i in out["fx"]]
        undo = [{"action": "remove", "layer": "fx", "id": op["id"]}]
    elif op["id"] == "m1":
        assert out["motion"][[m["id"] for m in out["motion"]].index("m1")][
            "composite"]
        undo = [{"action": "upsert", "layer": "motion", "id": "m1",
                 "value": {"id": "m1", "composite": None}}]
    else:
        undo = [{"action": "remove", "layer": "motion", "id": op["id"]}]
    back = edit_batch.apply_batch(out, undo, DUR, keys)
    assert schemas.edl_signature(back) == schemas.edl_signature(base)


@pytest.mark.parametrize("ops,match", OPS_BAD)
def test_stale_client_bad_shapes_are_refused(ops, match):
    import re
    with pytest.raises(Exception) as e:
        edit_batch.apply_batch(_base(), ops, DUR, {})
    assert re.search(match, str(e.value)), str(e.value)


def test_the_device_gate_judges_region_cards():
    """A region item may name only region cards (check_fx_devices)."""
    base = _base()
    ok = [{"action": "upsert", "layer": "fx", "id": "r", "value": {
        "id": "r", "type": "region", "stage": "world", "start": 1.0,
        "end": 2.0, "effect": "negate", "device": "region_tone",
        "region": {"shape": "rect", "box": [0.4, 0.4, 0.6, 0.6]}}}]
    edit_batch.apply_batch(base, ok, DUR, {})
    bad = copy.deepcopy(ok)
    bad[0]["value"]["device"] = "composite_blend"
    with pytest.raises(Exception, match="region_\\* card"):
        edit_batch.apply_batch(base, bad, DUR, {})


# ── cards ───────────────────────────────────────────────────────────────

def test_1c_cards_are_owned_live_and_owner_safe():
    cards = {k: v for k, v in devices.load_cards().items()
             if k.split("_", 1)[0] in ("composite", "region")}
    assert set(cards) == {"composite_blend", "composite_fill",
                          "composite_window", "composite_ghost",
                          "composite_light", "region_tone", "region_soften",
                          "region_material"}
    for cid, c in cards.items():
        assert devices.CARD_PREFIXES[cid.split("_", 1)[0]] == "1C"
        assert not c["owner_flags"]["adds_zoom"], cid
        assert not c["owner_flags"]["adds_sound"], cid
        assert c["verb"] in ct.VERBS, cid
        assert c["skill"] == "compositing"
        if cid.startswith("region_"):
            assert c["fx_type"] == "region"
            for k in c["params"]:
                assert k in member.RegionFx.model_fields, (cid, k)
        else:
            assert c.get("fx_type") is None
            for k in c["params"]:
                assert k in member.MotionComposite.model_fields, (cid, k)
        assert devices.proposable(c)[0], cid
    # the material degrades to the soften card, then nothing
    d = devices.resolve_detail("region_material", lane="interactive",
                               sidecar_status={}, budget_ms=10.0, frames=60)
    assert d["rung"] in ("substitute", "omit")
    if d["rung"] == "substitute":
        assert d["substitute"] == "region_soften"


def test_region_flash_times():
    def item(**kw):
        d = dict(id="r", type="region", stage="world", start=1.0, end=1.2,
                 effect="negate", region={"shape": "rect",
                                          "box": [0, 0, 1, 1]})
        d.update(kw)
        return member.RegionFx.model_validate(d)
    assert item().flash_times() == [1.0]
    assert item(end=2.0).flash_times() == []            # a held negative
    assert item(region={"shape": "rect", "box": [0.4, 0.4, 0.6, 0.6]}
                ).flash_times() == []                   # a small region
    assert item(region={"shape": "rect", "box": [0.4, 0.4, 0.6, 0.6],
                        "invert": True}).flash_times() == [1.0]
    assert item(effect="blur").flash_times() == []
    assert item(effect="exposure", ev=0.5).flash_times() == []
    assert item(effect="exposure", ev=-1.5).flash_times() == [1.0]
    assert item(strength=0.3).flash_times() == []
    assert item(region={"shape": "glyph", "ref": "m1"}).flash_times() == []
    assert item(region={"shape": "card"}).flash_times() == [1.0]
