"""Track 1B department verbs (tools_ext/subject_tools.py) on a fake project:
moments solved on the speech clock and the cut grid, bindings, device
cards, flash safety (existing flashes counted), colour roles kept as roles,
shorthand sources, refusals, the matte request (never waited for), and the
verb == apply_edit_batch identity. Browserless and model-free, as on the
agent / MCP lanes."""

import copy

import pytest

import agent_tools
import edit_batch
import schemas
import tools_ext
from tools_ext import subject_tools as st

DUR = 20.0
KEYS = {"media/p1/clip.mp4", "media/p1/plate.png"}


def _words():
    text = ("this one decision changed everything we built and nobody saw "
            "it coming until the very last day").split()
    return [{"w": w, "t0": round(1.0 + i * 0.5, 2),
             "t1": round(1.0 + i * 0.5 + 0.4, 2), "p": 0.95}
            for i, w in enumerate(text)]


class FakeDB:
    def __init__(self):
        self.assets = {
            "original": {"sha256": "a" * 64, "storage_key": "orig.mp4"},
            "proxy": {"storage_key": "proxy.mp4",
                      "meta": {"fps": 30.0, "width": 960, "height": 540}}}

    def run(self, fn, *args):
        name = getattr(fn, "__name__", "")
        if name == "project_media_keys":
            return {k: {} for k in KEYS}
        if name == "latest_asset":
            return self.assets.get(args[1])
        if name == "asset_by_key":
            return None
        raise AssertionError(f"unexpected db call {name}")


class Ctx:
    def __init__(self, edl=None, words=None, canvas=False, message=""):
        self.db = FakeDB()
        self.project_id = 7
        self.duration = 0.0 if canvas else DUR
        self.has_main_video = not canvas
        self.index = {} if canvas else {
            "words": words if words is not None else _words(),
            "video": {"duration": DUR, "fps": 30.0, "width": 1920,
                      "height": 1080},
            "shots": [{"start": 0.0}, {"start": 9.0}]}
        base = edl or (schemas.canvas_edl("9:16") if canvas
                       else schemas.default_edl(DUR))
        self.version = 1
        self.edl = base if canvas else \
            schemas.validate_edl(base, self.duration).model_dump()
        self.user_message = message
        self.writes = []

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self.edl)}

    def write_edl(self, new, desc):
        norm = schemas.validate_edl(new, self.duration).model_dump()
        self.writes.append(norm)
        self.edl = norm
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}."


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """No perception download, no executor, no sidecar launch."""
    def no_perception(ctx):
        raise RuntimeError("no proxy here")
    monkeypatch.setattr(agent_tools, "_get_perception", no_perception)
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: None)
    import sidecars
    calls = []

    def ensure(kind, source, ranges, priority="interactive", params=None):
        calls.append((kind, tuple(ranges), priority))
        return {"kind": kind}
    monkeypatch.setattr(sidecars, "ensure", ensure)
    monkeypatch.setattr(sidecars, "status", lambda h: "pending")
    monkeypatch.setattr(sidecars, "estimate_s", lambda h: 42.0)
    return calls


def _fx(ctx):
    return ctx.edl["fx"]


# ── the department ──────────────────────────────────────────────────────

def test_the_verbs_are_installed_in_the_compositing_department():
    for v in st.VERBS:
        assert v in agent_tools.TOOLS and v in tools_ext.EXT_TOOL_NAMES
        assert v in agent_tools.TOOL_DOMAINS["compositing"]
        assert v in agent_tools.WRITE_TOOLS
        assert v not in tools_ext.core_names(agent_tools.TOOLS)
    import json
    compact = agent_tools.openai_tools("m", compact=True,
                                       names=list(st.VERBS))
    assert len(json.dumps(compact, separators=(",", ":"))) <= 3000
    for v in st.VERBS:
        assert len(agent_tools.TOOLS[v][1]) <= 900


