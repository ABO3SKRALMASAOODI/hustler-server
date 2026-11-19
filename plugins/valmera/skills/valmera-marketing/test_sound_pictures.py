"""Round1 (20 Nov 2026) marketing causes sound-events-missing and stock-inventory-wrong-picture-kept, offline: no model
call, no stack, no render.

- the export's own pictures and titles (its EDL) ride on every cut of the export as `landings` in that cut's seconds,
  so the sound design hears them land; the call to action is climbed into on a riser;
- a window the picture check judged loose or wrong is filled in place with the film's own picture that shows its words
  (checked on its own frames), never only recorded; with nothing better it keeps what it had; the check knows each
  line's role, so a pain line's real recording reads as the pain.

    $PY -m pytest -q test_sound_pictures.py
"""
import json
import os
from types import SimpleNamespace

from test_devices import EXPORT, LIB, reel
from test_proof import P

D, F = P.D, P.F


def test_the_exports_landings_are_read_from_its_edit_list_and_cut_to_each_window(tmp_path):
    (tmp_path / "edl").mkdir()
    edl = dict(keep=[[0, 20]], overlays=[dict(id="o1", kind="image", start=8.3, duration_s=2.0)],
               fx=[dict(id="b1", type="broll", start=12.5, end=14.0)], motion=[dict(id="t1", start=15.4, end=16.0)])
    (tmp_path / "edl" / "v003.json").write_text(json.dumps(edl))
    (tmp_path / "record.json").write_text(json.dumps(dict(exports=[dict(tag="final", edl_version=3, file="e.mp4")])))
    lands = P.export_landings(dict(record=str(tmp_path / "record.json")))
    assert lands == [dict(t=8.3, kind="picture"), dict(t=12.5, kind="picture"), dict(t=15.4, kind="title")]
    assert P.landings_in(lands, 12.0, 16.0) == [dict(t=0.5, kind="picture"), dict(t=3.4, kind="title")]
    assert P.export_landings(dict(record=str(tmp_path / "missing.json"))) == []


def test_a_clip_with_landings_carries_them_onto_its_asset_and_the_reel_reads_them():
    plan = F.Plan({}, {})
    plan.video("v00", dict(path="/x/export_body.mp4", w=1080, h=1920, seconds=20, landings=[dict(t=12.5, kind="picture")]))
    plan.video("v01", dict(path="/x/clip.mp4", w=1920, h=1080, seconds=5))
    assert plan.assets["v00"]["landings"] == [dict(t=12.5, kind="picture")] and "landings" not in plan.assets["v01"]
    l1 = dict(fps=30, dur_f=300, assets=plan.assets,
              items=[dict(id="a", k="shot", pic="v00", f=[0, 60], src=[11.0, 13.0]), dict(id="b", k="shot", pic="v01", f=[60, 90], src=[0, 1])])
    shown = F.landings_shown(l1)
    assert [(e["f"], e["kind"]) for e in shown] == [(45, "card")]                  # 12.5 s, 1.5 s into a window starting at 11 s
    assert F.landings_shown(dict(l1, assets={"v01": plan.assets["v01"]})) == []


def test_the_call_to_action_is_climbed_into_on_a_riser():
    lib = dict(LIB, **{D.CTA_RISER: dict(file="r.flac", peak_s=0.98, max_db=-3.0, id=D.CTA_RISER)})
    for lay in (lambda l1: D.cta(l1, f0=110, f1=l1["dur_f"], export=EXPORT, lib=lib, src0=20.0),
                lambda l1: D.site(l1, f0=110, f1=l1["dur_f"], page=dict(path="/x/site.png", w=1170, h=2532), export=EXPORT, lib=lib, src0=20.0)):
        rec = lay(reel())
        names = [s["s"] for s in rec["sounds"]]
        assert D.CTA_RISER in names and "whoosh_soft_1" in names
        riser = next(s for s in rec["sounds"] if s["s"] == D.CTA_RISER)
        assert riser["f"] == 111                                                    # its top is the call's landing
    assert D.CTA_RISER not in [s["s"] for s in D.cta(reel(), f0=110, f1=213, export=EXPORT, lib=LIB, src0=20.0)["sounds"]]


