"""Wave 0 interface pass: the shared seams are registered and INERT.

worker/devices/INTERFACES.md pins the contracts four parallel tracks build
on. This file proves the skeleton changes nothing a customer can observe:

  * every EDL written before the seams existed keeps its exact signature,
    its dump (plus only the new empty fields) and its validity;
  * cached-render currency, stitched-preview structure and the review scope
    read old and re-validated EDLs identically;
  * the registries are empty: EDL.fx and every typed slot accept only their
    empty default, no department tool or job kind exists, and the core
    compact tool catalog is the one the 83,000-byte budget measures;
  * each registration mechanism works when a track drops a file in, without
    an edit to schemas.py, agent_tools.py, edit_batch.py or http_server.py.

The golden corpus (fixtures/w0_inert_corpus.json) was produced by the
pre-seam schemas.py at the production release this wave branched from.

Re-pinned at the Wave 1 interface pass (INTERFACES_W1.md §7): the assertions
that read "nothing is registered" now read "nothing outside the pinned
Wave 1 table is registered", so a Wave 1 track that switches its member
module LIVE keeps this file green; the registration mechanisms are exercised
on the bare Wave 0 package (``__init__.py`` + ``core.py``).
"""

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import agent_tools                                     # noqa: E402
import edit_batch                                      # noqa: E402
import faultinject                                     # noqa: E402
import jobs_ext                                        # noqa: E402
import playback_plan                                   # noqa: E402
import renderer                                        # noqa: E402
import schemas                                         # noqa: E402
import sidecars                                        # noqa: E402
import stitch                                          # noqa: E402
import tools_ext                                       # noqa: E402
import version                                         # noqa: E402
from schemas import EDLValidationError, edl_signature, validate_edl  # noqa: E402

with open(os.path.join(HERE, "fixtures", "w0_inert_corpus.json")) as _f:
    CORPUS = json.load(_f)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _without_inert(dump):
    """The dump with exactly the Wave 0 fields removed, each asserted empty."""
    d = copy.deepcopy(dump)
    assert d.pop("fx") == []
    for m in d.get("music") or []:
        assert m.pop("provenance") is None and m.pop("automation") is None
    for m in d.get("motion") or []:
        assert m.pop("composite") is None and m.pop("anchor") is None
    tr = (d.get("effects") or {}).get("transition")
    if tr:
        assert tr.pop("per_junction") is None
    return d


# ── 1. Old EDLs: signature, dump and validity ────────────────────────────

@pytest.mark.parametrize("name", sorted(CORPUS))
def test_old_edl_signature_and_dump_are_unchanged(name):
    case = CORPUS[name]
    new = validate_edl(copy.deepcopy(case["raw"]), case["duration"]).model_dump()
    assert _sha(edl_signature(new)) == case["signature_sha256"]
    assert _without_inert(new) == case["old_dump"]
    # A STORED old EDL (its dump from before the seams) re-validates to the
    # same programme and signature.
    again = validate_edl(copy.deepcopy(case["old_dump"]),
                         case["duration"]).model_dump()
    assert again == new
    assert edl_signature(case["old_dump"]) == edl_signature(new)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_old_edl_reads_the_same_to_caches_stitch_and_review(name):
    case = CORPUS[name]
    old = case["old_dump"]
    new = validate_edl(copy.deepcopy(old), case["duration"]).model_dump()
    import config
    current = {"look_v": config.RENDER_LOOK_VERSION,
               "carry_v": config.CAPTION_CARRY_VERSION,
               "follow_v": config.FOLLOW_VERSION,
               "card_v": config.CARD_LAYOUT_VERSION,
               "legib_v": config.LEGIBILITY_VERSION,
               "cap_v": config.CAPTION_TIMING_VERSION,
               "clock_v": config.BLOCK_CLOCK_VERSION,
               "cam_v": config.CAMERA_VERSION,
               "trans_v": config.TRANSITION_VERSION,
               "gfx_shape_v": config.GFX_SHAPING_VERSION,
               "master_v": config.MASTER_VERSION, "audio_peak_v": 1,
               "tail_v": config.MUSIC_TAIL_VERSION,
               "handoff_v": config.HANDOFF_VERSION}
    for meta in ({}, current):
        for fn in (renderer.carry_current, renderer.follow_current,
                   renderer.cards_current, renderer.legibility_current,
                   renderer.captions_current, renderer.block_clock_current,
                   renderer.transitions_current, renderer.shaping_current,
                   renderer.audio_peak_current, renderer.master_current):
            assert fn(meta, old) == fn(meta, new), fn.__name__
        assert renderer.camera_current(meta, old) == \
            renderer.camera_current(meta, new)
        dur = schemas.program_duration(new)
        assert renderer.music_tail_current(meta, old, dur) == \
            renderer.music_tail_current(meta, new, dur)
    # A stitched preview sees no structural change...
    assert stitch._strip(old)[0] == stitch._strip(new)[0]
    # ...and the review scope reports nothing changed.
    work = playback_plan.changed_work(old, new)
    assert work["changed_layers"] == []
    assert work["picture_unchanged"] and not work["audio_changed"]