# ── add_subject_effect ──────────────────────────────────────────────────

def test_a_fill_at_a_time_is_one_frame_plus_its_decay(_no_side_effects):
    ctx = Ctx()
    out = st.add_subject_effect(ctx, kind="fill", start=4.0)
    assert out.startswith("EDL v1 -> v2"), out
    (it,) = _fx(ctx)
    assert it["type"] == "subject" and it["kind"] == "fill"
    assert it["colour"] == "white" and it["decay_f"] == 2
    assert it["device"] == "subject_fill_flash"
    assert (it["start"], it["end"]) == (4.0, 4.1)       # 3 frames at 30 fps
    assert "Measuring the subject" in out and "42" in out
    assert _no_side_effects == [("matte.person", ((4.0, 4.14),),
                                 "interactive")]


def test_three_white_hits_on_the_strongest_stresses(_no_side_effects):
    ctx = Ctx()
    out = st.add_subject_effect(ctx, kind="fill", on="stress", count=3)
    assert out.startswith("EDL v1 -> v2"), out
    items = _fx(ctx)
    assert 1 <= len(items) <= 3
    words = _words()
    starts = sorted(i["start"] for i in items)
    for it in items:
        b = it["bind"]
        assert b["on"] == "stress" and b["rhythm"] == "speech"
        w = words[b["ref"]]
        assert abs(it["start"] - w["t0"]) <= 1 / 30 + 0.01
    assert all(b - a >= 1.0 - 1e-6 for a, b in zip(starts, starts[1:]))


def test_one_call_puts_a_hit_on_every_cut_of_a_montage():
    edl = schemas.default_edl(DUR)
    edl["keep"] = [[0.5, 2.5], [3.0, 5.0], [6.0, 8.0], [9.0, 11.0],
                   [12.0, 14.0], [15.0, 17.0]]
    ctx = Ctx(edl)
    out = st.add_subject_effect(ctx, kind="negate", on="cut", count=6,
                                frames=2)
    assert out.startswith("EDL"), out
    items = sorted(_fx(ctx), key=lambda i: i["start"])
    assert [i["start"] for i in items] == [2.0, 4.0, 6.0, 8.0, 10.0]
    assert [i["bind"]["ref"] for i in items] == [3.0, 6.0, 9.0, 12.0, 15.0]
    assert all(i["end"] == round(i["start"] + 2 / 30, 2) for i in items)


def test_per_shot_puts_the_strongest_moment_on_every_shot():
    edl = schemas.default_edl(DUR)
    edl["keep"] = [[0.9, 2.4], [2.6, 4.0], [4.2, 5.6], [5.8, 7.2],
                   [7.4, 8.8], [9.0, 9.9]]
    ctx = Ctx(edl)
    out = st.add_subject_effect(ctx, kind="fill", on="word", count=1,
                                per="shot")
    assert out.startswith("EDL"), out
    items = sorted(_fx(ctx), key=lambda i: i["start"])
    assert len(items) == 6
    edges = [0.0, 1.5, 2.9, 4.3, 5.7, 7.1, 8.0]
    for it, a, b in zip(items, edges, edges[1:]):
        assert a <= it["start"] < b, (it["start"], a, b)
    assert st.add_subject_effect(ctx, kind="fill", on="word",
                                 per="scene").startswith("REJECTED")


def test_music_moments_need_a_track_the_user_added():
    out = st.add_subject_effect(Ctx(), kind="fill", on="beat")
    assert out.startswith("REJECTED") and "no music" in out


def test_flash_safety_counts_the_flashes_already_in_the_edit():
    edl = schemas.default_edl(DUR)
    edl["effects"] = {"stylize": [
        {"id": f"fl{i}", "kind": "flash", "start": round(4.0 + i * 0.1, 2),
         "end": round(4.05 + i * 0.1, 2), "intensity": 0.8}
        for i in range(3)]}
    ctx = Ctx(edl)
    out = st.add_subject_effect(ctx, kind="fill", start=4.15)
    assert out.startswith("REJECTED: nothing left"), out
    ctx = Ctx(edl, message="make it flash and strobe hard")
    assert st.add_subject_effect(ctx, kind="fill",
                                 start=4.15).startswith("EDL")