def _film(tmp_path):
    for n in ("export_body.mp4", "marine.jpg", "upload_2.mp4"):
        (tmp_path / n).write_bytes(b"x")
    body, photo, up = (str(tmp_path / n) for n in ("export_body.mp4", "marine.jpg", "upload_2.mp4"))
    l1 = dict(fps=30, dur_f=300, assets={"v00": dict(kind="video", path=body, w=1080, h=1920, fps=30, dur=20.0),
                                          "p00": dict(kind="image", path=photo, w=1600, h=1067)},
              items=[dict(id="sh1", k="shot", pic="v00", f=[0, 60], src=[0.0, 2.0], crop=[0, 0, 1, 1]),
                     dict(id="sh2", k="shot", pic="p00", f=[60, 75], crop=[[0.1, 0, 0.5, 1]] * 15, look="mono_silver"),
                     dict(id="sh3", k="shot", pic="v00", f=[75, 120], src=[3.0, 4.5], crop=[0, 0, 1, 1]),
                     dict(id="cap1", k="word", f=[62, 70], t="joke", v="cap", role="cap")])
    pictures = [dict(f=[0, 60], path=body, name="THE EXPORT", kind="clip", src=[0.0, 2.0], line=1),
                dict(f=[60, 75], path=photo, name="Editor at a video timeline", kind="photo", src=None, line=2),
                dict(f=[75, 120], path=body, name="THE EXPORT", kind="clip", src=[3.0, 4.5], line=2)]
    pack = dict(footage=[dict(i=3, clips=[dict(path=up, subject="THE UPLOAD", seconds=1.0, w=1920, h=1080)])],
                pictures={"Editor at a video timeline": [dict(path=photo, w=1600, h=1067)]},
                proof_fill=[dict(path=body, subject="THE EXPORT", seconds=6.0, w=1080, h=1920, busy=[[4.0, 6.0]])],
                product=dict(paths=[body, up]))
    sc = dict(segments=[dict(beat="hook", pictures=["THE EXPORT"]), dict(beat="setup", pictures=["THE EXPORT"]),
                        dict(beat="setup", pictures=["Editor at a video timeline"]), dict(beat="low", pictures=["THE UPLOAD"])])
    return l1, pictures, pack, sc, {body, up}


def _wire(monkeypatch, verdicts, ranked):
    """The two looks, answered offline: the repair's ranking, then the check's verdict by file."""
    seen = []
    monkeypatch.setattr(P, "still", lambda *a, **k: "jpeg")
    monkeypatch.setattr(P, "words_under", lambda clock, f0, f1: {60: "Black hole joke, his face"}.get(f0, f"words {f0}"))

    def ask(system, content, **kw):
        texts = " ".join(x.get("text", "") for x in content if x.get("type") == "text")
        seen.append((kw.get("what"), texts))
        if kw.get("what") == "picture repair":
            return {"windows": [{"n": 0, "ranked": ranked}]}, {}
        rows = [x["text"] for x in content if x.get("type") == "text" and x["text"].startswith("Picture ")]
        return {"pictures": [{"n": k + 1, "fit": verdicts(r)} for k, r in enumerate(rows)]}, {}
    monkeypatch.setattr(P, "ask", ask)
    return seen


def test_a_window_judged_wrong_takes_the_films_own_picture_that_fits_in_place(tmp_path, monkeypatch):
    l1, pictures, pack, sc, product = _film(tmp_path)
    clock = SimpleNamespace(words=[])
    seen = _wire(monkeypatch, lambda row: "wrong" if "timeline" in row else "yes", ranked=[])
    cache = {}
    bad, _ = P.picture_check(None, {"pictures": pictures}, clock, product, cache, roles=P.roles_of(sc))
    assert [b[0] for b in bad] == ["marine.jpg"]
    assert "line role: setup" in seen[0][1]                                          # the check knows the line is a pain line
    cands = P.repair_candidates(pack, sc, {2}, l1=l1, win_s=0.5)
    body = [c for c in cands if c["path"].endswith("export_body.mp4")]
    shown = [(0.0, 2.0), (3.0, 4.5), (4.0, 6.0)]                 # sh1, sh3 and the after: export seconds already on screen
    assert [c["t"] for c in body] == [2.0, 2.5]                  # only the export's free seconds, each a distinct instant
    assert all(not (c["t"] + 0.5 > a and c["t"] < b) for c in body for a, b in shown)
    _wire(monkeypatch, lambda row: "yes", ranked=[body[1]["id"]])
    rec, _ = P.repair_misfits(None, l1, {"pictures": pictures}, clock, product, cache, None, pack, sc)
    assert rec["repaired"] and rec["repaired"][0]["was"] == "marine.jpg" and rec["repaired"][0]["now"] == "export_body.mp4"
    shot = next(it for it in l1["items"] if it["id"] == "sh2")
    assert shot["pic"] == "v00" and shot["f"] == [60, 75] and shot["src"] == [2.5, 3.0]
    assert not any(shot["src"][1] > a and shot["src"][0] < b for a, b in shown)  # the repair never plays a shown second again
    assert isinstance(shot["crop"][0], list) and len(shot["crop"]) == 15 and "look" not in shot     # moving, shown as delivered
    assert pictures[1]["path"].endswith("export_body.mp4") and pictures[1]["tier"] == "repair"
    assert not [it for it in l1["items"] if it["id"] == "cap1"]                     # no narration caption over the real export
    assert not [p for p in pictures if not (cache.get(P.picture_key(p, P.words_under(clock, *p["f"]))) or {}).get("fits")]