def test_describe_edl_is_unchanged_without_fx():
    case = CORPUS["legacy_v2"]
    new = validate_edl(copy.deepcopy(case["raw"]), case["duration"]).model_dump()
    assert "fx:" not in schemas.describe_edl(new, case["duration"])
    assert schemas.describe_edl(new) == schemas.describe_edl(case["old_dump"])


# ── 2. Empty registries reject everything but the empty default ─────────

# Every tag a Wave 1 member module may register (INTERFACES_W1.md §3), plus
# 0B's engine-only probe on probe lanes.
W1_FX_TAGS = {"subject", "background", "region", "accent", "strobe",
              "junction", "grade_window", "finish"}
W1_VERBS = {"add_subject_effect", "set_background", "remove_subject_effect",
            "set_composite", "add_type_window", "add_region_effect",
            "remove_region_effect", "add_accent", "add_strobe",
            "set_junction", "remove_punctuation", "set_grade_window",
            "isolate_colour", "import_lut", "match_grade", "add_finish",
            "make_variant_set", "remove_finish", "suggest_devices",
            "set_intensity", "proof_device", "audit_motion",
            # Wave 1 integration: recipes (INTERFACES_W1.md §10)
            "suggest_recipes", "apply_recipe"}


def test_only_pinned_fx_types_are_registered_and_others_are_rejected():
    assert set(schemas.FX_TYPES) <= W1_FX_TAGS | {"fx_probe"}
    assert schemas.FX_STAGES == ("world", "screen", "pre_captions",
                                 "over_type", "junction", "music_bus")
    edl = schemas.default_edl(30)
    edl["fx"] = [{"id": "a", "type": "no_such_fx", "stage": "pre_captions",
                  "start": 1.0, "end": 1.04}]
    match = None if schemas.FX_TYPES else "no fx type is registered"
    with pytest.raises(EDLValidationError, match=match):
        validate_edl(edl, 30)


@pytest.mark.parametrize("path,value", [
    (("motion", 0, "composite"), {"blend": "screen"}),
    (("motion", 0, "anchor"), {"kind": "plane"}),
    (("music", 0, "automation"), [{"t": 1.0, "gain_db": -6}]),
    (("effects", "transition", "per_junction"), [{"at": 2.0}]),
])
def test_unowned_slots_accept_only_none(path, value):
    slot = {"composite": "MotionItem.composite", "anchor": "MotionItem.anchor",
            "automation": "MusicItem.automation",
            "per_junction": "TransitionSpec.per_junction"}[path[-1]]
    if slot in schemas._FX_SLOT_TYPES:
        pytest.skip(f"{slot} is owned by a live Wave 1 module")
    edl = copy.deepcopy(CORPUS["legacy_v2"]["old_dump"])
    edl["motion"] = copy.deepcopy(
        CORPUS["motion_texts_vectors"]["old_dump"]["motion"])
    validate_edl(copy.deepcopy(edl), 30.0)          # None everywhere: valid
    target = edl
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(EDLValidationError, match="not available in this build"):
        validate_edl(edl, 30.0)


