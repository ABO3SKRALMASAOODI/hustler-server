"""Track 4E: aspects re-solved, languages from the user's translation,
format packs and trend grammars, the delivery department verbs."""

import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import aspects                                                # noqa: E402
import format_packs                                           # noqa: E402
import retypeset_lang                                         # noqa: E402
import ui_scene                                               # noqa: E402

WORDS = [("we", 0.5), ("built", 0.8), ("the", 1.1), ("rocket.", 1.3),
         ("then", 2.4), ("the", 2.7), ("factory", 2.9), ("grew", 3.4),
         ("fast.", 3.7)]
IDX = {"words": [{"w": w, "t0": t, "t1": t + 0.25} for w, t in WORDS],
       "video": {"width": 1920, "height": 1080, "fps": 25, "duration": 12}}


def _edl():
    from schemas import validate_edl
    g = ui_scene.solve([{"at": 0, "layout": "stack", "objects": [
        {"id": "a", "kind": "card", "title": "Rocket"},
        {"id": "b", "kind": "card", "title": "Factory"}]}], 4.0, 1080, 1920)
    e = {"keep": [[0.0, 12.0]], "frame": {"ratio": "9:16", "mode": "crop",
                                         "focus_x": 0.4},
         "captions": {"mode": "from_transcript",
                      "placement_track": None},
         "motion": [{"id": "ui1", "template": "ui_scene", "start": 1.0,
                     "end": 5.0, "params": {
                         "graph": g, "shown": ["Rocket", "Factory"],
                         "spec": {"scenes": [{"at": 0, "layout": "stack",
                                              "objects": [
                                                  {"id": "a", "kind": "card",
                                                   "title": "Rocket"},
                                                  {"id": "b", "kind": "card",
                                                   "title": "Factory"}]}],
                                  "words": []}}},
                    {"id": "h1", "template": "hook_title", "start": 0.2,
                     "end": 1.8, "params": {"text": "THE ROCKET"},
                     "footprint": {"box": [0.1, 0.1, 0.9, 0.3], "ar": 0.56,
                                   "faces": []}}],
         "overlays": [{"id": "o1", "asset_key": "media/p/x.png",
                       "kind": "image", "start": 2.0, "duration_s": 2.0,
                       "x": 0.5, "y": 0.4, "scale": 0.8}]}
    return validate_edl(e, 12.0).model_dump()


def test_4_3_is_an_aspect_master():
    from schemas import validate_edl
    e = validate_edl({"keep": [[0, 5]], "frame": {"ratio": "4:3"}}, 5.0)
    assert e.frame.ratio == "4:3"


def test_solve_re_solves_ui_scenes_and_drops_measurements():
    e0 = _edl()
    e1, notes = aspects.solve(e0, "16:9", IDX)
    assert e1["frame"]["ratio"] == "16:9" and e1["frame"]["focus_x"] == 0.4
    ui = next(m for m in e1["motion"] if m["template"] == "ui_scene")
    g0 = next(m for m in e0["motion"] if m["id"] == "ui1")["params"]["graph"]
    g1 = ui["params"]["graph"]
    assert (g1["W"], g1["H"]) == aspects.design_size("16:9")
    assert g1["objects"][0]["segs"] != g0["objects"][0]["segs"]  # re-laid out
    hook = next(m for m in e1["motion"] if m["id"] == "h1")
    assert hook.get("footprint") is None
    assert any("UI scene" in n for n in notes)


def test_overlays_keep_their_own_proportions():
    e0 = _edl()
    o0 = e0["overlays"][0]
    e1, _n = aspects.solve(e0, "16:9", IDX)
    o1 = e1["overlays"][0]
    # the same share of the short side: 0.8 x 1080 px wide in 9:16 is
    # 864 px; in 16:9 (1920 wide) that is a scale of 0.45
    assert abs(o0["scale"] * 1080 - o1["scale"] * 1920) < 2
    assert (o1["x"], o1["y"]) == (o0["x"], o0["y"])


