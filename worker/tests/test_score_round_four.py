"""First films, round four (week-1112 lane K, 13 Nov): a look anyone can see, a picture inside three seconds,
a wordless upload's own details as pictures, and never a film below today's first cut.

What these hold: the look is solved on the footage's own frames, on the renderer's tables, against the style
card's measured colour, and skin moves least; it lowers to what every source takes; the keyed tone stays off a
film that cuts its own take in; a detail of the upload compiles as a picture of its own; a talking film whose
first picture comes late is told so; a film that is the upload again is not written, and one the last look
finds worse hands the job to the chain. Fixtures are synthetic. No customer media is here."""
import json
import os
from types import SimpleNamespace as NS

import numpy as np
import pytest

import score.compiler as S
from score import brief, film as F, grade, lower, supply, taste
from tests.test_score_compiler import wordless_index
from tests.test_score_taste import beats_over, comp, film, selfie


def frames(seed=3, skin=True):
    """Twelve small frames: a dark wall, a mid grey-blue floor, a bright window, and a patch of skin."""
    rng = np.random.default_rng(seed)
    a = np.zeros((12, 64, 36, 3), np.uint8)
    a[:, :20] = (28, 30, 34)
    a[:, 20:44] = (96, 110, 128)
    a[:, 44:] = (226, 224, 216)
    if skin:
        a[:, 24:40, 8:28] = (196, 150, 124)
    return np.clip(a.astype(np.int16) + rng.integers(-6, 7, a.shape), 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ the look

def test_the_twin_lays_the_shadow_hue_where_ffmpeg_does_and_nowhere_above_it():
    """colorbalance weighs shadows by max + min, measured on a grey ramp with the box's ffmpeg: 0.7 of the offset
    under a code value of 0.1, nothing of shadows or mids from 0.4 up. Lit skin sits above that."""
    px = np.array([[0.1, 0.1, 0.1], [0.25, 0.25, 0.25], [0.5, 0.5, 0.5], [0.8, 0.8, 0.8]])
    rs, gs, bs = grade.balance_rgb(1.0, 0.0)
    out, _ = grade.apply(px, {"balance": {"temperature": 1.0, "tint": 0.0}})
    assert abs((out[0, 0] - px[0, 0]) - 0.7 * rs) < 0.01 and abs((out[0, 2] - px[0, 2]) - 0.7 * bs) < 0.01
    assert abs((out[1, 0] - px[1, 0]) - 0.6 * 0.7 * rs) < 0.012          # the mids' peak, at six tenths
    assert np.abs(out[2:] - px[2:]).max() < 0.004                          # nothing from the middle up


def test_the_keyed_tone_is_gradewins_own_formula_and_leaves_skin():
    import gradewin
    key = {"temperature": 0.2, "split_tone": {"shadows": "#808080", "highlights": "#f4e4c4", "balance": 0.0}, "protect": ["skin"]}
    wall, skin = np.array([[0.86, 0.86, 0.86]]), np.array([[0.77, 0.59, 0.49]])
    out, _ = grade.apply(wall, {"key": key})
    import lutlib
    y, cb, cr = lutlib.rgb_to_ycc8(wall)[0]
    want = lutlib.ycc8_to_rgb(np.array([[y, gradewin.chroma_value(key, "u", y, cb), gradewin.chroma_value(key, "v", y, cr)]]))
    assert np.abs(out - want).max() < 0.004 and out[0, 0] > out[0, 2] + 0.01                  # the wall went warm, by that formula
    kept, w = grade.apply(skin, {"key": key})
    assert w[0] > 0.9 and np.abs(kept - skin).max() < 0.012                                    # skin kept the colour it came with


def test_a_look_is_solved_on_the_frames_stands_off_the_upload_and_moves_skin_least():
    arr = frames()
    look = grade.solve(arr, "cream_teal", "S9")
    assert look == grade.solve(arr, "cream_teal", "S9")                    # the same frames give the same look
    assert look["name"] == "cream_teal" and look["card"] == "S9" and look["aim"]["L50"] == grade.CARDS["S9"]["L50"]
    assert look["de"] >= 3.0 and look["skin_de"] <= grade.SKIN_DE_MAX and look["skin_de"] < look["de"]
    t, m = look["balance"]["temperature"], look["balance"]["tint"]
    rs, gs, bs = grade.balance_rgb(t, m)
    assert -1 <= t <= 1 and -1 <= m <= 1 and rs < 0 < bs and gs > 0         # teal: red out of the shadows, green and blue in
    assert look["key_de"] > look["de"] and look["key_skin_de"] <= grade.SKIN_DE_MAX
    warm = grade.solve(arr, "warm_low", "S9")
    assert grade.balance_rgb(**{"t": warm["balance"]["temperature"], "m": warm["balance"]["tint"]})[0] > 0   # amber: red in
    assert grade.solve(None, "cream_teal") is None


def test_the_tone_goes_toward_the_cards_numbers_never_past_what_skin_takes():
    arr = np.clip(frames().astype(np.int16) + 40, 0, 255).astype(np.uint8)          # a bright, flat upload
    look = grade.solve(arr, "bleach", "S11")
    assert look["now"]["L50"] <= look["was"]["L50"] and look["now"]["C"] < look["was"]["C"]     # darker and drained, toward S11
    assert abs(look["base"].get("ev", 0.0)) <= grade.EV_MAX and grade.SAT[0] <= look["base"].get("saturation", 1.0) <= grade.SAT[1]
    assert grade.solve(arr, "clean", "S11")["amount"] <= 0.4                 # a clean look never floods its shadows


def test_older_numbers_and_unknown_names_are_read_as_a_design():
    assert grade.recipe({"contrast": 1.1, "sat": 0.92, "temp": 0.03})[0] == "warm_low"
    assert grade.recipe({"contrast": 1.1, "temp": -0.03})[0] == "cold_doc"
    assert grade.recipe({"sat": 0.5})[0] == "bleach"
    assert grade.recipe("moody and cinematic")[0] == grade.DEFAULT
    name, rec = grade.recipe({"name": "night", "shadows": "#102060", "amount": 1.5})
    assert name == "night" and rec["sh"] == "#102060" and rec["de"] > grade.LOOKS["night"]["de"]


def test_every_design_is_in_the_prompt_and_every_offered_card_is_measured():
    text = brief.prefix_text()
    for name in grade.LOOKS:
        assert f"\n{name}: " in text, name
    for card in brief.TALK_CARDS + brief.WORDLESS_CARDS:
        assert {"L5", "L50", "L95", "C"} <= set(grade.CARDS[card]), card


def _work(tmp_path, idx, arr=None, own=None, items=None):
    json.dump(idx, open(tmp_path / "index.json", "w"))
    np.save(tmp_path / "colour.npy", frames() if arr is None else arr)
    if own is not None or items is not None:
        json.dump({"own": own or [], "items": items or []}, open(tmp_path / "bin.json", "w"))
    return str(tmp_path)


def test_a_film_gets_its_look_as_one_plain_window_and_the_shadow_hue_and_the_keyed_tone_only_on_a_take_never_cut_in(tmp_path):
    import fx_schemas.finish as FX
    idx = selfie(20.0)
    work = _work(tmp_path, idx)
    c = S.compile_score(film(beats_over(idx, 3, lambda k, a, z: {"pic": "main"}), dna={"look": "cream_teal"}), index=idx, work_dir=work, bin_items={}, perception={})
    assert c.l1["film"]["look"]["name"] == "cream_teal" and "LOOK cream_teal" in c.meter_text
    assert c.meter["facts"]["grade_applied"]
    edl, _, rep = lower.lower(c.l1, work)
    wins = [f for f in edl["fx"] if f["type"] == "grade_window"]
    assert [w["id"] for w in wins] == ["look.base", "look.key"] and rep["look"]["keyed"]
    for w in wins:
        FX.GradeWindowFx(**w)                                                # today's schema takes both
        assert w["start"] == 0.0 and abs(w["end"] - c.l1["dur_f"] / 30) < 0.05
    assert "protect" not in wins[0] and wins[1]["protect"] == ["skin"]
    gc = edl["effects"]["grade_custom"]
    assert set(gc) <= {"temperature", "tint"} and gc["temperature"] < 0       # the hue, and nothing graded twice
    # the same film with a moment of its own take cut in: a cutaway takes plain windows only, so no keyed tone
    c2 = S.compile_score(film(beats_over(idx, 3, lambda k, a, z: {"cut": [30, 999], "pic": ["main@2.0-3.0", "main"]} if k == 1 else {"pic": "main"}),
                              dna={"look": "cream_teal"}), index=idx, work_dir=work, bin_items={}, perception={})
    edl2, _, rep2 = lower.lower(c2.l1, work)
    assert any(o.get("asset_key") == "main" for o in edl2["overlays"])
    assert [f["id"] for f in edl2["fx"] if f["type"] == "grade_window"] == ["look.base"] and not rep2["look"]["keyed"]


def test_a_compile_with_no_frames_keeps_the_plain_numbers():
    idx = selfie(20.0)
    c = comp(film(beats_over(idx, 3, lambda k, a, z: {"pic": "main"}), dna={"look": {"contrast": 1.1, "sat": 0.9}}), idx)
    assert "look" not in c.l1["film"] and c.l1["film"]["grade"]["contrast"] == 1.1


# ------------------------------------------------------------------ the upload's own details

VIEW = {"id": 0, "src": "main@30.00-32.40", "crop": [0.2, 0.3, 0.76, 0.8], "box": [0.3, 0.4, 0.6, 0.65], "at": 30.8, "wish": None, "what": "the label, close"}


def test_a_view_is_a_picture_of_its_own_at_its_moment_closer_than_the_take(tmp_path):
    idx = wordless_index(51.0)
    work = _work(tmp_path, idx, own=[VIEW])
    beats = [{"k": "b", "id": "b01", "at": "@0.0..@10.0", "job": "x", "pic": "main"},
             {"k": "b", "id": "b02", "at": "@10.0..@20.0", "job": "x", "cut": [45, 999], "pic": ["view:0", "main"]},
             {"k": "b", "id": "b03", "at": "@20.0..@30.0", "job": "x", "pic": "main"}]
    c = S.compile_score(film(beats), index=idx, work_dir=work, bin_items={}, perception={})
    s = next(i for i in c.l1["items"] if i["k"] == "shot" and i.get("view") == "0")
    r = s["crop"][0] if isinstance(s["crop"][0], list) else s["crop"]
    assert 30.0 <= s["src"][0] < 32.4
    assert 1 / S.VIEW_ZOOM[1] - 0.02 <= r[3] - r[1] <= 1 / S.VIEW_ZOOM[0] + 0.02                 # 1.4 to 2 times closer
    assert r[0] <= 0.3 and r[2] >= 0.6 and r[1] <= 0.4 and r[3] >= 0.65                          # and the detail is whole in it
    assert c.meter["taste"]["supply"]["np"] >= 2 and c.meter["new_pictures10"] > 0                # in and back: two new pictures
    assert not [x for x in c.meter["taste"].get("rows") or [] if x["dept"] == "camera"]           # a picture, not a camera move to earn
    edl, _, _ = lower.lower(c.l1, work)
    o = next(o for o in edl["overlays"] if o.get("asset_key") == "main" and o.get("source_crop"))
    assert abs(o["source_start_s"] - s["src"][0]) < 0.05


def test_a_view_the_board_does_not_hold_is_the_take_and_says_so(tmp_path):
    idx = wordless_index(51.0)
    work = _work(tmp_path, idx, own=[VIEW])
    c = S.compile_score(film([{"k": "b", "id": "b01", "at": "@0.0..@10.0", "job": "x", "pic": "view:7"}]), index=idx, work_dir=work, bin_items={}, perception={})
    assert any("view:7 is not on the board" in f for f in c.meter["findings"])


class _R:
    def __init__(self, text):
        self.text, self.usd, self.seconds = text, 0.001, 1.0


def test_wordless_footage_is_given_its_own_details_and_the_brief_lists_them(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(supply, "own_sheet", lambda *a, **k: Image.new("RGB", (64, 64)))
    monkeypatch.setattr(supply.call, "stream", lambda *a, **k: _R('{"own":[{"c":3,"box":[0.3,0.4,0.6,0.65],"what":"the label, close"},'
                                                                  '{"c":3,"box":[0.31,0.41,0.6,0.66],"what":"the label again"},'
                                                                  '{"c":9,"box":[0.0,0.0,1.0,1.0],"what":"the whole frame"}]}'))
    open(tmp_path / "source.mp4", "wb").close()
    rows, usd = supply.own_views(str(tmp_path), {"dur": 20.0, "W": 720, "H": 1280}, 1e12)
    assert [r["what"] for r in rows] == ["the label, close"] and usd > 0          # the twin and the whole frame are not details
    assert rows[0]["src"].startswith("main@") and rows[0]["crop"][3] - rows[0]["crop"][1] <= 0.72
    json.dump({"own": rows, "items": [], "own_lines": [f"view:{rows[0]['id']} the upload at {rows[0]['at']:.1f}s, re-framed on: {rows[0]['what']}"]},
              open(tmp_path / "bin.json", "w"))
    text = "\n".join(brief.bin_part(str(tmp_path))[0])
    assert "view:0" in text and '"pic":"view:0"' in text
    assert S.load_views(str(tmp_path))["0"]["box"] == rows[0]["box"]


# ------------------------------------------------------------------ a picture inside three seconds

def test_a_talking_film_is_asked_for_the_picture_it_opens_on_unless_the_scout_already_wished_one(tmp_path, monkeypatch):
    idx = selfie(20.0)
    json.dump(idx, open(tmp_path / "index.json", "w"))
    said = []
    monkeypatch.setattr(supply.call, "stream", lambda *a, **k: (said.append(a[2][0]["text"]), _R(
        '{"hook":{"w":"0-1","want":"Oscar Piastri, the McLaren driver","q":["formula one driver portrait","race car"],"kind":"photo","mine":false}}'))[1])
    wish, usd = supply.hook_wish(str(tmp_path), {"about": "a fan on Piastri"}, [], {"dur": 20.0}, 1e12)
    assert wish["hook"] and wish["want"].startswith("Oscar Piastri") and wish["w"] == "0-1" and not wish["mine"] and usd > 0
    assert "a fan on Piastri" in said[0] and "0:" in said[0]
    early = [{"w": "1-2", "want": "a bridge", "mine": False}]
    assert supply.hook_wish(str(tmp_path), {}, early, {"dur": 20.0}, 1e12) == (None, 0.0)       # one is already there: no call
    monkeypatch.setattr(supply.call, "stream", lambda *a, **k: _R('{"hook":null}'))
    assert supply.hook_wish(str(tmp_path), {}, [], {"dur": 20.0}, 1e12)[0] is None


def test_a_talking_film_whose_first_picture_comes_late_is_told_and_one_that_opens_on_it_is_not():
    idx = selfie(24.0)
    late = film(beats_over(idx, 4, lambda k, a, z: {"cut": [40, 45, 999], "pic": ["main", "bin:1", "main"]} if k == 2 else {"pic": "main"}))
    c = comp(late, idx)
    assert any(f.startswith("hook: the first picture that is not the take lands at") for f in c.meter["findings"])
    early = film(beats_over(idx, 4, lambda k, a, z: {"cut": [40, 999], "pic": ["bin:1", "main"]} if k == 0 else {"pic": "main"}))
    assert not any(f.startswith("hook:") for f in comp(early, idx).meter["findings"])
    assert not any(f.startswith("hook:") for f in comp(late, idx, bin_items={}).meter["findings"])       # nothing to show: nothing asked
    bin_hook = {"items": [{"id": 0, "kind": "image", "w": 1600, "h": 1067, "what": "Oscar Piastri", "for": "w0-1", "hook": True}]}
    assert "THE HOOK PICTURE" in "\n".join(brief.bin_part.__wrapped__(bin_hook) if hasattr(brief.bin_part, "__wrapped__") else _bin_text(bin_hook))


def _bin_text(doc, tmp=[None]):
    import tempfile
    d = tempfile.mkdtemp()
    json.dump(doc, open(os.path.join(d, "bin.json"), "w"))
    return brief.bin_part(d)[0]


# ------------------------------------------------------------------ never below today's first cut

def test_a_film_that_is_the_upload_again_but_for_its_colour_is_bare_and_one_with_a_picture_or_type_is_not(tmp_path):
    idx = wordless_index(8.0)
    idx["shots"] = [{"id": 1, "start": 0.0, "end": 8.0}]
    work = _work(tmp_path, idx)
    plain = S.compile_score(film([{"k": "b", "id": "b01", "at": "@0.0..@7.9", "pic": "main"}], dna={"look": "golden"}), index=idx, work_dir=work, bin_items={}, perception={})
    assert plain.meter["facts"]["bare"] and plain.meter["facts"]["grade_applied"] and F.bare(plain)
    idx2 = selfie(20.0)
    cut = comp(film(beats_over(idx2, 4, lambda k, a, z: {"cut": [40, 45, 999], "pic": ["main", f"bin:{k}", "main"]} if k == 1 else {"pic": "main"})), idx2)
    assert not F.bare(cut)


class _Db:
    def __init__(self):
        self.events = []

    def run(self, fn, *a, **k):
        if getattr(fn, "__name__", "") == "record_client_event":
            self.events.append((a[2], a[3]))
            return None
        if getattr(fn, "__name__", "") == "get_asset":
            return {"storage_key": "previews/1.mp4"}
        return None


def _looked(monkeypatch, tmp_path, verdict, text):
    """film.last_look on a film whose preview is on screen, with the look's answer given."""
    import storage
    from PIL import Image
    os.makedirs(tmp_path, exist_ok=True)
    idx = selfie(20.0)
    work = _work(tmp_path, idx, items=[{"id": i, "kind": "image", "path": f"bin/{i}.jpg", "w": 1600, "h": 1067, "what": "a bridge"} for i in range(4)])
    os.makedirs(tmp_path / "bin")
    for i in range(4):
        Image.new("RGB", (160, 107), (90, 60 + 20 * i, 40)).save(tmp_path / "bin" / f"{i}.jpg")
    c = S.compile_score(text, work_dir=work)
    wrote = []
    ctx = NS(director_state={"work": work, "text": text, "comp": c, "edl": {"film": 1}, "bin_doc": None, "original_key": "orig/a.mp4", "cfg": {"models": {"direct": "m"}},
                             "start_edl": {"keep": [[0.0, 20.0]]}},
             last_preview={"render_asset_id": 5}, director_version=2, versions_written=[2], first_cut_phases={},
             apply_score=lambda edl, desc: (wrote.append((edl, desc)), "EDL v2 -> v3: " + desc)[1])
    monkeypatch.setattr(storage, "presign_get", lambda key, expires=900: "https://signed")
    monkeypatch.setattr(taste, "look", lambda *a, **k: (verdict, 0.02))
    monkeypatch.setattr(F, "_publish", lambda ctx, db, edl, loc, bin_doc, key: edl)
    db = _Db()
    out = F.last_look(ctx, db, {"id": 9, "user_id": 1, "project_id": 2}, __import__("time").monotonic(), {}, None)
    return ctx, db, wrote, out


TITLED = lambda idx: film(beats_over(idx, 3, lambda k, a, z: {"pic": "main", "do": [{"g": "big", "t": "THE CLAIM", "for": "turn", "at": "b01.in", "d": 40}]} if k == 0 else {"pic": "main"}))


def test_a_film_most_of_whose_frames_lose_to_the_upload_is_taken_back_for_the_chain(monkeypatch, tmp_path):
    ctx, db, wrote, out = _looked(monkeypatch, tmp_path, {"pairs": 6, "upload": 5, "remove": ["b01.big"], "why": ["type on the face"]}, TITLED(selfie(20.0)))
    assert out is None and wrote == [({"keep": [[0.0, 20.0]]}, "the First cut starts again from the upload")]
    assert ctx.director_version is None and ctx.director_fell_why == "the last look: 5 of 6 frames for the upload, nothing left but the upload"
    assert ctx.first_cut_phases["director"]["look"]["decision"] == "chain"
    assert [k for k, _ in db.events] == ["director_taste"] and db.events[0][1]["decision"] == "chain"
    # first_cut then finds the film is not on screen and calls fell, which files it as the look's doing
    F.fell(ctx, db, {"id": 9, "user_id": 1, "project_id": 2}, "render", "no preview", start_edl={"keep": [[0.0, 20.0]]})
    assert db.events[-1][0] == "director_fell_back" and db.events[-1][1]["stage"] == "look" and len(wrote) == 1


def test_a_film_the_look_only_trims_is_written_again_as_before(monkeypatch, tmp_path):
    idx = selfie(20.0)
    text = film(beats_over(idx, 4, lambda k, a, z: {"cut": [40, 45, 999], "pic": ["main", f"bin:{k}", "main"]} if k in (1, 2) else {"pic": "main"}))
    ctx, db, wrote, out = _looked(monkeypatch, tmp_path, {"pairs": 6, "upload": 1, "remove": ["b02.s1"], "why": ["only loosely like the words"]}, text)
    assert out is not None, ctx.first_cut_phases
    assert len(wrote) == 1 and wrote[0][1] == "the Director's First cut, after the last look"
    assert ctx.first_cut_phases["director"]["look"]["decision"] == "cut" and ctx.director_version == 2


def test_the_critic_reads_which_of_two_films_is_better_and_says_nothing_when_it_cannot(monkeypatch, tmp_path):
    from PIL import Image
    from score import boards, call
    monkeypatch.setattr(boards, "grab", lambda p, t, width=None, height=None: Image.new("RGB", (90, height or 160)))
    monkeypatch.setattr(taste.subprocess if hasattr(taste, "subprocess") else __import__("subprocess"), "run",
                        lambda *a, **k: NS(stdout="12.5\n", returncode=0))
    monkeypatch.setattr(call, "stream", lambda *a, **k: _R('{"better":"A","why":"B is the raw footage with a grade"}'))
    v, usd = taste.against("a.mp4", "b.mp4", str(tmp_path / "board.jpg"), 1e12, "m")
    assert v == {"better": "current", "why": "B is the raw footage with a grade"} and usd > 0 and os.path.exists(tmp_path / "board.jpg")
    monkeypatch.setattr(call, "stream", lambda *a, **k: _R("no answer"))
    assert taste.against("a.mp4", "b.mp4", str(tmp_path / "board.jpg"), 1e12, "m")[0] is None


# ------------------------------------------------------------------ sound: lane J's tail reaches the renderer

def test_a_sound_with_a_measured_tail_lowers_with_its_length(tmp_path):
    idx = selfie(20.0)
    c = comp(film(beats_over(idx, 3, lambda k, a, z: {"pic": "main"})), idx)
    l1 = dict(c.l1, sound=[{"id": "x.impact", "s": "impact_1", "start_s": 1.0, "event_f": 40, "peak_s": 0.33, "db": -12.0, "dur_s": 1.4},
                           {"id": "x.pop", "s": "pop_1", "start_s": 4.0, "event_f": 121, "peak_s": 0.03, "db": -14.0}])
    edl, _, _ = lower.lower(l1, str(tmp_path))
    by = {s["id"]: s for s in edl["sfx"]}
    assert by["x.impact"]["dur_s"] == 1.4 and "dur_s" not in by["x.pop"]


def test_at_the_floor_what_the_look_named_goes_and_a_film_that_still_stands_is_delivered(monkeypatch, tmp_path):
    idx = selfie(20.0)
    text = film(beats_over(idx, 4, lambda k, a, z: {"cut": [40, 45, 999], "pic": ["main", f"bin:{k}", "main"]} if k in (1, 2) else {"pic": "main"}))
    ctx, db, wrote, out = _looked(monkeypatch, tmp_path, {"pairs": 6, "upload": 4, "remove": ["b02.s1"], "why": ["loosely like the words"] * 4}, text)
    assert out is not None and wrote[0][1] == "the Director's First cut, after the last look"          # the other picture still stands
    ctx, db, wrote, out = _looked(monkeypatch, tmp_path / "b", {"pairs": 6, "upload": 4, "remove": [], "why": ["the frame is cropped"] * 4}, text)
    assert out is None and ctx.director_fell_why.endswith("nothing it could name") and wrote[0][1] == "the First cut starts again from the upload"