def test_music_provenance_values_are_pinned():
    edl = copy.deepcopy(CORPUS["music_variants"]["old_dump"])
    for value in ("user_upload", "user_named", "user_picked_library",
                  "legacy", None):
        edl["music"][0]["provenance"] = value
        validate_edl(copy.deepcopy(edl), 40.0)
    edl["music"][0]["provenance"] = "agent_picked"
    with pytest.raises(EDLValidationError):
        validate_edl(edl, 40.0)


def test_render_path_can_strip_fx_types_it_does_not_know():
    edl = {"keep": [[0.0, 5.0]],
           "fx": [{"id": "n1", "type": "from_a_newer_lane"}, "junk"]}
    stripped, dropped = schemas.strip_unknown_fx(edl)
    assert stripped["fx"] == [] and dropped == ["n1", "junk"]
    assert edl["fx"]                                  # input untouched
    same, none = schemas.strip_unknown_fx({"keep": [[0.0, 5.0]]})
    assert none == [] and same == {"keep": [[0.0, 5.0]]}


def test_edit_batch_accepts_the_fx_layer_but_not_unknown_items():
    assert "fx" in edit_batch.LAYERS and "fx" in edit_batch.ITEM_LAYERS
    base = schemas.default_edl(30)
    out = edit_batch.apply_batch(
        base, [{"action": "set", "layer": "fx", "value": []}], 30, set())
    assert out["fx"] == [] and edl_signature(out) == edl_signature(base)
    match = None if schemas.FX_TYPES else "not available"
    with pytest.raises(EDLValidationError, match=match):
        edit_batch.apply_batch(base, [{
            "action": "upsert", "layer": "fx", "id": "a",
            "value": {"id": "a", "type": "no_such_fx", "stage": "world",
                      "start": 1.0, "end": 2.0}}], 30, set())


def test_engine_only_fx_types_are_never_authored(monkeypatch):
    monkeypatch.setattr(edit_batch, "FX_TYPES", ("probe_stage",))
    monkeypatch.setattr(edit_batch, "FX_AUTHORABLE_TYPES", ())
    with pytest.raises(ValueError, match="internal to the engine"):
        edit_batch.apply_batch(schemas.default_edl(30), [{
            "action": "set", "layer": "fx",
            "value": [{"id": "p", "type": "probe_stage", "stage": "world",
                       "start": 1.0, "end": 2.0}]}], 30, set())


def test_fx_layer_is_advertised_exactly_when_a_type_is_authorable():
    props = agent_tools.TOOLS["apply_edit_batch"][2]
    enum = props["operations"]["items"]["properties"]["layer"]["enum"]
    assert ("fx" in enum) == bool(schemas.FX_AUTHORABLE_TYPES)
    assert set(enum) - {"fx"} == edit_batch.LAYERS - {"fx"}


def test_only_pinned_department_tools_and_no_extension_job_kind_exist():
    assert tools_ext.EXT_TOOL_NAMES <= W1_VERBS
    live = set(agent_tools.TOOL_DOMAINS) & set(tools_ext.DEPARTMENTS)
    assert live == set(tools_ext.EXT_DEPARTMENTS)
    assert tools_ext.core_names(agent_tools.TOOLS) == [
        n for n in agent_tools.TOOLS if n not in tools_ext.EXT_TOOL_NAMES]
    import http_server
    # Re-pinned for track 0A (INTERFACES.md §2.5 requires jobs_ext/sidecar.py
    # to register the one `sidecar` kind); still no other extension kind.
    assert set(jobs_ext.REGISTRY) <= {"sidecar"}
    assert {name for name, fn in http_server.COMPUTE_RUNNERS.items()
            if getattr(fn, "__name__", "").startswith("lazy_")} \
        <= {"sidecar"}


# ── 3. The core compact catalog ──────────────────────────────────────────

def _compact_bytes(names=None):
    names = names if names is not None else tools_ext.core_names(
        agent_tools.TOOLS)
    return len(json.dumps(agent_tools.openai_tools(
        "any-model", compact=True, names=names), separators=(",", ":")))


def test_installing_no_department_changes_nothing():
    before = json.dumps(agent_tools.openai_tools("any-model", compact=True),
                        separators=(",", ":"))
    full_before = json.dumps(agent_tools.openai_tools(),
                             separators=(",", ":"))
    tools_ext.install(agent_tools, modules=[])
    assert json.dumps(agent_tools.openai_tools("any-model", compact=True),
                      separators=(",", ":")) == before
    assert json.dumps(agent_tools.openai_tools(),
                      separators=(",", ":")) == full_before


