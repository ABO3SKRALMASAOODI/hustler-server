"""Track 1B: the ``subject`` and ``background`` fx types as data.

Registration (live), every authoring rule in the member classes (so a verb
and a stale client's apply_edit_batch are refused identically), the
apply_edit_batch write/remove round trip for every kind with its device
card, the flash-safety reading (``flash_times``), the ledger family, and
the device cards' owner rules. No rendering here (tests/test_w1b_render.py).
"""

import copy
import json
import os

import pytest

import devices
import edit_batch
import flash_safety
import schemas
import taste

HERE = os.path.dirname(os.path.abspath(__file__))
DUR = 30.0
KEYS = {"media/p1/plate.png", "media/p1/clip.mp4"}


def _base():
    return schemas.validate_edl(schemas.default_edl(DUR), DUR).model_dump()


def _op(item):
    return {"action": "upsert", "layer": "fx", "id": item["id"],
            "value": item}


def _sx(fid="sx1", **kw):
    it = {"id": fid, "type": "subject", "stage": "world", "start": 4.0,
          "end": 4.12, "kind": "fill", "colour": "white"}
    it.update(kw)
    return it


def _bg(fid="bg1", **kw):
    it = {"id": fid, "type": "background", "stage": "world", "start": 3.0,
          "end": 5.0, "kind": "solid", "colour": "accent"}
    it.update(kw)
    return it


# one item per kind, each with the card its verb writes
EVERY_KIND = [
    _sx("f", decay_f=2, device="subject_fill_flash",
        bind={"on": "stress", "ref": 12, "rhythm": "speech"}),
    _sx("p", kind="poster", colour=None, field="accent", start=5.0, end=7.0,
        device="subject_poster"),
    _sx("ps", kind="poster", field="#f1ede4", keep_footage=False,
        colour="black", start=5.0, end=7.0, device="subject_poster"),
    _sx("g", kind="glow", colour="#ffd23f", radius=0.05, intensity=0.9,
        start=7.0, end=9.0, stage="screen", device="subject_glow"),
    _sx("o", kind="outline", width_px=4, start=2.0, end=3.0,
        device="subject_outline"),
    _sx("n", kind="negate", colour=None, scope="background", start=1.0,
        end=1.08, device="subject_negate"),
    _sx("e", kind="exposure", colour=None, scope="background", ev=-1.5,
        saturation=0.0, start=12.0, end=14.0, device="subject_exposure"),
    _sx("i", kind="isolate", colour=None, hue="#C0392B", hue_op="union",
        start=14.0, end=20.0, device="subject_isolate"),
    _sx("c", kind="echo", colour=None, start=20.0, end=21.0,
        echo={"mode": "clone", "offsets_f": [4, 8], "opacity": 0.75,
              "tint": "#ff2a2a"}, device="subject_echo"),
    _bg("bs", device="background_swap"),
    _bg("bgr", kind="gradient", colour="accent", colour2="#112233",
        angle=90, device="background_swap"),
    _bg("bb", kind="blur", colour=None, radius=0.025,
        device="background_blur"),
    _bg("br", kind="radial_blur", colour=None, radius=0.08,
        device="background_blur"),
    _bg("bsrc", kind="source", colour=None,
        source={"kind": "asset", "asset_key": "media/p1/clip.mp4",
                "offset_s": 1.5, "loop": True}, device="background_swap"),
    _bg("bh", kind="source", colour=None, start=15.0, end=15.6,
        source={"kind": "next"}, hold_subject=True, hold_t=14.96,
        device="background_subject_hold"),
    _bg("bst", kind="strobe", colour=None, start=22.0, end=22.4, hold_f=4,
        sources=[{"kind": "asset", "asset_key": "media/p1/plate.png"},
                 {"kind": "source", "src_t": 2.0},
                 {"kind": "colour", "colour": "accent"}],
        device="background_strobe"),
]


def _clean(it):
    return {k: v for k, v in it.items() if v is not None}


def test_both_types_are_live_with_their_pinned_shape():
    from fx_schemas import subject
    assert subject.LIVE is True
    assert {"subject", "background"} <= set(schemas.FX_TYPES)
    assert {"subject", "background"} <= set(schemas.FX_AUTHORABLE_TYPES)
    for cls in subject.FX_MEMBERS:
        assert cls.STAGES == ("world", "screen")
        assert cls.COMPILER == "subject_fx:COMPILER"


@pytest.mark.parametrize("item", EVERY_KIND, ids=lambda i: i["id"])
def test_every_kind_is_written_and_removed_through_apply_edit_batch(item):
    """TOOL REACH: what the verbs write, a stale MCP client writes and
    removes through apply_edit_batch, card gate included."""
    base = _base()
    it = _clean(item)
    out = edit_batch.apply_batch(base, [_op(it)], DUR, KEYS)
    got = [f for f in out["fx"] if f["id"] == it["id"]]
    assert got and got[0]["type"] == it["type"]
    assert f"[{it['id']}]" in schemas.describe_edl(out, DUR)
    back = edit_batch.apply_batch(out, [{"action": "remove", "layer": "fx",
                                         "id": it["id"]}], DUR, KEYS)
    assert schemas.edl_signature(back) == schemas.edl_signature(base)