def test_colour_roles_stay_roles_and_kinds_take_their_fields():
    ctx = Ctx()
    st.add_subject_effect(ctx, kind="poster", start=0.0, end=2.5)
    st.add_subject_effect(ctx, kind="poster", start=3.0, end=4.0,
                          colour="#F1EDE4", style={"silhouette": "black"})
    st.add_subject_effect(ctx, kind="glow", start=5.0, end=7.0,
                          colour="accent", style={"radius": 0.06,
                                                  "stage": "screen"})
    st.add_subject_effect(ctx, kind="echo", start=8.0, end=9.0,
                          echo={"tint": "#ff2a2a"})
    by = {i["id"]: i for i in _fx(ctx)}
    assert by["sx1"]["field"] == "accent" and "colour" not in by["sx1"] or \
        by["sx1"].get("colour") is None
    assert by["sx2"]["field"] == "#f1ede4"
    assert by["sx2"]["keep_footage"] is False and by["sx2"]["colour"] == \
        "black"
    assert by["sx3"]["stage"] == "screen" and by["sx3"]["radius"] == 0.06
    assert by["sx4"]["echo"]["mode"] == "clone"
    assert by["sx4"]["echo"]["offsets_f"] == [4, 8]


def test_hue_auto_without_track_1e_keeps_the_subject_only():
    ctx = Ctx()
    out = st.add_subject_effect(ctx, kind="isolate", start=1.0, end=6.0,
                                hue="auto")
    assert out.startswith("EDL"), out
    assert "hue" not in _fx(ctx)[0] or _fx(ctx)[0]["hue"] is None
    assert "mono" in out


def test_authoring_rules_come_from_the_schema():
    ctx = Ctx()
    out = st.add_subject_effect(ctx, kind="glow", start=1.0, end=2.0,
                                scope="background")
    assert out.startswith("REJECTED") and "draws the subject" in out
    assert st.add_subject_effect(ctx, kind="fill", hue="#ff0000",
                                 start=1.0).startswith("REJECTED")
    assert st.add_subject_effect(ctx, kind="sparkle").startswith("REJECTED")
    assert st.add_subject_effect(ctx, kind="fill").startswith("REJECTED")
    assert ctx.writes == []


def test_a_canvas_or_an_older_executor_refuses(monkeypatch):
    assert "no main video" in st.add_subject_effect(
        Ctx(canvas=True), kind="fill", start=1.0)
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: False)
    out = st.add_subject_effect(Ctx(), kind="fill", start=1.0)
    assert out.startswith("REJECTED") and "render service" in out


# ── set_background / remove_subject_effect ──────────────────────────────

def test_backgrounds_take_shorthand_sources_and_cards():
    ctx = Ctx()
    out = st.set_background(ctx, kind="source", start=3.0, end=3.6,
                            source="media/p1/clip.mp4", hold_subject=True,
                            hold_t=2.97)
    assert out.startswith("EDL"), out
    (it,) = _fx(ctx)
    assert it["source"] == {"kind": "asset", "asset_key": "media/p1/clip.mp4",
                            "offset_s": 0.0, "src_t": None,
                            "colour": None, "loop": False}
    assert it["device"] == "background_subject_hold"
    assert it["hold_t"] == 2.97
    out = st.set_background(ctx, kind="gradient", start=5.0, end=7.0,
                            colour="accent", colour2="#112233",
                            style={"angle": 90})
    assert _fx(ctx)[-1]["device"] == "background_swap"
    out = st.set_background(ctx, kind="blur", start=8.0, end=9.0,
                            style={"radius": 0.03})
    assert out.startswith("EDL") and _fx(ctx)[-1]["radius"] == 0.03