def _isolated_agent_tools(monkeypatch):
    """Private copies of every registry install() mutates."""
    tools = dict(agent_tools.TOOLS)
    for name in ("load_tools", "expand_toolset"):
        fn, desc, props = tools[name]
        tools[name] = (fn, desc, copy.deepcopy(props))
    monkeypatch.setattr(agent_tools, "TOOLS", tools)
    monkeypatch.setattr(agent_tools, "TOOL_DOMAINS",
                        {k: set(v) for k, v in agent_tools.TOOL_DOMAINS.items()})
    monkeypatch.setattr(agent_tools, "TOOL_DOMAIN_NAMES",
                        set(agent_tools.TOOL_DOMAIN_NAMES))
    monkeypatch.setattr(agent_tools, "AGENT_TOOL_DOMAINS",
                        tuple(agent_tools.AGENT_TOOL_DOMAINS))
    monkeypatch.setattr(agent_tools, "REQUIRED_ARGS",
                        dict(agent_tools.REQUIRED_ARGS))
    monkeypatch.setattr(agent_tools, "WRITE_TOOLS",
                        set(agent_tools.WRITE_TOOLS))
    monkeypatch.setattr(agent_tools, "_COMPACT_CONTRACTS",
                        dict(agent_tools._COMPACT_CONTRACTS))
    # copies, not empties: departments a Wave 1 track has made live stay
    # department tools (out of the core catalog) inside the isolation
    monkeypatch.setattr(tools_ext, "EXT_TOOL_NAMES",
                        set(tools_ext.EXT_TOOL_NAMES))
    monkeypatch.setattr(tools_ext, "EXT_DEPARTMENTS",
                        {k: set(v) for k, v in
                         tools_ext.EXT_DEPARTMENTS.items()})
    monkeypatch.setattr(tools_ext, "DISABLED", dict(tools_ext.DISABLED))


def _probe_department(dept="compositing", hidden=False):
    mod = types.ModuleType("tools_ext.probe")
    mod.DEPARTMENT = dept

    def add_probe_effect(ctx, kind, at=None):
        return f"EDL v1 -> v2: probe {kind}"

    def probe_status(ctx):
        return "nothing measured"
    mod.TOOL_SPECS = {
        "add_probe_effect": (add_probe_effect,
                             "Add a probe effect at a moment. Second sentence "
                             "of handbook prose the compact catalog drops.",
                             {"kind": {"type": "string",
                                       "enum": ["fill", "glow"]},
                              "at": {"type": "number"}}),
        "probe_status": (probe_status, "Report probe state.", {}),
    }
    mod.REQUIRED_ARGS = {"add_probe_effect": ["kind"]}
    mod.WRITE_TOOLS = {"add_probe_effect"}
    mod.COMPACT = {"add_probe_effect": "Probe effect; kind fill|glow."}
    mod.DISABLED = {"probe_status": lambda: hidden}
    return mod


def _free_department():
    """A department no live module has registered yet (the probe's)."""
    for dept in tools_ext.DEPARTMENTS:
        if dept not in agent_tools.TOOL_DOMAINS:
            return dept
    pytest.skip("every department is live")


