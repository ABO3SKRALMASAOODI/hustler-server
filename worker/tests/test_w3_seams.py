"""Wave 3 seams (devices/INTERFACES_W3.md): what Wave 3 adds is exactly
the pinned table, reachable by a stale client, inert for everything else.

  * the registry: tags, the anchor slot, the sidecar kinds through the
    ``sidecar_kinds`` seam, the pinned models (licences, sha, images);
  * the catalog: the ``world`` department inside its budget, every full
    description under 900 characters, the core catalog under the ceiling;
  * the cards: live verbs, registered types, owner-safe flags, skills;
  * tool reach: every Wave 3 item written AND removed through
    apply_edit_batch (edit_batch.apply_batch, the backend's own path).
"""

import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import agent_tools                                     # noqa: E402
import devices                                         # noqa: E402
import edit_batch                                      # noqa: E402
import schemas                                         # noqa: E402
import sidecars                                        # noqa: E402
import tools_ext                                       # noqa: E402
from w3_pins import (W3_DEPARTMENTS, W3_FX_TAGS,       # noqa: E402
                     W3_SIDECAR_KINDS, W3_VERBS)

DUR = 30.0
STALE_OPS = [
    {"action": "upsert", "layer": "fx", "id": "tr1", "value": {
        "id": "tr1", "type": "track", "stage": "world", "start": 1.0,
        "end": 4.0, "seed": {"src_t": 2.0, "x": 0.4, "y": 0.5},
        "device": "anchor_track"}},
    {"action": "upsert", "layer": "fx", "id": "ob1", "value": {
        "id": "ob1", "type": "object", "stage": "world", "start": 1.0,
        "end": 5.0, "label": "laptop", "prompt": {
            "src_t": 2.0, "box": [0.4, 0.75, 0.66, 1.0]},
        "device": "object_occluder"}},
    {"action": "upsert", "layer": "fx", "id": "pl1", "value": {
        "id": "pl1", "type": "plane", "stage": "world", "start": 1.0,
        "end": 5.0, "src_t": 2.0, "surface": "desk",
        "quad": [[0.2, 0.6], [0.8, 0.6], [0.9, 0.95], [0.1, 0.95]],
        "device": "plane_type"}},
    {"action": "upsert", "layer": "fx", "id": "lt1", "value": {
        "id": "lt1", "type": "light", "stage": "world", "start": 6.0,
        "end": 8.0, "kind": "dissolve", "duration_s": 1.5,
        "device": "light_dissolve"}},
    {"action": "upsert", "layer": "motion", "id": "m1", "value": {
        "id": "m1", "anchor": {"to": "plane:pl1", "occluders": [
            "person", "object:ob1"], "shadow": True}}},
    {"action": "upsert", "layer": "motion", "id": "m2", "value": {
        "id": "m2", "anchor": {"to": "hand.index_tip", "pin": [0.5, 1.1],
                               "lost": "hide"}}},
]


def _base():
    edl = {"keep": [[0.0, 20.0]],
           "motion": [{"id": "m1", "template": "word_slam", "start": 2.0,
                       "end": 4.0, "params": {"text": "PRICE"}},
                      {"id": "m2", "template": "word_slam", "start": 5.0,
                       "end": 7.0, "params": {"text": "IDEA"}}]}
    return schemas.validate_edl(edl, DUR).model_dump()


def test_the_registry_holds_the_pinned_wave3_table():
    assert W3_FX_TAGS <= set(schemas.FX_TYPES)
    assert W3_FX_TAGS <= set(schemas.FX_AUTHORABLE_TYPES)
    assert "MotionItem.anchor" in schemas._FX_SLOT_TYPES
    assert W3_SIDECAR_KINDS <= set(sidecars.KINDS)
    assert set(sidecars.KINDS) <= {"matte.person", "faces.yunet"} | \
        W3_SIDECAR_KINDS | set(sidecars.RESERVED_KINDS)


def test_every_wave3_kind_is_keyed_by_its_models_and_code():
    for kind in W3_SIDECAR_KINDS:
        sp = sidecars.spec(kind)
        assert sp["model_id"] and sp["model_version"], kind
        assert sp["ext"] not in ("json",), kind     # never the manifest name
        for name in sp.get("models") or ():
            rec = json.load(open(os.path.join(WORKER, "models",
                                              f"{name}.json")))
            assert rec["sha256"][:12] in sp["model_version"], (kind, name)


