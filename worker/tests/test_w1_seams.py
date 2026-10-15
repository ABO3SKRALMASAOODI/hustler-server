"""Wave 1 interface pass: the shared contracts are pinned and INERT.

worker/devices/INTERFACES_W1.md pins what six parallel Wave 1 tracks build
on. This file proves the skeleton commit changes nothing a customer can
observe, and keeps proving it as each track switches its module live:

  * the fx registry holds only pinned tags; a member module registers only
    when its track flips ``LIVE`` (or the tests' preview switch is set);
  * with EVERY Wave 1 member registered (preview lane, child process), old
    EDLs keep their signatures and dumps, and every device is written and
    removed through ``edit_batch.apply_batch`` exactly as a stale MCP client
    would (fixtures/w1_stale_client_ops.json), bad shapes refused;
  * the core compact catalog is Wave 0's byte for byte once the automatic
    Wave 1 growth (the ``fx`` layer, department names) is set aside, and the
    render version stamps are unchanged;
  * the shared helpers every track calls (program-matte stubs, peers,
    regions, mask_ops, composite_behind, the batch write path, the device
    gate, the flash hook) behave as pinned.

Round-lead owned. A track that needs a change here asks in its report.
"""

import ast
import copy
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import agent_tools                                     # noqa: E402
import devices                                         # noqa: E402
import edit_batch                                      # noqa: E402
import flash_safety                                    # noqa: E402
import fxgraph                                         # noqa: E402
import fxprims                                         # noqa: E402
import motion_layer                                    # noqa: E402
import regions                                         # noqa: E402
import schemas                                         # noqa: E402
import tools_ext                                       # noqa: E402

FIX = os.path.join(HERE, "fixtures")

# ── the pinned table (INTERFACES_W1.md §3, §4) ───────────────────────────
MEMBER_MODULES = {
    "subject": ("1b", {"subject", "background"}, set()),
    "composite": ("1c", {"region"}, {"MotionItem.composite"}),
    "accents": ("1d", {"accent", "strobe", "junction"}, set()),
    "finish": ("1e", {"grade_window", "finish"}, set()),
}
CLASSES = {
    "subject": (("world", "screen"), "subject_fx:COMPILER", "subject"),
    "background": (("world", "screen"), "subject_fx:COMPILER", "subject"),
    "region": (("world", "screen", "pre_captions", "over_type"),
               "compositor:REGION", "composite"),
    "accent": (("pre_captions", "over_type"), "accents:ACCENT", "accents"),
    "strobe": (("pre_captions", "over_type"), "strobe:STROBE", "accents"),
    "junction": (("junction",), "junctions_v2:JUNCTION", "accents"),
    "grade_window": (("world", "screen"), "gradewin:GRADE", "finish"),
    "finish": (("world", "screen", "pre_captions", "over_type"),
               "finish:FINISH", "finish"),
}
DEPARTMENTS = {
    "subject_tools": ("compositing", ("add_subject_effect", "set_background",
                                      "remove_subject_effect")),
    "composite_tools": ("compositing", ("set_composite", "add_type_window",
                                        "add_region_effect",
                                        "remove_region_effect")),
    "punctuation_tools": ("rhythm", ("add_accent", "add_strobe",
                                     "set_junction", "remove_punctuation")),
    "finish_tools": ("compositing", ("set_grade_window", "isolate_colour",
                                     "import_lut", "match_grade",
                                     "add_finish", "make_variant_set",
                                     "remove_finish")),
    "verify_tools": ("verify", ("suggest_devices", "set_intensity",
                                "proof_device", "audit_motion")),
}
PEERS = {"face": "matte_track:face_mask", "glyph": "compositor:glyph_mask",
         "region": "regions:region_mask", "hue": "gradewin:hue_mask"}
# Core compact catalog at the end of Wave 1 (budget 83,000; the cap is
# never raised). INTERFACES_W1.md §4.3.
W1_CORE_CEILING = 82_700


def _fx_module(name):
    return importlib.import_module(f"fx_schemas.{name}")