def test_department_tools_stay_out_of_the_core_compact_catalog(monkeypatch):
    dname = _free_department()
    baseline = _compact_bytes()
    _isolated_agent_tools(monkeypatch)
    tools_ext.install(agent_tools, modules=[_probe_department(dname)])
    assert {"add_probe_effect", "probe_status"} <= set(agent_tools.TOOLS)
    assert agent_tools.TOOL_DOMAINS[dname] == {
        "add_probe_effect", "probe_status"}
    assert "add_probe_effect" in agent_tools.WRITE_TOOLS
    core = tools_ext.core_names(agent_tools.TOOLS)
    assert "add_probe_effect" not in core
    # The only core growth is the department name in the two domain enums.
    enum_cost = 2 * len(f',"{dname}"')
    assert _compact_bytes(core) == baseline + enum_cost
    for tool in ("load_tools", "expand_toolset"):
        enum = agent_tools.TOOLS[tool][2]["domains"]["items"]["enum"]
        assert enum[-1] == dname
    # The department pages in on request, compact, within its own budget.
    dept = agent_tools.openai_tools("any-model", compact=True,
                                    names=agent_tools.TOOL_DOMAINS[dname])
    assert {t["function"]["name"] for t in dept} == {"add_probe_effect",
                                                     "probe_status"}
    by_name = {t["function"]["name"]: t["function"] for t in dept}
    assert by_name["add_probe_effect"]["description"] == \
        "Probe effect; kind fill|glow."
    assert by_name["add_probe_effect"]["parameters"]["required"] == ["kind"]
    assert len(json.dumps(dept, separators=(",", ":"))) <= \
        tools_ext.DEPARTMENT_COMPACT_BUDGET

    class Ctx:
        pass
    ctx = Ctx()
    assert agent_tools.load_tools(ctx, domains=[dname]).startswith(
        "Capabilities loaded")
    assert "add_probe_effect" in agent_tools.compact_tool_names(ctx)
    assert f"{dname}: add_probe_effect" in \
        agent_tools.capability_directory()


def test_department_honest_off_and_bad_modules(monkeypatch):
    dname = _free_department()
    _isolated_agent_tools(monkeypatch)
    tools_ext.install(agent_tools, modules=[_probe_department(dname,
                                                              hidden=True)])
    assert agent_tools._tool_disabled("probe_status") is True
    assert agent_tools._tool_disabled("add_probe_effect") is False
    names = {t["function"]["name"] for t in agent_tools.openai_tools()}
    assert "probe_status" not in names and "add_probe_effect" in names

    bad = _probe_department(dept="effects")
    with pytest.raises(ValueError, match="DEPARTMENT"):
        tools_ext.install(agent_tools, modules=[bad])
    clash = _probe_department(dname)
    clash.TOOL_SPECS = {"add_zoom": clash.TOOL_SPECS["add_probe_effect"]}
    clash.COMPACT = {}
    with pytest.raises(ValueError, match="collides"):
        tools_ext.install(agent_tools, modules=[clash])


# ── 4. Job kinds ─────────────────────────────────────────────────────────

def test_job_kinds_register_lazily_and_never_shadow_core(monkeypatch):
    monkeypatch.setattr(jobs_ext, "REGISTRY", {})
    calls = []
    runner_mod = types.ModuleType("w0_probe_runner")
    runner_mod.run = lambda worker_db, job: calls.append(job) or {"ok": 1}
    mod = types.ModuleType("jobs_ext.probe")
    mod.JOB_KINDS = [jobs_ext.JobKind(name="probe_kind",
                                      runner="w0_probe_runner:run",
                                      lane="batch")]
    core = {"index": object()}
    merged = jobs_ext.merge_into(dict(core), modules=[mod])
    assert set(merged) == {"index", "probe_kind"}
    assert calls == []                      # nothing imported or run yet
    monkeypatch.setitem(sys.modules, "w0_probe_runner", runner_mod)
    assert merged["probe_kind"](None, {"a": 1}) == {"ok": 1}
    assert calls == [{"a": 1}]
    clash = types.ModuleType("jobs_ext.clash")
    clash.JOB_KINDS = [jobs_ext.JobKind(name="index", runner="json:dumps")]
    with pytest.raises(ValueError, match="shadow"):
        jobs_ext.merge_into(dict(core), modules=[clash])
    with pytest.raises(ValueError, match="lane"):
        jobs_ext.register(jobs_ext.JobKind(name="x", runner="a:b",
                                           lane="gpu"))


# ── 5. The fx registry, exercised by dropping a member file in ───────────