def test_a_window_nothing_fits_better_keeps_what_it_had(tmp_path, monkeypatch):
    l1, pictures, pack, sc, product = _film(tmp_path)
    clock = SimpleNamespace(words=[])
    before = json.loads(json.dumps(l1))
    _wire(monkeypatch, lambda row: "loose" if "timeline" in row else "yes", ranked=[])
    cache = {}
    P.picture_check(None, {"pictures": pictures}, clock, product, cache)
    cands = P.repair_candidates(pack, sc, {2}, l1=l1, win_s=0.5)
    _wire(monkeypatch, lambda row: "wrong", ranked=[c["id"] for c in cands if c["path"].endswith("export_body.mp4")][:3])
    rec, _ = P.repair_misfits(None, l1, {"pictures": pictures}, clock, product, cache, None, pack, sc)
    assert rec["misfits"] == 1 and not rec["repaired"]
    assert sorted(json.dumps(i, sort_keys=True) for i in l1["items"]) == sorted(json.dumps(i, sort_keys=True) for i in before["items"])
    assert pictures[1]["path"].endswith("marine.jpg")


def test_no_misfit_no_look(tmp_path, monkeypatch):
    l1, pictures, pack, sc, product = _film(tmp_path)
    monkeypatch.setattr(P, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no look needed")))
    monkeypatch.setattr(P, "words_under", lambda *a: "w")
    cache = {P.picture_key(p, "w"): dict(fits=True, fit="yes") for p in pictures}
    rec, spend = P.repair_misfits(None, l1, {"pictures": pictures}, None, product, cache, None, pack, sc)
    assert rec == dict(misfits=0, repaired=[]) and spend == []


# ----------------------------------------------------------------- the repair's second round (20 Nov review)

def test_an_unanswered_recheck_keeps_the_films_own_pictures_and_carries_on(tmp_path, monkeypatch):
    l1, pictures, pack, sc, product = _film(tmp_path)
    clock = SimpleNamespace(words=[])
    before = json.loads(json.dumps(l1))
    _wire(monkeypatch, lambda row: "wrong" if "timeline" in row else "yes", ranked=[])
    cache = {}
    P.picture_check(None, {"pictures": pictures}, clock, product, cache)
    cands = P.repair_candidates(pack, sc, {2}, l1=l1, win_s=0.5)
    calls = []

    def ask(system, content, **kw):
        calls.append(kw.get("what"))
        if kw.get("what") == "picture repair":
            return {"windows": [{"n": 0, "ranked": [next(c["id"] for c in cands if c["path"].endswith("export_body.mp4"))]}]}, {}
        raise RuntimeError("openai 503: no response")              # the re-check gets no answer, twice
    monkeypatch.setattr(P, "ask", ask)
    rec, spend = P.repair_misfits(None, l1, {"pictures": pictures}, clock, product, cache, None, pack, sc)
    assert calls == ["picture repair", "picture check", "picture check"]          # asked again once, then recovered
    assert rec["misfits"] == 1 and not rec["repaired"] and rec["faults"] and "503" in rec["faults"][0]["fault"]
    assert sorted(json.dumps(i, sort_keys=True) for i in l1["items"]) == sorted(json.dumps(i, sort_keys=True) for i in before["items"])
    assert pictures[1]["path"].endswith("marine.jpg") and pictures[1].get("tier") != "repair"
    assert any(s.get("fault") for s in spend)


def test_an_unanswered_first_check_is_asked_again_then_the_film_carries_on(tmp_path, monkeypatch):
    _l1, pictures, _pack, _sc, product = _film(tmp_path)
    monkeypatch.setattr(P, "still", lambda *a, **k: "jpeg")
    monkeypatch.setattr(P, "words_under", lambda clock, f0, f1: f"words {f0}")
    n = []

    def ask(*a, **k):
        n.append(1)
        raise RuntimeError("stream ended with no response")
    monkeypatch.setattr(P, "ask", ask)
    bad, spend, fault = P.checked(None, {"pictures": pictures}, SimpleNamespace(words=[]), product, {})
    assert len(n) == 2 and "no response" in fault and len(bad) == 3
    assert all(w == "missing picture verdict" for _, _, w in bad)


def test_the_role_is_the_line_whose_words_sit_under_the_picture(tmp_path, monkeypatch):
    _l1, pictures, _pack, sc, product = _film(tmp_path)
    seen = _wire(monkeypatch, lambda row: "yes", ranked=[])
    sc["segments"].append(dict(beat="payoff", pictures=["Editor at a video timeline"]))
    p = dict(pictures[1], line=4, spoken_line=2)                     # picked for the payoff, spoken over a pain line
    P.picture_check(None, {"pictures": [p]}, SimpleNamespace(words=[]), product, {}, roles=P.roles_of(sc))
    assert "line role: setup" in seen[0][1] and "line role: payoff" not in seen[0][1]


def test_a_clip_shorter_than_the_window_records_the_seconds_it_really_plays(tmp_path, monkeypatch):
    l1, pictures, pack, sc, product = _film(tmp_path)
    (tmp_path / "laugh.mp4").write_bytes(b"x")
    pack["footage"].append(dict(i=2, clips=[dict(path=str(tmp_path / "laugh.mp4"), subject="a crowd laughing", seconds=0.4, w=1920, h=1080)]))
    clock = SimpleNamespace(words=[])
    _wire(monkeypatch, lambda row: "wrong" if "timeline" in row else "yes", ranked=[])
    cache = {}
    P.picture_check(None, {"pictures": pictures}, clock, product, cache)
    cands = P.repair_candidates(pack, sc, {2}, l1=l1, win_s=0.5)
    laugh = next(c for c in cands if c["path"].endswith("laugh.mp4"))
    _wire(monkeypatch, lambda row: "yes", ranked=[laugh["id"]])
    rec, _ = P.repair_misfits(None, l1, {"pictures": pictures}, clock, product, cache, None, pack, sc)
    shot = next(it for it in l1["items"] if it["id"] == "sh2")
    assert shot["src"] == [0.0, 0.4] and pictures[1]["src"] == [0.0, 0.4] and rec["repaired"][0]["src"] == [0.0, 0.4]
    assert cache[P.picture_key(pictures[1], P.words_under(clock, *pictures[1]["f"]))]["fits"]


def test_an_export_window_repaired_to_footage_keeps_its_narration_caption_pool_and_finish(tmp_path, monkeypatch):
    l1, pictures, pack, sc, product = _film(tmp_path)
    (tmp_path / "crowd.mp4").write_bytes(b"x")
    pack["footage"].append(dict(i=1, clips=[dict(path=str(tmp_path / "crowd.mp4"), subject="a crowd laughing", seconds=3.0, w=1920, h=1080)]))
    l1["items"][0]["look"] = "mono_silver"                                        # sh1 plays the export at frames 0-60
    l1["items"] += [dict(id="cap0", k="word", f=[10, 20], t="black", v="cap", role="cap"),
                    dict(id="pool", k="card", f=[0, 120], z=1, pic="plate", op=[0.7] * 120)]
    pre = json.loads(json.dumps(l1["items"]))
    P.as_delivered(l1, product)                                                   # the recording shown as delivered
    assert not [it for it in l1["items"] if it["id"] == "cap0"] and "look" not in l1["items"][0]
    clock = SimpleNamespace(words=[])
    _wire(monkeypatch, lambda row: "wrong" if "'words 0'" in row else "yes", ranked=[])
    cache = {}
    P.picture_check(None, {"pictures": pictures}, clock, product, cache)
    cands = P.repair_candidates(pack, sc, {1}, l1=l1, win_s=2.0)
    crowd = next(c for c in cands if c["path"].endswith("crowd.mp4"))
    _wire(monkeypatch, lambda row: "yes", ranked=[crowd["id"]])
    rec, _ = P.repair_misfits(None, l1, {"pictures": [pictures[0]]}, clock, product, cache, None, pack, sc, pre=pre)
    assert rec["repaired"][0]["now"] == "crowd.mp4" and rec["off_recording"]["windows"] == [[0, 60]]
    assert [it for it in l1["items"] if it["id"] == "cap0"]                       # the narration caption is back on the footage
    sh1 = next(it for it in l1["items"] if it["id"] == "sh1")
    assert sh1["look"] == "mono_silver"                                           # its section finish too
    pool = sorted(tuple(it["f"]) for it in l1["items"] if str(it["id"]).startswith("pool"))
    assert (0, 60) in pool and not any(a < 120 and b > 75 for a, b in pool)       # the pool covers the footage, not the export


def test_a_repaired_windows_sounds_follow_its_new_picture(tmp_path, monkeypatch):
    l1, pictures, pack, sc, product = _film(tmp_path)
    l1["assets"]["v00"]["landings"] = [dict(t=2.7, kind="picture")]            # the export lands a picture at 2.7 s
    l1["sound"] = [dict(s="shutter_1", event_f=62), dict(s="click_1", event_f=10), dict(s="tick_1", event_f=70, device="dv")]
    clock = SimpleNamespace(words=[])
    _wire(monkeypatch, lambda row: "wrong" if "timeline" in row else "yes", ranked=[])
    cache = {}
    P.picture_check(None, {"pictures": pictures}, clock, product, cache)
    cands = P.repair_candidates(pack, sc, {2}, l1=l1, win_s=0.5)
    late = next(c for c in cands if c["path"].endswith("export_body.mp4") and c["t"] == 2.5)
    _wire(monkeypatch, lambda row: "yes", ranked=[late["id"]])
    rec, _ = P.repair_misfits(None, l1, {"pictures": pictures}, clock, product, cache, None, pack, sc)
    assert rec["repaired"]
    planned = []

    def design(l1_, sup, d, turn_f=None, spoken=None, np10=None):            # the engine, offline: a sound on each landing
        planned.append(turn_f)
        return [dict(s="paper_1", event_f=e["f"]) for e in F.landings_shown(l1_)] + [dict(s="click_1", event_f=10)], {}
    monkeypatch.setattr(F, "design_sound", design)
    got = P.resound(l1, pack, dict(bed=True), 300, clock, [tuple(r["frames"]) for r in rec["repaired"]])
    by = sorted((s["event_f"], s["s"]) for s in l1["sound"])
    assert by == [(10, "click_1"), (66, "paper_1"), (70, "tick_1")]               # the stale shutter went; the landing sounds
    assert got == dict(windows=1, dropped=1, added=1) and planned == [300]


def test_recording_candidates_name_the_words_said_there(tmp_path, monkeypatch):
    (tmp_path / "edl").mkdir()
    (tmp_path / "edl" / "v002.json").write_text(json.dumps(dict(keep=[[1.0, 3.0], [5.0, 8.0]])))
    (tmp_path / "record.json").write_text(json.dumps(dict(source=dict(file="pod.mp4", t0=100.0, t1=110.0),
                                                          exports=[dict(tag="final", edl_version=2, file="e.mp4")])))
    monkeypatch.setattr(P, "sources", lambda: [dict(file="pod.mp4", captions="c.json3")])
    monkeypatch.setattr(P, "json3_words", lambda p: [(101.2, "trash"), (102.5, "can"), (105.4, "black"), (105.9, "hole"), (109.0, "end")])
    said = P.recording_words(dict(record=str(tmp_path / "record.json")))
    assert said["export"] == [(0.2, "trash"), (1.5, "can"), (2.4, "black"), (2.9, "hole")]
    l1, pictures, pack, sc, product = _film(tmp_path)
    cands = P.repair_candidates(pack, sc, {2}, l1=l1, said=said, win_s=0.5)
    assert next(c for c in cands if c["path"].endswith("export_body.mp4") and c["t"] == 2.5)["said"] == "black hole"
    assert P.recording_words(dict(record=str(tmp_path / "none.json"))) == {}