def test_a_fast_plate_strobe_needs_the_users_opt_in():
    srcs = ["media/p1/plate.png", 2.0, "#ff0000", "media/p1/clip.mp4"]
    out = st.set_background(Ctx(), kind="strobe", start=3.0, end=4.0,
                            sources=srcs, style={"hold_f": 2})
    assert out.startswith("REJECTED") and "flashes" in out
    ctx = Ctx(message="strobe the background behind him")
    out = st.set_background(ctx, kind="strobe", start=3.0, end=4.0,
                            sources=srcs, style={"hold_f": 2})
    assert out.startswith("EDL"), out
    kinds = [s["kind"] for s in _fx(ctx)[0]["sources"]]
    assert kinds == ["asset", "source", "colour", "asset"]
    assert _fx(ctx)[0]["device"] == "background_strobe"


def test_remove_takes_only_subject_and_background_ids():
    ctx = Ctx()
    st.add_subject_effect(ctx, kind="glow", start=1.0, end=2.0)
    st.set_background(ctx, kind="solid", start=3.0, end=4.0, colour="black")
    assert st.remove_subject_effect(ctx, ids=["nope"]).startswith("REJECTED")
    out = st.remove_subject_effect(ctx, ids=["sx1", "bg1"])
    assert out.startswith("EDL") and _fx(ctx) == []


def test_a_verb_writes_exactly_what_a_stale_client_batch_writes():
    ctx = Ctx()
    st.add_subject_effect(ctx, kind="fill", on="stress", count=2)
    st.set_background(ctx, kind="solid", start=8.0, end=9.0,
                      colour="accent")
    ops = [{"action": "upsert", "layer": "fx", "id": i["id"],
            "value": {k: v for k, v in i.items() if v is not None}}
           for i in _fx(ctx)]
    fresh = schemas.validate_edl(schemas.default_edl(DUR), DUR).model_dump()
    via_batch = edit_batch.apply_batch(fresh, ops, DUR, KEYS)
    assert via_batch["fx"] == ctx.edl["fx"]


def test_the_matte_is_asked_through_track_1as_helpers_when_present(
        monkeypatch):
    """With track 1A merged, a write asks for the person matte through
    matte_track (its matte.person params, its launch rule), so the request
    warms exactly the chunks a render reads, and a lane that may not launch
    launches nothing."""
    import sys
    import types

    import sidecars
    seen = []
    fake = types.ModuleType("matte_track")
    src = sidecars.source_from("a" * 64, "proxy.mp4",
                               {"duration": DUR, "fps": 30.0, "width": 960,
                                "height": 540}, 7, [9.0])
    fake.launch_source = lambda sha, proxy, index, pid: (
        seen.append(("launch", sha, proxy["storage_key"], pid)) or src)
    fake.sidecar_params = lambda: {"downsample_ratio": 0.375}
    monkeypatch.setitem(sys.modules, "matte_track", fake)

    def ensure(kind, source, ranges, priority="interactive", params=None):
        seen.append(("ensure", kind, source is src, params))
        return {"kind": kind}
    monkeypatch.setattr(sidecars, "ensure", ensure)
    ctx = Ctx()
    out = st.add_subject_effect(ctx, kind="fill", start=4.0)
    assert out.startswith("EDL v1 -> v2"), out
    assert seen == [("launch", "a" * 64, "proxy.mp4", 7),
                    ("ensure", "matte.person", True,
                     {"downsample_ratio": 0.375})]
    # a lane where 1A may not launch: nothing is requested from here
    seen.clear()
    fake.launch_source = lambda *a: None
    out = st.add_subject_effect(Ctx(), kind="glow", start=6.0, end=7.0)
    assert out.startswith("EDL v1 -> v2") and "Measuring" not in out
    assert seen == []