_PROBE_MEMBER = '''
from typing import ClassVar, List, Literal, Optional
from pydantic import BaseModel
from . import FxItemBase


class ProbeAccent(FxItemBase):
    STAGES: ClassVar[tuple] = ("pre_captions", "over_type")
    type: Literal["probe_accent"] = "probe_accent"
    kind: Literal["negative", "white_hit"] = "negative"

    def check(self, ctx):
        if self.kind == "white_hit" and ctx.canvas:
            raise ValueError("white_hit needs footage")


class ProbeJunction(FxItemBase):
    STAGES: ClassVar[tuple] = ("junction",)
    type: Literal["probe_junction"] = "probe_junction"


class ProbeBus(FxItemBase):
    STAGES: ClassVar[tuple] = ("music_bus",)
    type: Literal["probe_bus"] = "probe_bus"


class ProbeStage(FxItemBase):
    AUTHORABLE: ClassVar[bool] = False
    type: Literal["probe_stage"] = "probe_stage"


class Composite(BaseModel):
    blend: Literal["screen", "difference"]


FX_MEMBERS = (ProbeAccent, ProbeJunction, ProbeBus, ProbeStage)
FIELD_TYPES = {"MotionItem.composite": Composite}
'''


W0_PACKAGE = ("__init__.py", "core.py")