def test_every_kind_in_one_batch_and_flash_events():
    base = _base()
    ops = [_op(_clean(i)) for i in EVERY_KIND]
    out = edit_batch.apply_batch(base, ops, DUR, KEYS)
    assert len(out["fx"]) == len(EVERY_KIND)
    evs = flash_safety.flash_events(out)
    ids = [e["id"] for e in evs]
    # fill, the 2-frame negate, the hold swap and every strobe change flash
    assert ids.count("f") == 1 and ids.count("n") == 1
    assert ids.count("bh") == 1 and ids.count("bst") == 3
    for quiet in ("p", "ps", "g", "o", "e", "i", "c", "bs", "bb"):
        assert quiet not in ids, quiet
    counts = taste.device_counts(out)
    assert counts["matte"] == len(EVERY_KIND)


@pytest.mark.parametrize("item, match", [
    (_sx(kind="poster", colour=None), "names its field"),
    (_sx(kind="glow", stage="over_type"), "runs at world, screen"),
    (_sx(hue="#ff0000"), "hue belongs to kind isolate"),
    (_sx(kind="isolate", colour=None, hue="red"), "hue is 'auto' or #rrggbb"),
    (_sx(kind="isolate", colour=None, hue_op="union"), "hue_op needs a hue"),
    (_sx(kind="glow", scope="background"), "draws the subject"),
    (_sx(kind="echo", colour=None), "names its echo offsets"),
    (_sx(kind="echo", colour=None, echo={"offsets_f": [0]}), "1..240"),
    (_sx(kind="echo", colour=None, echo={"offsets_f": [4, 4]}), "differ"),
    (_sx(kind="fill", radius=0.05), "radius belongs to kind glow"),
    (_sx(kind="negate", colour=None, decay_f=2), "decay_f belongs"),
    (_sx(kind="fill", colour="redish"), "colour must be one of"),
    (_sx(region={"shape": "glyph", "ref": "nope"}), "not in this edit"),
    (_bg(kind="gradient"), "names colour2"),
    (_bg(kind="source", colour=None), "names its source"),
    (_bg(kind="strobe", colour=None), "names its sources"),
    (_bg(kind="source", colour=None, source={"kind": "source",
                                             "src_t": 99.0}),
     "past the end of the main video"),
    (_bg(kind="source", colour=None, source={"kind": "asset",
                                             "asset_key": "media/x/y.mp4"}),
     "not attached|not part of this project|asset"),
    (_bg(hold_t=2.0), "hold_t needs hold_subject"),
    (_bg(hold_subject=True, hold_t=31.0), "past the program end"),
    (_sx(device="no_such_card"), "not a known device card"),
    (_bg(device="subject_fill_flash"), "writes fx type subject"),
])
def test_bad_shapes_are_refused_with_their_rule(item, match):
    import re
    with pytest.raises(Exception) as e:
        edit_batch.apply_batch(_base(), [_op(_clean(item))], DUR, KEYS)
    assert re.search(match, str(e.value)), str(e.value)


def test_a_canvas_program_refuses_subject_effects():
    canvas = schemas.canvas_edl("9:16")
    with pytest.raises(Exception, match="canvas"):
        edit_batch.apply_batch(canvas, [_op(_sx())], 0.0, KEYS)


def test_hue_is_stored_lower_case():
    out = edit_batch.apply_batch(_base(), [_op(_clean(EVERY_KIND[7]))], DUR,
                                 KEYS)
    assert out["fx"][0]["hue"] == "#c0392b"


@pytest.mark.parametrize("item, times", [
    (_sx(), [4.0]),
    (_sx(kind="negate", colour=None, start=1.0, end=1.08), [1.0]),
    (_sx(kind="negate", colour=None, start=1.0, end=3.0), []),
    (_sx(kind="poster", colour=None, field="black", start=5.0, end=5.3),
     [5.0]),
    (_sx(kind="poster", colour=None, field="black", start=5.0, end=7.0), []),
    (_sx(kind="exposure", colour=None, ev=-2.0, start=1.0, end=1.2), [1.0]),
    (_sx(kind="exposure", colour=None, ev=-0.5, start=1.0, end=1.2), []),
    (_sx(kind="glow", start=1.0, end=1.1), []),
    (_bg(start=2.0, end=2.3), [2.0]),
    (_bg(start=2.0, end=4.0), []),
])
def test_flash_times_reads_each_kind(item, times):
    cls = {c.model_fields["type"].default: c for c in schemas.FX_MEMBERS}
    got = cls[item["type"]].model_validate(_clean(item)).flash_times()
    assert got == times