def test_unknown_ratio_is_refused():
    with pytest.raises(ValueError):
        aspects.solve(_edl(), "21:9", IDX)


def test_retypeset_from_the_users_translation():
    e0 = _edl()
    ph = retypeset_lang.phrases(e0, IDX)
    assert [p["text"] for p in ph] == ["we built the rocket.",
                                       "then the factory grew fast."]
    e1, notes = retypeset_lang.retypeset(
        e0, IDX, "de", translation=["wir bauten die Rakete.",
                                    "dann wuchs die Fabrik schnell."],
        mapping={"THE ROCKET": "DIE RAKETE"})
    corr = e1["captions"]["corrections"]
    assert len(corr) == 2 and corr[0]["to"] == "wir bauten die Rakete."
    assert corr[0]["start"] <= ph[0]["t0"] and corr[0]["end"] >= ph[0]["t1"]
    hook = next(m for m in e1["motion"] if m["id"] == "h1")
    assert hook["params"]["text"] == "DIE RAKETE"
    # choreography: no window moved
    for a, b in zip(e0["motion"], e1["motion"]):
        assert (a["start"], a["end"]) == (b["start"], b["end"])
    from schemas import validate_edl
    validate_edl(copy.deepcopy(e1), 12.0)


def test_retypeset_never_machine_translates():
    with pytest.raises(ValueError, match="never machine"):
        retypeset_lang.retypeset(_edl(), IDX, "de")
    with pytest.raises(ValueError, match="2 spoken phrases"):
        retypeset_lang.retypeset(_edl(), IDX, "de", translation=["eins"])


def test_srt_cues_replace_the_words_inside_them():
    srt = ("1\n00:00:00,400 --> 00:00:01,700\nwir bauten die Rakete\n\n"
           "2\n00:00:02,300 --> 00:00:04,100\ndann wuchs die Fabrik\n")
    e1, _n = retypeset_lang.retypeset(_edl(), IDX, "de", srt=srt)
    assert [c["to"] for c in e1["captions"]["corrections"]] == [
        "wir bauten die Rakete", "dann wuchs die Fabrik"]


def test_format_packs_and_budgets():
    import taste
    for code in format_packs.CODES:
        p = format_packs.load(code)
        assert p["id"] == code and p["structure"]
        blob = json.dumps(p.get("devices_prefer")).lower()
        assert "zoom" not in blob and "sfx" not in blob and "music" not in \
            blob
        b = format_packs.budgets(code)
        assert b["zoom"] is None and b["sfx"] is None
        led = taste.ledger(_edl(), "designed", fmt=code)
        assert "zoom" in led.line()
    assert format_packs.budgets("Z") is None
    with pytest.raises(ValueError):
        taste.ledger(_edl(), "designed", fmt="Z")


def test_trend_grammar_recognised_on_the_users_track():
    period = 0.6
    beats = [0.5 + i * period for i in range(40)]
    energy = [-4.0] * 6 + [-18.0] * 11 + [-2.0] * 30   # rise at 8.5 s
    rmap = {"bpm": 100.0, "beats": beats, "drops": []}
    got = format_packs.recognise(rmap, energy=energy, bin_s=0.5)
    assert got and got["id"] == "desk-drop"
    assert abs(got["drop_s"] - 8.3) <= period + 1e-6
    names = [s["name"] for s in got["sections"]]
    assert names == ["hook", "build", "drop", "montage"]
    # no structure, no grammar (abstain)
    flat = format_packs.recognise(rmap, energy=[-6.0] * 50, bin_s=0.5)
    assert flat is None


# ── the verbs ────────────────────────────────────────────────────────────

class FakeDB:
    def __init__(self):
        self.keys = {"media/p/x.png": {"storage_key": "media/p/x.png",
                                       "duration_s": None}}

    def run(self, fn, *a, **kw):
        import edit_batch
        if fn is edit_batch.project_media_keys:
            return dict(self.keys)
        raise AssertionError(f"unexpected db call {fn}")