def _load_schemas_copy(tmp_path, name, extra=None, full=False):
    """schemas.py + fx_schemas/ copied beside a member file and loaded BY
    PATH under a foreign module name: exactly how the backend loads it.
    Without ``full`` only the bare Wave 0 package is copied, so the
    registration mechanisms are exercised whatever Wave 1 has switched on."""
    shutil.copy(os.path.join(WORKER, "schemas.py"), tmp_path / "schemas.py")
    keep = None if full else set(W0_PACKAGE)
    shutil.copytree(os.path.join(WORKER, "fx_schemas"),
                    tmp_path / "fx_schemas",
                    ignore=lambda d, names: [
                        n for n in names if n == "__pycache__" or (
                            keep is not None and n.endswith(".py")
                            and n not in keep)])
    for fname, text in (extra or {}).items():
        (tmp_path / "fx_schemas" / fname).write_text(text)
    spec = importlib.util.spec_from_file_location(
        name, str(tmp_path / "schemas.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture
def probe_name():
    names = []

    def make(base):
        names.append(base)
        return base
    yield make
    for base in names:
        for key in [k for k in sys.modules
                    if k == base or k.startswith(base + "__fx_schemas")]:
            sys.modules.pop(key, None)


def test_backend_style_load_has_the_same_registry(tmp_path, probe_name):
    mod = _load_schemas_copy(tmp_path, probe_name("w0_backend_schemas"),
                             full=True)
    assert mod.FX_TYPES == schemas.FX_TYPES and \
        "w0_backend_schemas__fx_schemas" in sys.modules
    assert mod.edl_signature(mod.default_edl(12)) == \
        edl_signature(schemas.default_edl(12))


def test_a_dropped_member_file_registers_fx_types_and_slots(tmp_path,
                                                            probe_name):
    s = _load_schemas_copy(tmp_path, probe_name("w0_probe_schemas"),
                           {"probe.py": _PROBE_MEMBER})
    assert s.FX_TYPES == ("probe_accent", "probe_junction", "probe_bus",
                          "probe_stage")
    assert s.FX_AUTHORABLE_TYPES == ("probe_accent", "probe_junction",
                                     "probe_bus")
    base = {"keep": [[0.0, 20.0]],
            "music": [{"id": "m1", "storage_key": "library:x",
                       "start": 0.0, "end": 20.0}]}
    edl = dict(base, fx=[
        {"id": "b", "type": "probe_accent", "stage": "over_type",
         "start": 4.0, "end": 4.04, "kind": "white_hit",
         "bind": {"on": "stress", "ref": 12, "rhythm": "speech"}},
        {"id": "a", "type": "probe_accent", "stage": "pre_captions",
         "start": 4.004, "end": 4.041},
        {"id": "j", "type": "probe_junction", "stage": "junction", "at": 9.0},
        {"id": "m", "type": "probe_bus", "stage": "music_bus",
         "music_id": "m1"},
    ])
    out = s.validate_edl(copy.deepcopy(edl), 20.0).model_dump()
    # Sorted by stage order, then time; times on the 0.01 s grid.
    assert [f["id"] for f in out["fx"]] == ["a", "b", "j", "m"]
    assert out["fx"][0]["start"] == 4.0 and out["fx"][0]["end"] == 4.04
    assert out["fx"][1]["bind"] == {"on": "stress", "ref": 12,
                                    "rhythm": "speech", "offset_f": 0}
    assert "fx: probe_accent [a] pre_captions 4-4.04s" in \
        s.describe_edl(out, 20.0)
    assert '"fx"' in s.edl_signature(out)
    stripped, dropped = s.strip_unknown_fx(
        dict(out, fx=out["fx"] + [{"id": "z", "type": "nope"}]))
    assert dropped == ["z"] and len(stripped["fx"]) == 4

    def rejects(fx, match):
        with pytest.raises(s.EDLValidationError, match=match):
            s.validate_edl(dict(base, fx=fx), 20.0)
    rejects([{"id": "x", "type": "probe_accent", "stage": "world",
              "start": 1, "end": 2}], "runs at pre_captions, over_type")
    rejects([{"id": "x", "type": "probe_accent", "stage": "over_type"}],
            "needs start and end")
    rejects([{"id": "x", "type": "probe_accent", "stage": "over_type",
              "start": 19.0, "end": 21.0}], "exceeds the limit")
    rejects([{"id": "x", "type": "probe_junction", "stage": "junction"}],
            "names its cut by at")
    rejects([{"id": "x", "type": "probe_bus", "stage": "music_bus",
              "music_id": "nope"}], "music_id must name")
    rejects([{"id": "x", "type": "unknown", "stage": "world",
              "start": 1, "end": 2}], "does not match any of the expected")
    rejects([{"id": "x", "type": "probe_accent", "stage": "over_type",
              "start": 1, "end": 2}] * 2, "unique")
    # A member's own check() runs on the validated EDL.
    canvas = {"keep": [], "canvas": {"width": 1080, "height": 1920,
                                     "fps": 30, "bg_color": "#000000"},
              "inserts": [{"id": "c", "asset_key": "media/p/a.png",
                           "kind": "image", "at_output_s": 0.0,
                           "duration_s": 4.0}],
              "fx": [{"id": "w", "type": "probe_accent",
                      "stage": "over_type", "start": 1, "end": 2,
                      "kind": "white_hit"}]}
    with pytest.raises(s.EDLValidationError, match="needs footage"):
        s.validate_edl(canvas, None)
    # The owned slot is typed; the others still accept only None.
    mo = s.MotionItem(id="m", template="html", start=1, end=2,
                      composite={"blend": "screen"})
    assert mo.composite.blend == "screen"
    with pytest.raises(Exception):
        s.MotionItem(id="m", template="html", start=1, end=2,
                     composite={"blend": "multiply"})
    with pytest.raises(Exception, match="not available"):
        s.MotionItem(id="m", template="html", start=1, end=2,
                     anchor={"kind": "plane"})


@pytest.mark.parametrize("text,match", [
    (_PROBE_MEMBER.replace('"probe_bus"', '"probe_accent"'),
     "registered twice"),
    (_PROBE_MEMBER.replace('"MotionItem.composite"', '"MotionItem.colour"'),
     "unknown slot"),
    (_PROBE_MEMBER.replace('("junction",)', '("everywhere",)'),
     "STAGES must be"),
])
def test_bad_member_files_fail_at_import(tmp_path, probe_name, text, match):
    with pytest.raises((ValueError, TypeError), match=match):
        _load_schemas_copy(tmp_path, probe_name("w0_bad_schemas"),
                           {"probe.py": text})


def test_a_slot_has_exactly_one_owner(tmp_path, probe_name):
    other = ("from pydantic import BaseModel\n"
             "class C(BaseModel):\n    x: int\n"
             "FIELD_TYPES = {'MotionItem.composite': C}\n")
    with pytest.raises(ValueError, match="already owned"):
        _load_schemas_copy(tmp_path, probe_name("w0_owner_schemas"),
                           {"probe.py": _PROBE_MEMBER, "zz_other.py": other})


# ── 6. Shared plumbing: fingerprint, faults, sidecar keys ────────────────

def test_runtime_packages_are_inside_the_code_fingerprint():
    rel = {os.path.relpath(p, WORKER).replace(os.sep, "/")
           for p in version._source_files()}
    for required in ("schemas.py", "fx_schemas/__init__.py",
                     "tools_ext/__init__.py", "jobs_ext/__init__.py",
                     "devices/__init__.py"):
        assert required in rel
    assert not any(p.endswith(".md") or "__pycache__" in p for p in rel)
    assert "modal_app.py" not in rel


def test_faults_are_off_unless_injected(monkeypatch):
    monkeypatch.delenv("VALMERA_FAULTS", raising=False)
    assert not faultinject.fault("sidecar_timeout")
    with faultinject.inject("ffmpeg_filter_missing:maskedmerge"):
        assert faultinject.fault("ffmpeg_filter_missing:maskedmerge")
        assert not faultinject.fault("ffmpeg_filter_missing:xfade")
    with faultinject.inject("model_missing"):
        assert faultinject.fault("model_missing:matte.person")
    assert not faultinject.fault("model_missing")
    monkeypatch.setenv("VALMERA_FAULTS", "chromium_crash")
    assert faultinject.fault("chromium_crash")
    with pytest.raises(ValueError):
        with faultinject.inject("gremlins"):
            pass


def test_sidecar_keys_and_chunks_are_deterministic():
    k1 = sidecars.sidecar_key("matte.person", "abc", "rvm", "1.0",
                              {"b": 1, "a": 2}, (149.44, 164.44))
    k2 = sidecars.sidecar_key("matte.person", "abc", "rvm", "1.0",
                              {"a": 2, "b": 1}, (149.4400001, 164.44))
    assert k1 == k2 and len(k1) == 64
    assert k1 != sidecars.sidecar_key("matte.person", "abc", "rvm", "1.1",
                                      {"a": 2, "b": 1}, (149.44, 164.44))
    grid = sidecars.chunk_ranges([10.0, 50.0], 60.0)
    # shot [0,10) whole; [10,50) in ceil(40/15)=3 equal parts; [50,60) whole
    assert grid == [(0.0, 10.0), (10.0, 23.333), (23.333, 36.667),
                    (36.667, 50.0), (50.0, 60.0)]
    assert all(b - a <= sidecars.CHUNK_MAX_S + 1e-6 for a, b in grid)
    assert sidecars.chunks_for([10.0, 50.0], 60.0, [(20.0, 24.0)]) == \
        [(10.0, 23.333), (23.333, 36.667)]
    src = sidecars.SourceRef(sha256="abc", storage_key="proxy.mp4",
                             duration_s=60.0, fps=25.0, width=960,
                             height=540, shots=(10.0, 50.0))
    h = sidecars.ensure("matte.person", src, [(0.0, 12.0)])
    assert len(h.chunks) == 2 and sidecars.status(h) == "absent"
    assert sidecars.fetch(h, "/nonexistent") == []


def test_ci_static_registry_sees_department_tools(tmp_path):
    """CI validates skills before dependencies exist, reading the registry
    statically: a department module's TOOL_SPECS literal must be visible to
    it, or every skill naming a department verb fails validation."""
    import skill_validator
    (tmp_path / "agent_tools.py").write_text(
        "TOOLS = {'core_tool': 1}\n", encoding="utf-8")
    (tmp_path / "tools_ext").mkdir()
    (tmp_path / "tools_ext" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tools_ext" / "subject_tools.py").write_text(
        "BASE = {'remove_subject_effect': 1}\n"
        "TOOL_SPECS = {**BASE, 'add_subject_effect': 2}\n", encoding="utf-8")
    (tmp_path / "tools_ext" / "_private.py").write_text(
        "TOOL_SPECS = {'hidden_helper': 1}\n", encoding="utf-8")
    assert skill_validator.live_tool_names(tmp_path / "agent_tools.py") == {
        "core_tool", "add_subject_effect", "remove_subject_effect"}


def test_every_live_department_fits_its_compact_budget():
    """Holds for whatever departments the tracks register."""
    for dept, names in tools_ext.EXT_DEPARTMENTS.items():
        schemas_ = agent_tools.openai_tools("any-model", compact=True,
                                            names=names)
        size = len(json.dumps(schemas_, separators=(",", ":")))
        assert size <= tools_ext.DEPARTMENT_COMPACT_BUDGET, (dept, size)
        assert dept in agent_tools.TOOL_DOMAINS and \
            dept in agent_tools.TOOL_DOMAIN_NAMES
