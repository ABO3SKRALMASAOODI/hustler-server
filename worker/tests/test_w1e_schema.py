"""Track 1E schema member: grade_window and finish validate, describe, flash
and travel through apply_edit_batch exactly like the verbs write them.

Every authoring rule lives in ``fx_schemas/finish.py`` (INTERFACES_W1 §5):
a stale MCP client's batch is refused with the very message a verb would
get. The module is also loaded the way the backend loads it (by path,
without the worker on sys.path)."""

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import edit_batch  # noqa: E402
import flash_safety  # noqa: E402
import schemas  # noqa: E402
from fx_schemas import finish as fin  # noqa: E402

DUR = 30.0


def _base():
    edl = schemas.default_edl(DUR)
    edl["keep"] = [[0.0, 10.0], [12.0, 30.0]]
    return schemas.validate_edl(edl, DUR).model_dump()


def _batch(ops, before=None, keys=()):
    return edit_batch.apply_batch(before or _base(), ops, DUR, set(keys))


def _gw(fid="gw1", **kw):
    v = {"id": fid, "type": "grade_window", "stage": "world", "start": 1.0,
         "end": 5.0}
    v.update(kw)
    return {"action": "upsert", "layer": "fx", "id": fid, "value": v}


def _fi(fid="fi1", **kw):
    v = {"id": fid, "type": "finish", "stage": "pre_captions",
         "start": 2.0, "end": 3.0, "kind": "crt"}
    v.update(kw)
    return {"action": "upsert", "layer": "fx", "id": fid, "value": v}


def test_the_module_is_live_with_its_two_types():
    assert fin.LIVE is True
    assert {c.model_fields["type"].default for c in fin.FX_MEMBERS} == \
        {"grade_window", "finish"}
    assert {"grade_window", "finish"} <= set(schemas.FX_AUTHORABLE_TYPES)


@pytest.mark.parametrize("op", [
    _gw(saturation=0.0, ramp_out_f=3, device="grade_section"),
    _gw(isolate={"hue": "#C0392B"}, protect=["skin"],
        device="grade_isolate_hue"),
    _gw(split_tone={"shadows": "#1f4e79", "highlights": "accent",
                    "balance": 0.2}, protect=["skin", "brand"],
        brand_hue="#E9B44C", device="grade_split_tone"),
    _gw(ev=0.5, contrast=1.1, temperature=0.2, tint=-0.1,
        region={"shape": "ellipse", "box": [0.2, 0.2, 0.8, 0.8],
                "feather_px": 12}),
    _gw(stage="screen", saturation=1.2, ramp_in_f=2),
    _fi(kind="thermal", matte_weighted=True, palette="rainbow",
        device="finish_thermal"),
    _fi(kind="pixel_stretch", origin=0.4, direction="right",
        device="finish_pixel_stretch"),
    _fi(kind="halftone", palette="red", scale=1.4, strength=0.8,
        stage="over_type", ramp_in_f=2, ramp_out_f=2),
    _fi(kind="kaleidoscope", stage="world",
        region={"shape": "rect", "box": [0, 0, 0.5, 1]}),
])
def test_valid_items_write_and_remove_through_apply_edit_batch(op):
    base = _base()
    out = _batch([op], base)
    item = next(i for i in out["fx"] if i["id"] == op["id"])
    assert item["type"] == op["value"]["type"]
    if "isolate" in op["value"]:
        assert item["isolate"]["hue"] == "#c0392b"      # stored lower-case
    if "brand_hue" in op["value"]:
        assert item["brand_hue"] == "#e9b44c"
    assert f"[{op['id']}]" in schemas.describe_edl(out, DUR)
    back = _batch([{"action": "remove", "layer": "fx", "id": op["id"]}],
                  out)
    assert schemas.edl_signature(back) == schemas.edl_signature(base)


@pytest.mark.parametrize("op, match", [
    (_gw(), "changes at least one"),
    (_gw(isolate={"hue": "auto"}), "#rrggbb"),
    (_gw(saturation=3.0), "less than or equal"),
    (_gw(saturation=0.0, ramp_in_f=49), "less than or equal"),
    (_gw(saturation=0.0, start=1.0, end=1.05, ramp_in_f=3, ramp_out_f=3),
     "do not fit"),
    (_gw(saturation=0.0, protect=["face"]), "skin"),
    (_gw(saturation=0.0, brand_hue="orange"), "brand_hue"),
    (_gw(saturation=0.0, stage="pre_captions"), "runs at world, screen"),
    (_gw(lut={"asset_key": "media/p1/look.cube", "strength": 2}),
     "less than or equal"),
    (_fi(kind="vhs"), "kind"),
    (_fi(matte_weighted=True), "matte_weighted belongs"),
    (_fi(kind="crt", palette="iron"), "takes no palette"),
    (_fi(kind="thermal", palette="sepia"), "palette for thermal"),
    (_fi(kind="crt", origin=0.5), "belong to pixel_stretch"),
    (_fi(kind="crt", scale=20), "less than or equal"),
    (_fi(region={"shape": "glyph", "ref": "nope"}), "not in this edit"),
    (_fi(colr="red"), "Unknown field"),
])
def test_bad_items_are_refused_with_their_reason(op, match):
    with pytest.raises(ValueError, match=match):
        _batch([op], keys=["media/p1/look.cube"])


