"""Wave 3 (3F) and the W3 verbs: the attachment language end to end, the
world / element / layout verbs on a fake tool context, pre-production
(plan_footage, the acquisition dialogue), the upload warm-up policy, the
genre D pack and the world candidates' per-line no-repeat budget.

Every verb commits through ``tools_ext._write.commit`` (edit_batch.apply_batch),
so what a verb writes, a stale client's batch writes and removes the same
way. No database, no proxy, no browser, no model.
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

import attachment                                         # noqa: E402
import coverage                                           # noqa: E402
import edit_batch                                         # noqa: E402
import schemas                                            # noqa: E402
import sidecars                                           # noqa: E402
import warmers                                            # noqa: E402
import world_planner                                      # noqa: E402
import w3_support as w3s                                  # noqa: E402
from tools_ext import element_tools as E                  # noqa: E402
from tools_ext import layout_tools as L                   # noqa: E402
from tools_ext import preprod_tools as PP                 # noqa: E402
from tools_ext import world_tools as WT                   # noqa: E402

DUR = 20.0
WORDS = [{"w": w, "t0": round(0.4 + 0.42 * i, 2),
          "t1": round(0.4 + 0.42 * i + 0.34, 2), "p": 0.95}
         for i, w in enumerate(
             ("They promised us flying cars. Instead we got one hundred "
              "forty characters. That is not nothing. But it is not the "
              "future we were promised at all.").split())]


class _DB:
    def run(self, fn, *a):
        return {"media/p1/a.mp4": {"duration_s": 4.0},
                "media/p1/b.mp4": {"duration_s": 4.0},
                "media/p1/logo.png": {"duration_s": None}}


class Ctx:
    def __init__(self, edl):
        self.db, self.project_id, self.duration = _DB(), 1, DUR
        self.index = {"words": WORDS, "video": {
            "fps": 25.0, "duration": DUR, "width": 1920, "height": 1080}}
        self.user_message = ""
        self.version = 3
        self.edl = schemas.validate_edl(copy.deepcopy(edl),
                                        DUR).model_dump()
        self.written = []
        self.pending_images = []

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self.edl)}

    def write_edl(self, edl, desc):
        self.edl = edl
        self.written.append(desc)
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


BASE = {"keep": [[0.0, 12.0]], "frame": {"ratio": "9:16", "mode": "crop"}}


@pytest.fixture(autouse=True)
def _no_executor_check(monkeypatch):
    monkeypatch.setattr(WT, "_executor_refusal", lambda: None)
    monkeypatch.setattr(WT, "_launch_source", lambda ctx: None)


def _motion(ctx, mid):
    return next(m for m in ctx.edl.get("motion") or [] if m["id"] == mid)


def _fx(ctx, t):
    return [i for i in ctx.edl.get("fx") or [] if i["type"] == t]


def _removable(ctx):
    """Everything the verbs wrote is removed by a plain batch too."""
    ops = [{"action": "remove", "layer": "motion", "id": m["id"]}
           for m in ctx.edl.get("motion") or []]
    ops += [{"action": "remove", "layer": "fx", "id": f["id"]}
            for f in ctx.edl.get("fx") or []]
    if ops:
        out = edit_batch.apply_batch(ctx.edl, ops, DUR, set())
        assert not out.get("motion") and not out.get("fx")


# ── AT-3F-2: the attachment language, end to end ─────────────────────────

def test_put_the_price_on_the_desk():
    ctx = Ctx(BASE)
    res = WT.place_in_world(ctx, "put the price on the desk", text="$40",
                            t=3.0, quad=[[0.2, 0.62], [0.8, 0.62],
                                         [0.95, 0.95], [0.05, 0.95]])
    assert res.startswith("EDL v3 -> v4"), res
    (pl,) = _fx(ctx, "plane")
    assert pl["surface"] == "desk" and pl["device"] == "plane_type"
    m = _motion(ctx, ctx.edl["motion"][0]["id"])
    assert m["anchor"]["to"] == f"plane:{pl['id']}"
    assert m["composite"]["blend"] == "multiply"
    # the quad was mapped back to SOURCE fractions through the 9:16 crop
    assert pl["quad"][0][0] > 0.2 and pl["quad"][1][0] < 0.8
    _removable(ctx)


def test_hide_the_word_behind_the_laptop():
    ctx = Ctx(BASE)
    res = WT.place_in_world(ctx, "hide the word behind the laptop",
                            text="SECRET", t=5.0,
                            box=[0.2, 0.7, 0.8, 1.0])
    assert "REJECTED" not in res, res
    (ob,) = _fx(ctx, "object")
    assert ob["label"] == "laptop" and ob["device"] == "object_occluder"
    assert ob["end"] - ob["start"] <= 6.0
    m = ctx.edl["motion"][0]
    assert m["anchor"]["to"] == "screen"
    assert m["anchor"]["occluders"] == [f"object:{ob['id']}"]
    _removable(ctx)


def test_make_the_label_follow_his_hand():
    edl = dict(BASE, motion=[{"id": "lab1", "template": "glass_card",
                              "start": 2.0, "end": 4.0,
                              "params": {"title": "3X"}}])
    ctx = Ctx(edl)
    res = WT.place_in_world(ctx, "make the label follow his hand",
                            motion_id="lab1")
    assert "REJECTED" not in res, res
    a = _motion(ctx, "lab1")["anchor"]
    assert a["to"].startswith("hand.")
    _removable(ctx)


def test_arc_it_round_his_head():
    ctx = Ctx(BASE)
    res = WT.place_in_world(ctx, "arc it round his head",
                            text="THE FUTURE", t=6.0)
    assert "REJECTED" not in res, res
    m = ctx.edl["motion"][0]
    assert m["template"] == "el_orbit" and m["anchor"]["to"] == "head"
    _removable(ctx)


@pytest.mark.parametrize("phrase,relation,target", [
    ("put the price on the desk", "on", "plane"),
    ("hide the word behind the laptop", "behind", "object"),
    ("make the label follow his hand", "follow", "hand"),
    ("arc it round his head", "around", "head"),
    ("the number on her fingertip", "follow", "hand"),
    ("type on the floor under my shadow", "on", "plane"),
])
def test_the_grammar_reads_the_owner_phrases(phrase, relation, target):
    p = attachment.parse(phrase)
    assert p["relation"] == relation and p["target"] == target, p


def test_an_unreadable_place_is_refused_with_the_grammar():
    ctx = Ctx(BASE)
    res = WT.place_in_world(ctx, "somewhere nice", text="HI", t=1.0)
    assert res.startswith("REJECTED") and "behind the" in res
    assert ctx.written == []


# ── the world verbs ────────────────────────────────────────────────────

def test_track_then_attach_then_remove():
    edl = dict(BASE, motion=[{"id": "m1", "template": "word_slam",
                              "start": 1.0, "end": 3.0,
                              "params": {"text": "HI"}}])
    ctx = Ctx(edl)
    assert "REJECTED" not in WT.track_point(ctx, 1.5, 0.5, 0.4)
    (tr,) = _fx(ctx, "track")
    res = WT.attach(ctx, "m1", to=f"track:{tr['id']}", pin=[0.5, 0.5])
    assert "REJECTED" not in res, res
    assert _motion(ctx, "m1")["anchor"]["to"] == f"track:{tr['id']}"
    # removing the definition detaches what names it
    assert "REJECTED" not in WT.remove_world(ctx, [tr["id"]])
    assert _fx(ctx, "track") == []
    assert _motion(ctx, "m1").get("anchor") is None


def test_attach_to_a_hand_hides_when_the_hand_leaves():
    edl = dict(BASE, motion=[{"id": "c1", "template": "glass_card",
                              "start": 1.0, "end": 3.0,
                              "params": {"title": "X"}}])
    ctx = Ctx(edl)
    WT.attach(ctx, "c1", to="hand.index_tip")
    assert _motion(ctx, "c1")["anchor"]["lost"] == "hide"
    assert "detached" in WT.attach(ctx, "c1", clear=True)
    assert _motion(ctx, "c1").get("anchor") is None


def test_an_object_needs_a_box_or_points():
    ctx = Ctx(BASE)
    assert WT.select_object(ctx, 2.0).startswith("REJECTED")
    assert "REJECTED" not in WT.select_object(ctx, 2.0,
                                              box=[0.3, 0.6, 0.7, 0.9])
    (ob,) = _fx(ctx, "object")
    assert ob["prompt"]["box"][0] < ob["prompt"]["box"][2]


# ── element and layout verbs ───────────────────────────────────────────

def test_light_dissolve_writes_the_light_and_its_embers():
    ctx = Ctx(BASE)
    res = E.add_element(ctx, "light_dissolve", 9.0, 11.0)
    assert "REJECTED" not in res, res
    (lt,) = _fx(ctx, "light")
    assert lt["kind"] == "dissolve" and not lt.get("flash_times")
    em = ctx.edl["motion"][0]
    assert em["template"] == "el_particles"
    assert em["params"]["emit"] == "edge"
    _removable(ctx)


@pytest.mark.parametrize("kind", ["embers", "lightning", "arrow", "shapes",
                                  "pictogram", "light_trail", "light_rim"])
def test_every_element_kind_writes(kind):
    ctx = Ctx(BASE)
    res = E.add_element(ctx, kind, 2.0, 4.0, at=[2.5])
    assert "REJECTED" not in res, (kind, res)
    _removable(ctx)


def test_layout_verbs_write_and_hud_avoids_the_corner_mark():
    ctx = Ctx(BASE)
    assert "REJECTED" not in L.add_collage(
        ctx, "mosaic", ["asset:media/p1/a.mp4", "source:3.0",
                        "asset:media/p1/b.mp4"], 0.0, 4.0, hero=1,
        title="DEV DAY")
    assert "REJECTED" not in L.set_stack(
        ctx, ["source:2.0", "asset:media/p1/a.mp4"], 4.0, 7.0,
        labels=["NOW", "THEN"])
    assert "REJECTED" not in L.add_hud(ctx, tl="DEV DAY", tr="SF")
    assert "REJECTED" not in L.add_breakdown(ctx, 9.0, 12.0)
    assert "REJECTED" not in L.add_device_stage(
        ctx, "asset:media/p1/a.mp4", 7.0, 9.0, device="laptop")
    hud = next(m for m in ctx.edl["motion"] if m["template"] == "hud")
    mark = [float(x) for x in hud["params"]["mark"]]
    assert mark[0] < 0.5 and mark[1] < 0.2   # the top-left corner-mark zone
    _removable(ctx)


# ── AT-3F-3: pre-production ────────────────────────────────────────────

BRIEFS = {
    "16": "floor type with my shadow, a word hidden behind the calendar, "
          "type running along my outline, silhouettes in colour fields",
    "21": "type on the wall and on the desk, a word behind the laptop, "
          "arcs, a card on my fingertip, no device twice",
    "38": "poster colour fields, BECAUSE behind me, an arc round my head, "
          "type on the desk, a vector burst",
}


@pytest.mark.parametrize("ref", sorted(BRIEFS))
def test_plan_footage_covers_a_reference_brief(ref):
    plan = coverage.plan_footage(BRIEFS[ref])
    assert 1 <= len(plan["shots"]) <= coverage.MAX_SHOTS
    assert plan["minutes"] <= coverage.MAX_MINUTES
    assert plan["covered"] >= 0.9, plan["missing"]
    for c in plan["shots"]:
        assert c["svg"].startswith("<svg") and c["svg"].endswith("</svg>")
        for k in ("framing", "lens", "camera", "light", "action"):
            assert c[k]
    # shots that share a camera position are filmed together
    setups = [c["setup"] for c in plan["shots"]]
    assert setups == sorted(setups, key=setups.index)


def test_the_acquisition_dialogue_is_at_most_three_priced_options():
    plan = coverage.plan_footage(BRIEFS["21"])
    for up in (0.0, 4.5):
        opts = coverage.acquisition(plan, up)
        assert 2 <= len(opts) <= 3
        for o in opts:
            assert o["time"] and o["cost"] and o["gives"]
        assert not any("generat" in o["gives"] and "no generated" not in
                       o["gives"] for o in opts)


def test_plan_footage_verb_and_its_sheet(tmp_path, monkeypatch):
    ctx = Ctx(BASE)
    out = PP.plan_footage(ctx, BRIEFS["16"], have=["talking head"])
    assert "Options:" in out and "1)" in out and "4)" not in out
    assert "A-cam talking head" not in out          # the user has it
    assert PP.plan_footage(ctx, "").startswith("plan_footage needs")
    monkeypatch.setattr(PP, "_sees", lambda ctx: True)
    PP.plan_footage(ctx, BRIEFS["38"])
    (label, path), = ctx.pending_images
    assert os.path.getsize(path) > 1000
    os.unlink(path)


def test_an_overfull_brief_names_what_it_cannot_cover():
    plan = coverage.plan_footage(
        "type on the floor, the price on the desk, a word behind the "
        "laptop, the number on my fingertip, arc it round his head, embers "
        "when I finish; show the app; a voiceover; the kit flat-lay; walk "
        "past the door jamb; macro of my eye")
    assert plan["minutes"] <= coverage.MAX_MINUTES
    assert len(plan["shots"]) <= coverage.MAX_SHOTS
    assert plan["missing"] and plan["covered"] < 1.0
    assert "Not coverable" in coverage.format_plan(
        plan, coverage.acquisition(plan))


# ── warmers (AT-3F-5) ──────────────────────────────────────────────────

def test_the_upload_policy_spends_its_budget_in_order():
    short = warmers.policy(90.0)
    assert short[0] == ("matte.person", [(0.0, 90.0)], "prefetch")
    assert {k for k, _r, _p in short} == set(warmers.WARM_KINDS)
    long = warmers.policy(3600.0, [{"start": 0.0, "end": 30.0},
                                   {"start": 100.0, "end": 400.0},
                                   {"start": 900.0, "end": 960.0}])
    ranges = long[0][1]
    assert ranges[0] == (0.0, 60.0)
    assert (100.0, 130.0) in ranges and (900.0, 930.0) in ranges
    cost = sum(warmers._cost_s("matte.person", b - a) for a, b in ranges)
    assert cost <= warmers.BUDGET_S + 1e-6
    tight = warmers.policy(3600.0, budget_s=60.0)
    assert tight[0][1][0][1] - tight[0][1][0][0] < 60.0
    assert all(k not in warmers.NEVER for k, _r, _p in long)


def test_warming_is_off_unless_the_owner_turns_it_on(monkeypatch):
    calls = []
    monkeypatch.setattr(sidecars, "ensure",
                        lambda kind, src, ranges, priority="interactive",
                        params=None: calls.append((kind, priority)) or kind)
    src = w3s.source(dur=40.0)
    monkeypatch.delenv("SIDECAR_WARM_UPLOAD", raising=False)
    assert warmers.warm_upload(src) == {} and calls == []
    monkeypatch.setenv("SIDECAR_WARM_UPLOAD", "1")
    out = warmers.warm_upload(src)
    assert set(out) == set(warmers.WARM_KINDS)
    assert all(p == "prefetch" for _k, p in calls)


def test_a_warmed_upload_reads_ready(tmp_path):
    root = str(tmp_path / "store")
    prev = sidecars.use_store(sidecars.LocalStore(root))
    try:
        src = w3s.source(dur=12.0)
        (kind, ranges, _p), _face = warmers.policy(src.duration_s)
        assert warmers.warm_state(src, ranges)["matte.person"] == 0.0
        clip = w3s.gray_mask_clip(str(tmp_path / "m.mp4"), 64, 36, 25.0,
                                  25, (0, 0, 32, 36))
        for span in sidecars.chunks_for(src.shots, src.duration_s, ranges):
            w3s.put_chunk(root, src, "matte.person", span, path=clip)
        sidecars._reset_caches()    # the batch lane wrote them meanwhile
        assert warmers.warm_state(src, ranges)["matte.person"] == 1.0
    finally:
        sidecars.use_store(prev)


# ── the genre D pack ───────────────────────────────────────────────────

def test_the_genre_d_pack_names_real_devices_verbs_and_skills():
    import agent_prompt
    import devices
    import tools_ext
    with open(os.path.join(WORKER, "formats", "D.json")) as fh:
        pack = json.load(fh)
    cards = set(devices.load_cards()) if hasattr(devices, "load_cards") \
        else {os.path.splitext(f)[0] for f in os.listdir(
            os.path.join(WORKER, "devices", "cards"))}
    for fam in pack["palette"].values():
        for d in fam:
            assert d in cards, d
    for sec in pack["structure"]:
        for d in sec["devices"]:
            assert d in cards, d
    import agent_tools
    for v in pack["verbs"]:
        assert v in agent_tools.TOOLS, v
    for s in pack["skills"]:
        assert s in agent_prompt.skill_names(), s
    assert pack["budgets"]["per_line_no_repeat"] is True
    assert pack["budgets"]["music"].startswith("never")
    assert "unless the user asks" in pack["colour_world"]["never"]
    assert sum(s["share"][0] for s in pack["structure"]) <= 1.0 <= sum(
        s["share"][1] for s in pack["structure"])
    assert tools_ext.EXT_DEPARTMENTS


# ── the per-line no-repeat budget ──────────────────────────────────────

def test_line_starts_follow_sentence_ends():
    plan = {"words": [{"w": "One", "t0": 0.0}, {"w": "two.", "t0": 0.5},
                      {"w": "Three", "t0": 1.0}, {"w": "four!", "t0": 1.5},
                      {"w": "Five", "t0": 2.2}]}
    starts = world_planner._line_starts(plan)
    assert starts == [0.0, 1.0, 2.2]
    assert [world_planner._line_of(starts, t) for t in (0.2, 1.2, 3.0)] == \
        [0, 1, 2]


def test_world_candidates_never_repeat_a_device_on_adjacent_lines(
        monkeypatch):
    words = [{"w": w, "t0": 0.5 * i, "t1": 0.5 * i + 0.4}
             for i, w in enumerate("Alpha beta. Gamma delta. Epsilon zeta. "
                                   "Eta theta.".split())]
    defs = [{"id": f"pl{k}", "type": "plane", "stage": "world",
             "start": a, "end": a + 1.0, "src_t": a + 0.2,
             "quad": [[0.2, 0.6], [0.8, 0.6], [0.9, 0.9], [0.1, 0.9]],
             "surface": "wall"} for k, a in enumerate((0.0, 1.0, 2.0, 3.0))]
    edl = {"keep": [[0.0, 4.0]], "fx": defs}
    moments = [{"t": w["t0"] + 0.01, "kind": "stress", "strength": 0.8,
                "word": w["w"].strip(".").lower(), "tags": ["payload"],
                "bind": {"on": "stress"}} for w in words]
    plan = {"moments": moments, "words": words, "sections": [(0.0, 4.0)]}
    src = w3s.source(dur=4.0)
    monkeypatch.setattr(world_planner, "_src", lambda index: src)
    monkeypatch.setattr(world_planner, "_hand_up", lambda *a, **k: False)
    monkeypatch.setattr(world_planner, "_face_ok", lambda *a, **k: False)
    monkeypatch.setattr(world_planner, "_matte_ready", lambda *a: False)
    out = world_planner.candidates(edl, w3s.index_for(src), plan, max_n=4)
    lines = [c["detail"]["line"] for c in out if c["device"] == "plane_type"]
    assert lines, out
    assert all(abs(a - b) >= 2 for a, b in zip(lines, lines[1:]))
    for c in out:
        assert not c["owner_flags"]["adds_sound"]
        assert not c["owner_flags"]["adds_zoom"]
        # every candidate is a batch the write path accepts
        edit_batch.apply_batch(edl, c["ops"], 4.0, set())