def test_a_fast_plate_strobe_is_over_the_flash_limit_unless_asked():
    st = _bg(kind="strobe", colour=None, start=2.0, end=3.0, hold_f=2,
             sources=[{"kind": "colour", "colour": "white"},
                      {"kind": "colour", "colour": "black"}])
    out = edit_batch.apply_batch(_base(), [_op(_clean(st))], DUR, KEYS)
    a = flash_safety.assess(out, fps=30.0)
    assert a["over_limit"] and not a["contains_flashing"]
    b = flash_safety.assess(out, fps=30.0, request_text="strobe the "
                            "background behind him")
    assert b["contains_flashing"]


def test_old_edls_keep_their_signature_with_1b_live():
    with open(os.path.join(HERE, "fixtures", "w0_inert_corpus.json")) as f:
        cases = json.load(f)
    import hashlib
    for name, case in sorted(cases.items()):
        new = schemas.validate_edl(copy.deepcopy(case["raw"]),
                                   case["duration"]).model_dump()
        sig = hashlib.sha256(schemas.edl_signature(new).encode()).hexdigest()
        assert sig == case["signature_sha256"], name


# ── the device cards ────────────────────────────────────────────────────

CARDS = ("subject_fill_flash", "subject_poster", "subject_glow",
         "subject_outline", "subject_negate", "subject_exposure",
         "subject_isolate", "subject_echo", "background_swap",
         "background_blur", "background_subject_hold", "background_strobe")


def test_track_1b_ships_its_cards_and_they_keep_the_owner_rules():
    cards = devices.load_cards()
    mine = {k: v for k, v in cards.items()
            if k.startswith(("subject_", "background_"))}
    assert set(mine) == set(CARDS)
    for cid, c in mine.items():
        assert c["family"] == "matte_fx", cid
        assert c["fx_type"] in ("subject", "background"), cid
        assert c["verb"] in ("add_subject_effect", "set_background"), cid
        assert c["owner_flags"]["adds_sound"] is False, cid
        assert c["owner_flags"]["adds_zoom"] is False, cid
        assert c["needs"]["sidecars"] == ["matte.person"], cid
        assert devices.proposable(c)[0], cid
        # every lane feature a card needs is one the production lanes state
        for f in c["needs"]["lane_features"]:
            assert f in devices.lane_features("interactive"), (cid, f)
        # params keys are fx item field names (1F builds candidates on them)
        cls = {k.model_fields["type"].default: k
               for k in schemas.FX_MEMBERS}[c["fx_type"]]
        assert set(c["params"]) <= set(cls.model_fields), cid
        assert c["skill"] == "subject-fx"
    flashy = {k for k, c in mine.items() if c["owner_flags"]["flash"]}
    assert flashy == {"subject_fill_flash", "subject_negate",
                      "background_subject_hold", "background_strobe"}


def test_cards_resolve_to_their_substitutes_without_a_matte():
    absent = {"matte.person": "absent"}
    want = {"subject_fill_flash": "full_frame_flash",
            "subject_poster": "colour_field_card",
            "subject_glow": "halation", "subject_isolate": "hue_key_only",
            "background_subject_hold": "flash_junction"}
    for cid, sub in want.items():
        d = devices.resolve_detail(cid, lane="interactive",
                                   sidecar_status=absent, frames=30)
        assert (d["rung"], d["substitute"]) == ("substitute", sub), cid
    for cid in ("subject_outline", "subject_negate", "subject_exposure",
                "subject_echo", "background_swap", "background_blur",
                "background_strobe"):
        d = devices.resolve_detail(cid, lane="interactive",
                                   sidecar_status=absent, frames=30)
        assert d["rung"] == "omit", cid
    ready = {"matte.person": "ready"}
    for cid in CARDS:
        assert devices.resolve(cid, lane="batch", sidecar_status=ready,
                               frames=60)[0] == "full", cid


def test_card_params_build_valid_items():
    """A planner (1F) writes a card's first choice of every param: that item
    must validate."""
    cards = devices.load_cards()
    for cid in CARDS:
        c = cards[cid]
        it = {"id": "x", "type": c["fx_type"], "stage": c["stage"],
              "start": 2.0, "end": 2.5, "device": cid}
        for k, v in c["params"].items():
            it[k] = v[0] if isinstance(v, list) else v
        if it["type"] == "background":
            k = it.get("kind")
            if k in ("solid", "gradient"):
                it.setdefault("colour", "accent")
            if k == "gradient":
                it["colour2"] = "black"
            if k == "source":
                it["source"] = {"kind": "next"}
            if k == "strobe":
                it["sources"] = [{"kind": "colour", "colour": "white"},
                                 {"kind": "colour", "colour": "black"}]
            if k not in ("blur", "radial_blur"):
                it.pop("radius", None)
            if k not in ("solid", "gradient"):
                it.pop("colour", None)
        edit_batch.apply_batch(_base(), [_op(it)], DUR, KEYS)
