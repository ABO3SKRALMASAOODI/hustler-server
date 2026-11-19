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
    cands = P.repair_candidates(pack, sc, {2})
    body_ids = [c["id"] for c in cands if c["path"].endswith("export_body.mp4")]
    assert all(not (4.0 < c["t"] + 1.0 and c["t"] < 6.0) for c in cands if c["path"].endswith("export_body.mp4"))   # never the after
    _wire(monkeypatch, lambda row: "yes", ranked=[body_ids[1]])
    rec, _ = P.repair_misfits(None, l1, {"pictures": pictures}, clock, product, cache, None, pack, sc)
    assert rec["repaired"] and rec["repaired"][0]["was"] == "marine.jpg" and rec["repaired"][0]["now"] == "export_body.mp4"
    shot = next(it for it in l1["items"] if it["id"] == "sh2")
    assert shot["pic"] == "v00" and shot["f"] == [60, 75] and shot["src"] == [1.3, 1.8]
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
    cands = P.repair_candidates(pack, sc, {2})
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