def test_a_lut_must_be_project_media():
    op = _gw(lut={"asset_key": "media/other/look.cube"})
    with pytest.raises(ValueError, match="media attached"):
        _batch([op])
    out = _batch([op], keys=["media/other/look.cube"])
    assert out["fx"][0]["lut"]["origin"] == "import"


def test_device_cards_gate_the_items():
    with pytest.raises(ValueError, match="writes fx type finish"):
        _batch([_gw(saturation=0.0, device="finish_crt")])
    with pytest.raises(ValueError, match="not a known device"):
        _batch([_fi(device="finish_hologram")])


# ── flash_times ──────────────────────────────────────────────────────────

def _items(*ops):
    return [o["value"] for o in ops]


def test_flash_times():
    cls = {"grade_window": fin.GradeWindowFx, "finish": fin.FinishFx}

    def ft(op):
        v = op["value"]
        return cls[v["type"]](**v).flash_times()
    # a 3-frame thermal flip over the frame is a flash; a 2 s one is not
    assert ft(_fi(kind="thermal", start=2.0, end=2.12)) == [2.0]
    assert ft(_fi(kind="thermal", start=2.0, end=4.0)) == []
    # a small region never flashes the frame; a ramped one is no flash
    assert ft(_fi(kind="xray", start=2.0, end=2.08,
                  region={"shape": "rect", "box": [0, 0, 0.3, 0.3]})) == []
    assert ft(_fi(kind="xray", start=2.0, end=2.08, ramp_in_f=2)) == []
    assert ft(_fi(kind="film", start=2.0, end=2.08)) == []
    assert ft(_fi(kind="solarise", start=2.0, end=2.08, strength=0.3)) == []
    # grade: an abrupt exposure pop flashes; mono does not
    assert ft(_gw(ev=2.0, start=1.0, end=1.08)) == [1.0]
    assert ft(_gw(saturation=0.0, start=1.0, end=1.08)) == []
    assert ft(_gw(ev=2.0, start=1.0, end=3.0)) == []


def test_flash_safety_counts_short_flips():
    edl = _batch([_fi(f"t{i}", kind="thermal", start=2.0 + 0.2 * i,
                      end=2.0 + 0.2 * i + 0.08) for i in range(5)])
    a = flash_safety.assess(edl)
    assert len(flash_safety.flash_events(edl)) == 5
    assert a["peak_per_s"] == 5 and a["over_limit"] is True
    assert a["contains_flashing"] is False          # nobody opted in
    assert flash_safety.assess(edl, request_text="strobe it")[
        "contains_flashing"] is True
    kept, thinned = flash_safety.plan(flash_safety.flash_events(edl), 25.0)
    assert len(kept) == 3 and len(thinned) == 2


# ── the backend's way of loading the member ──────────────────────────────

BACKEND_LOAD = r"""
import importlib.util, json, sys, copy
spec = importlib.util.spec_from_file_location(
    "worker_schemas", sys.argv[1] + "/schemas.py")
m = importlib.util.module_from_spec(spec)
sys.modules["worker_schemas"] = m
spec.loader.exec_module(m)
assert {"grade_window", "finish"} <= set(m.FX_AUTHORABLE_TYPES), m.FX_TYPES
edl = m.default_edl(20.0)
edl["fx"] = [{"id": "g", "type": "grade_window", "stage": "world",
              "start": 0, "end": 5, "isolate": {"hue": "#C8202A"}},
             {"id": "f", "type": "finish", "stage": "over_type",
              "start": 5, "end": 6, "kind": "lcd", "scale": 1.2}]
out = m.validate_edl(edl, 20.0).model_dump()
print(json.dumps([i["type"] for i in out["fx"]]))
print("worker modules imported:",
      sorted(n for n in ("config", "renderer", "gradewin", "finish",
                         "lutlib", "fxgraph") if n in sys.modules))
"""


def test_the_member_loads_by_path_without_the_worker_tree(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VALMERA_FX_PREVIEW")}
    r = subprocess.run([sys.executable, "-I", "-c", BACKEND_LOAD, WORKER],
                       cwd=str(tmp_path), env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    lines = r.stdout.strip().splitlines()
    assert json.loads(lines[0]) == ["grade_window", "finish"]
    assert lines[1] == "worker modules imported: []"


def test_landing_frames():
    import fxgraph
    plan = fxgraph.FxPlan(W=64, H=64, fps=25.0, program_s=20.0)
    drop = {"id": "g", "type": "grade_window", "stage": "world",
            "start": 0.0, "end": 3.64, "saturation": 0.0, "ramp_out_f": 3}
    assert fxgraph.landing_frames(plan, drop) == [91]
    plain = dict(drop, ramp_out_f=0, start=2.0)
    assert fxgraph.landing_frames(plan, plain) == [50]
    fin = {"id": "f", "type": "finish", "stage": "world", "start": 1.0,
           "end": 2.0, "kind": "crt"}
    assert fxgraph.landing_frames(plan, fin) == [25]