class Ctx:
    def __init__(self, edl):
        self.db = FakeDB()
        self.project_id = 7
        self.duration = 12.0
        self._edl = edl
        self.version = 1
        self.writes = []
        self.index = IDX

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self._edl)}

    def write_edl(self, edl, desc):
        self.writes.append((edl, desc))
        self._edl = edl
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


@pytest.fixture
def ctx(monkeypatch):
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: True)
    return Ctx(_edl())


def test_deliver_aspects_lists_then_applies(ctx):
    from tools_ext import delivery_tools as format_tools
    out = format_tools.deliver_aspects(ctx)
    assert out.startswith("ASPECTS") and not ctx.writes
    assert "4:3" in out and "16:9" in out
    out = format_tools.deliver_aspects(ctx, apply="4:3")
    assert out.startswith("EDL v1 -> v2"), out
    assert ctx._edl["frame"]["ratio"] == "4:3"


def test_apply_format_sets_the_aspect_and_briefs(ctx):
    # Wave 4's apply_format is the one pack verb now (engines release):
    # apply_format_pack, department direction, every genre A-J
    from tools_ext import format_tools
    ctx._edl["frame"]["ratio"] = "1:1"
    n = len(ctx.writes)
    out = format_tools.apply_format_pack(ctx, "I")
    assert "FORMAT I" in out and "LEDGER (I)" in out
    # a chosen frame is kept unless the agent asks (review: a 16:9 edit was
    # silently re-framed vertical by a brief)
    assert ctx._edl["frame"]["ratio"] == "1:1" and "kept this edit" in out
    # a brief pack without picks writes nothing but its frame
    assert len(ctx.writes) == n
    out = format_tools.apply_format_pack(ctx, "I", aspect=True)
    assert ctx._edl["frame"]["ratio"] == "9:16"
    assert format_tools.apply_format_pack(ctx, "Q").startswith("REJECTED")


def test_retypeset_language_verb(ctx):
    from tools_ext import delivery_tools as format_tools
    out = format_tools.retypeset_language(ctx, "de", list_phrases=True)
    assert "we built the rocket." in out and not ctx.writes
    out = format_tools.retypeset_language(
        ctx, "de", translation=json.dumps(["wir bauten die Rakete.",
                                           "dann wuchs die Fabrik."]))
    assert out.startswith("EDL v1 -> v2"), out
    assert ctx._edl["captions"]["corrections"][1]["to"] == \
        "dann wuchs die Fabrik."
    assert format_tools.retypeset_language(ctx, "german").startswith(
        "REJECTED")


def test_registration():
    import agent_tools
    import tools_ext
    from tools_ext import delivery_tools as format_tools
    for v in format_tools.VERBS:
        assert v in agent_tools.TOOLS
        assert v in agent_tools.TOOL_DOMAINS["delivery"]
    assert "delivery" in tools_ext.DEPARTMENTS
    core = tools_ext.core_names(agent_tools.TOOLS)
    cat = agent_tools.openai_tools("any-model", compact=True, names=core)
    assert len(json.dumps(cat, separators=(",", ":"))) <= 82_700
    names = list(tools_ext.EXT_DEPARTMENTS["delivery"])
    comp = agent_tools.openai_tools("x", compact=True, names=names)
    assert len(json.dumps(comp, separators=(",", ":"))) <= 3000


def test_retypeset_swaps_ui_scene_texts():
    e0 = _edl()
    e1, _n = retypeset_lang.retypeset(e0, IDX, "de",
                                      mapping={"Rocket": "Rakete"})
    ui = next(m for m in e1["motion"] if m["id"] == "ui1")
    titles = [o.get("title") for o in ui["params"]["graph"]["objects"]]
    assert "Rakete" in titles and "Rakete" in ui["params"]["shown"]
    spec_titles = [o.get("title") for o in
                   ui["params"]["spec"]["scenes"][0]["objects"]]
    assert "Rakete" in spec_titles