# ── 1. The registry ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(MEMBER_MODULES))
def test_a_member_module_registers_only_when_live(name):
    track, tags, slots = MEMBER_MODULES[name]
    mod = _fx_module(name)
    assert mod.TRACK == track
    preview = os.environ.get("VALMERA_FX_PREVIEW", "").strip()
    registered = {c.model_fields["type"].default for c in mod.FX_MEMBERS}
    if mod.LIVE:
        assert registered == tags
        assert tags <= set(schemas.FX_TYPES)
        assert set(getattr(mod, "FIELD_TYPES", {}) or {}) == slots
    elif not preview:
        assert registered == set()
        assert not tags & set(schemas.FX_TYPES)
        assert not slots & set(schemas._FX_SLOT_TYPES)


def test_live_switch_reads_flag_then_preview(monkeypatch):
    from fx_schemas import live
    monkeypatch.delenv("VALMERA_FX_PREVIEW", raising=False)
    assert live(True, "1b") and not live(False, "1b")
    monkeypatch.setenv("VALMERA_FX_PREVIEW", "1")
    assert live(False, "1e")
    monkeypatch.setenv("VALMERA_FX_PREVIEW", "1b, 1D")
    assert live(False, "1b") and live(False, "1d") and not live(False, "1c")


def _child(env_preview, script="w1_preview_probe.py"):
    env = dict(os.environ)
    env.update(VALMERA_FX_PREVIEW=env_preview, SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub")
    env.pop("VALMERA_FX_PROBE", None)
    r = subprocess.run([sys.executable, os.path.join(HERE, script)],
                       cwd=WORKER, env=env, capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def preview_report():
    return _child("1")


def test_preview_lane_registers_exactly_the_pinned_table(preview_report):
    reg = preview_report["registry"]
    assert set(reg["types"]) == set(CLASSES)
    assert set(reg["authorable"]) == set(CLASSES)
    assert reg["slots"] == ["MotionItem.composite"]
    for tag, (stages, compiler, module) in CLASSES.items():
        got = reg["classes"][tag]
        assert tuple(got["stages"]) == stages, tag
        assert got["compiler"] == compiler, tag
        assert got["module"] == module, tag


def test_old_edls_are_unchanged_with_every_w1_member_registered(
        preview_report):
    assert preview_report["corpus"] == []


def test_every_w1_device_is_written_and_removed_through_apply_edit_batch(
        preview_report):
    """TOOL REACH: an MCP client that cached tools/list before Wave 1 sees
    no department verb, but its apply_edit_batch reaches every device."""
    assert preview_report["ok_failures"] == []
    with open(os.path.join(FIX, "w1_stale_client_ops.json")) as f:
        fx = json.load(f)
    n_fx = sum(1 for c in fx["ok"] if c["ops"][0]["layer"] == "fx")
    assert preview_report["fx_written"] == n_fx
    written = {c["ops"][0]["value"]["type"] for c in fx["ok"]
               if c["ops"][0]["layer"] == "fx"}
    assert written == set(CLASSES)          # every pinned type has a shape
    assert preview_report["flash_events"] is not None


def test_bad_stale_client_shapes_are_refused_with_their_reason(
        preview_report):
    assert preview_report["bad_failures"] == []


def test_preview_by_track_registers_only_that_track():
    """VALMERA_FX_PREVIEW=1b adds 1B's module to whatever is live already."""
    report = _child("1b")
    live = {n for n in MEMBER_MODULES if _fx_module(n).LIVE} | {"subject"}
    assert set(report["registry"]["types"]) == set().union(
        *(MEMBER_MODULES[n][1] for n in live))
    assert set(report["registry"]["slots"]) == set().union(
        *(MEMBER_MODULES[n][2] for n in live))
    # the 1B shapes are written there; a track that is not live is refused
    failed = {f.split(":", 1)[0] for f in report["ok_failures"]}
    assert not any(name.endswith("(1B)") for name in failed)
    if "accents" not in live:
        assert any(name.endswith("(1D)") for name in failed)


# ── 2. The core compact catalog and the render stamps ────────────────────

def _core_catalog():
    core = tools_ext.core_names(agent_tools.TOOLS)
    return agent_tools.openai_tools("any-model", compact=True, names=core)


# The track skills INTERFACES_W1.md §5 requires (each with its "Through
# apply_edit_batch" section): a skill file joins read_skill's name enum
# automatically, like a department joins load_tools'.
W1_SKILLS = {"subject-fx", "compositing", "punctuation", "finishing"}


def _strip_w1_growth(catalog):
    """The catalog with Wave 1's automatic growth set aside: the ``fx``
    layer in apply_edit_batch's enum, live department names in the
    load_tools / expand_toolset domain enums, and the track skills (§5) in
    read_skill's name enum."""
    cat = copy.deepcopy(catalog)
    live = set(tools_ext.EXT_DEPARTMENTS)

    def walk(node, drop):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "enum" and isinstance(v, list):
                    node[k] = [e for e in v if e not in drop]
                else:
                    walk(v, drop)
        elif isinstance(node, list):
            for v in node:
                walk(v, drop)
    for tool in cat:
        name = tool["function"]["name"]
        if name == "apply_edit_batch":
            walk(tool, {"fx"})
        elif name in ("load_tools", "expand_toolset"):
            walk(tool, live)
        elif name == "read_skill":
            from w2_support import W2_SKILLS   # Wave 2's skills, same rule
            walk(tool, W1_SKILLS | W2_SKILLS)
    return cat


def test_core_catalog_is_wave0_byte_identical_apart_from_w1_growth():
    """Pinned at the base commit. Only 1F may re-pin the fixture (its
    look_at overlay parameter, INTERFACES_W1.md §4.3), reporting the delta."""
    with open(os.path.join(FIX, "w1_core_catalog.json")) as f:
        pin = json.load(f)
    text = json.dumps(_strip_w1_growth(_core_catalog()),
                      separators=(",", ":"))
    assert len(text) == pin["bytes"]
    assert hashlib.sha256(text.encode()).hexdigest() == pin["sha256"]


def test_core_catalog_stays_under_the_wave1_ceiling():
    size = len(json.dumps(_core_catalog(), separators=(",", ":")))
    assert size <= W1_CORE_CEILING < 83_000


def test_render_version_stamps_are_unchanged():
    """Cached renders are keyed by these. A Wave 1 rendering change that
    must reach cached renders is a round-lead decision (report it)."""
    import config
    with open(os.path.join(FIX, "w1_render_stamps.json")) as f:
        pin = json.load(f)
    now = {k: getattr(config, k) for k in dir(config)
           if k.endswith("_VERSION") and isinstance(getattr(config, k),
                                                    (int, str))
           and not isinstance(getattr(config, k), bool)}
    assert now == pin


# ── 3. Departments, cards, config blocks, the document ──────────────────

@pytest.mark.parametrize("mod_name", sorted(DEPARTMENTS))
def test_department_modules_pin_their_verbs(mod_name):
    dept, verbs = DEPARTMENTS[mod_name]
    mod = importlib.import_module(f"tools_ext.{mod_name}")
    assert mod.DEPARTMENT == dept and tuple(mod.VERBS) == verbs
    specs = dict(getattr(mod, "TOOL_SPECS", {}) or {})
    assert set(specs) <= set(verbs), "only pinned verbs"
    for verb in verbs:
        if verb not in specs:
            assert verb not in agent_tools.TOOLS, f"{verb} is a core name"


def test_department_verbs_never_collide():
    seen = {}
    for mod_name, (_d, verbs) in DEPARTMENTS.items():
        for v in verbs:
            assert v not in seen, (v, seen.get(v), mod_name)
            seen[v] = mod_name


def test_every_card_id_carries_an_owned_prefix():
    for cid in devices.load_cards():
        prefix = cid.split("_", 1)[0]
        assert prefix in devices.CARD_PREFIXES, cid


def test_wave1_builtin_substitutes_are_pinned():
    assert {"flash_junction", "hue_key_only"} <= set(
        devices.BUILTIN_SUBSTITUTES)


def test_config_has_one_block_per_track_in_order():
    with open(os.path.join(WORKER, "config.py"), encoding="utf-8") as f:
        text = f.read()
    marks = re.findall(r"^# ---- W1 (1[A-F])\b.*?(begin|end)$", text, re.M)
    assert marks == [(t, e) for t in "1A 1B 1C 1D 1E 1F".split()
                     for e in ("begin", "end")]


def test_the_interface_document_names_every_pinned_name():
    with open(os.path.join(WORKER, "devices", "INTERFACES_W1.md"),
              encoding="utf-8") as f:
        doc = f.read()
    names = set(CLASSES) | {v for _d, vs in DEPARTMENTS.values() for v in vs}
    names |= set(PEERS.values()) | {"request_matte", "matte_info",
                                    "program_matte", "handle_matte",
                                    "composite_behind", "check_fx_devices",
                                    "flash_times", "landing", "mask_ops",
                                    "VALMERA_FX_PREVIEW"}
    missing = sorted(n for n in names if f"`{n}" not in doc)
    assert not missing, missing


# ── 4. Shared helpers ────────────────────────────────────────────────────

def test_program_matte_api_is_an_inert_stub():
    plan = fxgraph.FxPlan(W=1080, H=1920, fps=30.0, program_s=10.0)
    info = fxgraph.request_matte(plan, {"id": "a"}, "person", "screen")
    assert info["status"] == "absent" and info["windows"] == []
    assert set(info) == {"label", "status", "source", "reason", "windows",
                         "qa"}
    assert plan.matte_requests == {("person", "screen"): ["a"]}
    assert fxgraph.matte_info(plan)["status"] in fxgraph.MATTE_STATUSES
    assert fxgraph.program_matte(plan, "world") is None
    assert fxgraph.handle_matte(None, "a") is None
    assert fxgraph.empty_plan().matte_requests == {}


def test_mask_peers_resolve_lazily_and_degrade(monkeypatch):
    assert fxgraph.MASK_PEERS == PEERS
    plan = fxgraph.FxPlan(W=64, H=64, fps=25.0, program_s=1.0)
    # absent module -> absent mask, never an exception
    monkeypatch.setitem(fxgraph.MASK_PEERS, "face",
                        "w1_no_such_module:face_mask")
    assert fxgraph.mask(plan, "face") is None
    fake = types.ModuleType("w1_fake_track")
    calls = []

    def face_mask(plan_, name, space, ctx):
        calls.append((name, space))
        return "facepad"
    fake.face_mask = face_mask
    monkeypatch.setitem(sys.modules, "w1_fake_track", fake)
    monkeypatch.setitem(fxgraph.MASK_PEERS, "face", "w1_fake_track:face_mask")
    assert fxgraph.mask(plan, "face:0@world") == "facepad"
    assert calls == [("face:0", "world")]
    assert fxgraph.peer("w1_fake_track:missing", default=7) == 7
    fake.boom = lambda: 1 / 0
    assert fxgraph.peer("w1_fake_track:boom", default="d") == "d"


def test_landing_frames_default_and_hook():
    plan = fxgraph.FxPlan(W=64, H=64, fps=25.0, program_s=10.0)
    assert fxgraph.landing_frames(plan, {"type": "x", "stage": "world",
                                         "start": 2.0, "end": 2.04}) == [50]
    assert fxgraph.landing_frames(plan, {"type": "x", "stage": "junction",
                                         "at": 4.0}) == [100]
    assert fxgraph.landing_frames(plan, {"type": "x",
                                         "stage": "music_bus"}) == []


def test_mask_ops_is_a_single_chain():
    parts = []
    assert fxprims.mask_ops(parts, "m", "o", uid="u") == "o"
    assert parts == ["[m]format=gray[o]"]
    parts = []
    fxprims.mask_ops(parts, "m", "o", uid="u", invert=True, grow_px=2,
                     feather_px=4)
    assert parts == ["[m]format=gray,dilation,dilation,gblur=sigma=2.000,"
                     "negate[o]"]
    parts = []
    fxprims.mask_ops(parts, "m", "o", uid="u", grow_px=-1)
    assert parts == ["[m]format=gray,erosion[o]"]


class _Ctx:
    def __init__(self, plan, stage="world"):
        self.plan, self.stage, self.parts = plan, stage, []
        self.W, self.H = plan.W, plan.H

    def uid(self, p="fx"):
        return self.plan.uid(p)

    def input_label(self, key, stream="v"):
        return self.plan.input_label(key, stream)

    def mask(self, label, space=None):
        return fxgraph.mask(self.plan, label, space or "world", ctx=self)


def test_regions_static_shapes(tmp_path):
    rect = {"shape": "rect", "box": [0.25, 0.5, 0.75, 1.0]}
    im = regions.still(rect, 40, 20)
    assert im.getpixel((10, 10)) == 255 and im.getpixel((5, 5)) == 0
    assert im.getpixel((29, 19)) == 255 and im.getpixel((30, 19)) == 0
    inv = regions.still(dict(rect, invert=True), 40, 20)
    assert inv.getpixel((5, 5)) == 255 and inv.getpixel((10, 10)) == 0
    ell = regions.still({"shape": "ellipse", "box": [0, 0, 1, 1]}, 40, 40)
    assert ell.getpixel((20, 20)) == 255 and ell.getpixel((0, 0)) == 0
    poly = regions.still({"shape": "polygon",
                          "points": [[0, 0], [1, 0], [0, 1]]}, 40, 40)
    assert poly.getpixel((5, 5)) == 255 and poly.getpixel((38, 38)) == 0
    assert regions.bbox(rect, 40, 20) == (10, 10, 20, 10)
    assert regions.bbox(dict(rect, invert=True), 40, 20) is None
    assert regions.bbox({"shape": "face"}, 40, 20) is None
    plan = fxgraph.FxPlan(W=40, H=20, fps=25.0, program_s=2.0,
                          workdir=str(tmp_path))
    item = {"id": "r1"}
    assert regions.prepare(plan, item, rect) is None
    assert os.path.exists(tmp_path / "fxregion_r1_region.png")
    plan.bind_inputs([], 3)
    ctx = _Ctx(plan)
    pad = regions.pad(ctx, item, rect)
    assert pad and ctx.parts == [f"[3:v]format=gray[{pad}]"]
    assert regions.pad(ctx, item, rect) is None        # one pad per key
    # shapes owned by other tracks degrade until they land
    assert regions.prepare(plan, item, {"shape": "card"})[0] == "omit"
    keyed = {"shape": "rect", "keys": [
        {"t": 0, "points": [[0, 0], [0.5, 0.5]]},
        {"t": 1, "points": [[0.5, 0.5], [1, 1]]}]}
    assert regions.prepare(plan, item, keyed)[0] == "omit"
    assert regions.pad(ctx, item, {"shape": "glyph", "ref": "m1"}) is None
    assert regions.prepare(plan, item, {"shape": "glyph", "ref": "m1"}) \
        is None


def test_composite_behind_emits_todays_filters_exactly():
    parts = []
    out = motion_layer.composite_behind(parts, "v0", "bhm0", 0, fps=30.0,
                                        ass_path="/w/b.ass",
                                        fonts_dir="/fonts")
    assert out == "vbh0"
    assert parts == [
        "[v0]split[bhb0][bhf0]",
        "[bhb0]subtitles=filename='/w/b.ass':fontsdir='/fonts'[bht0]",
        "[bhf0][bhm0]alphamerge[bhfa0]",
        "[bht0][bhfa0]overlay=0:0:format=auto:eof_action=pass[vbh0]"]

    class Clip:
        x, y = 10, 20
    parts = []
    motion_layer.composite_behind(
        parts, "v1", "bhm1", 1, fps=30.0,
        motion=(7, {"start": 1.0, "end": 2.0}, Clip()))
    assert parts[0] == "[v1]split[bhb1][bhf1]"
    assert parts[1].startswith("[7:v]setpts=PTS-STARTPTS") and \
        parts[1].endswith("[mgs1c]")
    assert parts[2].startswith("[bhb1][mgs1c]overlay=x=10:y=20")
    assert parts[3:] == ["[bhf1][bhm1]alphamerge[bhfa1]",
                         "[mgs1o][bhfa1]overlay=0:0:format=auto"
                         ":eof_action=pass[vbh1]"]
    # the renderer still emits the same filters inline until 1A switches
    with open(os.path.join(WORKER, "renderer.py"), encoding="utf-8") as f:
        src = f.read()
    for frag in ('f"[{vlabel}]split[bhb{j}][bhf{j}]"',
                 'f"[bhf{j}][bhm{j}]alphamerge[bhfa{j}]"',
                 'f":eof_action=pass[vbh{j}]"',
                 'f":fontsdir=\'{caplib.FONTS_DIR}\'[bht{j}]"'):
        assert frag in src or "composite_behind" in src, frag


def test_batch_write_path_is_apply_batch(monkeypatch):
    from tools_ext import _write
    base = schemas.validate_edl(schemas.default_edl(30), 30).model_dump()
    written = []

    class DB:
        def run(self, fn, *a):
            return {}

    class Ctx:
        db, project_id, duration = DB(), 1, 30.0

        def latest_edl(self):
            return {"version": 4, "json": base}

        def write_edl(self, edl, desc):
            written.append((edl, desc))
            return f"EDL v4 -> v5: {desc}"
    op = {"action": "upsert", "layer": "texts", "id": "t1",
          "value": {"id": "t1", "text": "HI", "start": 1.0, "end": 2.0}}
    assert _write.commit(Ctx(), [op], "added a word") == \
        "EDL v4 -> v5: added a word"
    assert written[0][0]["texts"][0]["id"] == "t1"
    bad = dict(op, value=dict(op["value"], colr="red"))
    assert _write.commit(Ctx(), [bad], "x").startswith(
        "REJECTED (EDL v4 unchanged): Unknown field")
    assert _write.upsert_fx({"id": "a", "type": "accent"}) == {
        "action": "upsert", "layer": "fx", "id": "a",
        "value": {"id": "a", "type": "accent"}}
    assert _write.remove_fx("a") == {"action": "remove", "layer": "fx",
                                     "id": "a"}
    assert _write.set_composite("m1", None)["value"] == {"id": "m1",
                                                         "composite": None}


def _write_card(d, cid, **kw):
    card = {"id": cid, "fx_type": "accent", "status": "live"}
    card.update(kw)
    with open(os.path.join(d, cid + ".json"), "w") as f:
        json.dump(card, f)


def test_device_gate_on_every_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(edit_batch, "_CARDS_DIR", str(tmp_path))
    _write_card(tmp_path, "accent_negative")
    _write_card(tmp_path, "accent_dark", status="dark",
                flag="W1_TEST_DARK_FLAG")
    monkeypatch.delenv("W1_TEST_DARK_FLAG", raising=False)

    def item(dev, **kw):
        return dict({"id": "a", "type": "accent", "device": dev}, **kw)
    edit_batch.check_fx_devices({}, {"fx": [item("accent_negative")]})
    edit_batch.check_fx_devices({}, {"fx": [{"id": "x", "type": "accent"}]})
    with pytest.raises(ValueError, match="not a known device card"):
        edit_batch.check_fx_devices({}, {"fx": [item("nope")]})
    with pytest.raises(ValueError, match="writes fx type accent"):
        edit_batch.check_fx_devices({}, {"fx": [item("accent_negative",
                                                     type="strobe")]})
    with pytest.raises(ValueError, match="dark"):
        edit_batch.check_fx_devices({}, {"fx": [item("accent_dark")]})
    # an item the batch leaves as it was is not re-judged
    same = {"fx": [item("accent_dark")]}
    edit_batch.check_fx_devices(same, copy.deepcopy(same))
    monkeypatch.setenv("W1_TEST_DARK_FLAG", "1")
    edit_batch.check_fx_devices({}, {"fx": [item("accent_dark")]})


def test_flash_safety_reads_a_members_own_flash_times(monkeypatch):
    from fx_schemas import FxItemBase

    class Strobing(FxItemBase):
        type: str = "w1_test_strobe"

        def flash_times(self):
            return [self.start, self.start + 0.1, self.start + 0.2]

    class Quiet(FxItemBase):
        type: str = "w1_test_quiet"
    monkeypatch.setattr(schemas, "FX_MEMBERS", (Strobing, Quiet))
    edl = {"fx": [{"id": "s", "type": "w1_test_strobe", "stage": "world",
                   "start": 1.0, "end": 1.5},
                  {"id": "q", "type": "w1_test_quiet", "stage": "world",
                   "start": 2.0, "end": 2.1}]}
    evs = flash_safety.flash_events(edl)
    assert [e["t"] for e in evs] == [1.0, 1.1, 1.2]
    assert flash_safety.member_flash_times(edl["fx"][1]) is None
    assert flash_safety.member_flash_times({"type": "nope"}) is None


def test_fx_members_import_only_pydantic_and_the_standard_library():
    """The backend loads schemas.py (and so every fx_schemas member) by path
    with only backend/ on sys.path: a member may import pydantic, the
    standard library and its own package, nothing else."""
    allowed = {"typing", "pydantic", "os", "re", "math", "json",
               "typing_extensions", "enum", "dataclasses", "functools"}
    pkg = os.path.join(WORKER, "fx_schemas")
    for fname in sorted(os.listdir(pkg)):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg, fname), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                assert root in allowed, (fname, root)