def test_wave3_models_are_pinned_licensed_and_never_on_agent_lanes():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bake", os.path.join(WORKER, "models", "bake.py"))
    bake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bake)
    assert bake.check(os.path.join(WORKER, "models")) == []
    for name in ("mediapipe_face_landmarker", "mediapipe_hand_landmarker",
                 "mediapipe_pose_landmarker", "efficientsam_ti"):
        rec = json.load(open(os.path.join(WORKER, "models",
                                          f"{name}.json")))
        assert rec["licence"] == "Apache-2.0" and rec["commercial_use"]
        assert set(rec["images"]) == {"batch", "vision"}, name


def test_the_world_department_fits_its_budget_and_mcp_prose():
    names = tools_ext.EXT_DEPARTMENTS.get("world") or set()
    assert names and names <= W3_VERBS
    size = len(json.dumps(agent_tools.openai_tools(
        "any-model", compact=True, names=sorted(names)),
        separators=(",", ":")))
    assert size <= tools_ext.DEPARTMENT_COMPACT_BUDGET
    for n in names:
        assert len(agent_tools.TOOLS[n][1]) <= 900, n
    assert W3_DEPARTMENTS <= set(agent_tools.TOOL_DOMAINS)


def test_the_core_catalog_stays_under_the_ceiling():
    core = tools_ext.core_names(agent_tools.TOOLS)
    size = len(json.dumps(agent_tools.openai_tools(
        "any-model", compact=True, names=core), separators=(",", ":")))
    assert size <= 82_700 < 83_000
    assert not (set(core) & W3_VERBS)


def test_wave3_cards_are_live_registered_and_owner_safe():
    cards = devices.load_cards()
    w3 = {cid: c for cid, c in cards.items()
          if devices.CARD_PREFIXES.get(cid.split("_", 1)[0], "")
          .startswith("3")}
    assert w3
    for cid, c in w3.items():
        assert c["verb"] in agent_tools.TOOLS, cid
        if c.get("fx_type"):
            assert c["fx_type"] in schemas.FX_TYPES, cid
        assert not c["owner_flags"]["adds_zoom"], cid
        assert not c["owner_flags"]["adds_sound"], cid
        assert os.path.exists(os.path.join(WORKER, "skills",
                                           f"{c['skill']}.md")), cid


def test_a_stale_client_writes_and_removes_every_wave3_item():
    """TOOL REACH (INTERFACES_W3.md §0.4): no department verb needed."""
    edl = _base()
    sig0 = schemas.edl_signature(edl)
    ops = copy.deepcopy(STALE_OPS)
    out = edit_batch.apply_batch(edl, ops, DUR, set())
    out = out[0] if isinstance(out, tuple) else out
    v = schemas.validate_edl(out, DUR).model_dump()
    assert {f["type"] for f in v["fx"]} >= W3_FX_TAGS
    assert v["motion"][0]["anchor"]["to"] == "plane:pl1"
    # removing a definition an anchor still names is refused ...
    with pytest.raises(Exception, match="ob1"):
        edit_batch.apply_batch(copy.deepcopy(v), [
            {"action": "remove", "layer": "fx", "id": "ob1"}], DUR, set())
    # ... detach first, then remove everything: the signature returns
    back = [{"action": "upsert", "layer": "motion", "id": m,
             "value": {"id": m, "anchor": None}} for m in ("m1", "m2")]
    back += [{"action": "remove", "layer": "fx", "id": i}
             for i in ("tr1", "ob1", "pl1", "lt1")]
    out2 = edit_batch.apply_batch(copy.deepcopy(v), back, DUR, set())
    out2 = out2[0] if isinstance(out2, tuple) else out2
    v2 = schemas.validate_edl(out2, DUR).model_dump()
    assert schemas.edl_signature(v2) == sig0


def test_a_render_without_world_fields_never_builds_a_world_context():
    import renderer
    import world_layer
    edl = _base()
    assert not world_layer.needed(edl)
    assert renderer._world_ctx(edl, 1080, 1920, 30.0, None, {}, True, None,
                               0.0, "/tmp", None) is None


def test_department_modules_export_their_verbs():
    from tools_ext import world_tools
    assert set(world_tools.VERBS) == set(world_tools.TOOL_SPECS)
    assert world_tools.DEPARTMENT == "world"
    assert set(world_tools.VERBS) <= W3_VERBS
