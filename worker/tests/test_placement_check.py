import json
from types import SimpleNamespace

from PIL import Image

from score import compiler, placement_check as pc


def remove_cut_items(comp):
    def compile_score(text, **kw):
        cuts = {row["id"] for row in map(json.loads, text.splitlines()) if row.get("k") == "cut"}
        comp.l1["items"] = [item for item in comp.l1["items"] if item["id"] not in cuts]
        return comp
    return compile_score


def scene(tmp_path, kind="image"):
    (tmp_path/"index.json").write_text(json.dumps({"words": [
        {"w": "qualified", "t0": 20.5, "t1": 21},
        {"w": "airline", "t0": 21, "t1": 21.5},
        {"w": "pilot", "t0": 21.5, "t1": 22},
        {"w": "aviation", "t0": 30.5, "t1": 31},
    ]}))
    (tmp_path/"bin.json").write_text(json.dumps({"items": [
        {"id": 0, "path": "picture.jpg", "kind": kind, "wish": 0,
         "what": "An aircraft", "word_check": {"ok": True, "said": "aviation"}}
    ], "wishes": [{"wish": 0, "how": "thing"}]}))
    Image.new("RGB", (80, 80)).save(tmp_path/"picture.jpg")
    return SimpleNamespace(l1={"fps": 30, "spine": [
        {"f": [0, 600], "src": [20, 40]}],
        "assets": {"bin:0": {"path": "picture.jpg", "kind": kind}},
        "items": [{"k": "shot", "id": "b1.s1", "pic": "bin:0", "f": [0, 60]},
                  {"k": "shot", "id": "b2.s1", "pic": "bin:0", "f": [300, 360]}]})


def test_reused_picture_is_checked_against_each_actual_spoken_passage(tmp_path, monkeypatch):
    comp = scene(tmp_path)
    seen = {}
    def check_fit(work, candidates, wishes, deadline):
        seen["wishes"] = wishes
        return [candidates[1]], .01, [{"id": 0, "ok": False}, {"id": 1, "ok": True}]
    monkeypatch.setattr(pc.supply, "check_fit", check_fit)
    monkeypatch.setattr(compiler, "compile_score", remove_cut_items(comp))
    result, text, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 1e20)
    assert [w["at"] for w in seen["wishes"]] == [21, 31]
    assert [w["placed_words"] for w in seen["wishes"]] == ["qualified airline pilot", "aviation"]
    cuts = [json.loads(line) for line in text.splitlines()][1:]
    assert [r["id"] for r in cuts] == ["b1.s1"]
    assert report["removed"] == ["b1.s1"] and result is comp
    assert report["checked"] == 2  # the unchanged valid occurrence keeps its approval
    assert not [p for p in tmp_path.glob("placement-*") if p.is_dir()]


def test_every_frame_of_used_video_interval_must_match(tmp_path, monkeypatch):
    comp = scene(tmp_path, "video")
    comp.l1["items"] = [dict(comp.l1["items"][0], src=[4., 6.])]
    frames = []
    def grab(path, t, **kwargs):
        frames.append(t)
        return Image.new("RGB", (80, 80))
    monkeypatch.setattr(pc.boards, "grab", grab)
    monkeypatch.setattr(pc.supply, "check_fit", lambda w, c, wishes, d:
                        ([c[0], c[2]], 0., [{"id": 1, "ok": False}]))
    monkeypatch.setattr(compiler, "compile_score", remove_cut_items(comp))
    _, _, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 1e20)
    assert frames == [4., 5., 6.-1/30]
    assert report["removed"] == ["b1.s1"]


def test_unreadable_video_cannot_inherit_the_search_thumbnails_approval(tmp_path, monkeypatch):
    comp = scene(tmp_path, "video")
    comp.l1["items"] = comp.l1["items"][:1]
    monkeypatch.setattr(pc.boards, "grab", lambda *a, **k: None)
    monkeypatch.setattr(compiler, "compile_score", remove_cut_items(comp))
    _, text, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 1e20)
    assert report["removed"] == ["b1.s1"]
    assert json.loads(text.splitlines()[-1])["id"] == "b1.s1"


def test_removing_a_video_use_rechecks_the_later_compilers_shifted_source(tmp_path, monkeypatch):
    from score import pace
    from tests.test_score_compiler import talk_index
    from tests.test_score_taste import film, beats_over
    monkeypatch.setattr(pace, "on", lambda: False)
    idx = talk_index(12.)
    (tmp_path/"index.json").write_text(json.dumps(idx))
    (tmp_path/"bin.json").write_text(json.dumps({"items": [
        {"id": 0, "path": "video.mp4", "kind": "video", "w": 1080,
         "h": 1920, "dur": 40, "wish": 0}], "wishes": []}))
    text = film(beats_over(idx, 2, lambda k, a, z: {"pic": "bin:0"}))
    comp = compiler.compile_score(text, work_dir=str(tmp_path), govern=True)
    used = [i for i in comp.l1["items"] if i.get("pic") == "bin:0"]
    assert len(used) >= 2
    first, later = used[:2]
    later_original = list(later["src"])
    monkeypatch.setattr(pc.boards, "grab", lambda *a, **kw: Image.new("RGB", (80, 80)))
    calls = []
    def fit(work, candidates, wishes, deadline):
        calls.append(len(candidates))
        # First placement fails; the later old interval passes. Its shifted
        # interval must face a new check and fails on that check.
        kept = candidates[3:] if len(calls) == 1 else []
        return kept, .01, [{"id": c["id"], "ok": c in kept} for c in candidates]
    monkeypatch.setattr(pc.supply, "check_fit", fit)
    after, _, report = pc.check(str(tmp_path), comp, text, 1e20)
    assert len(calls) >= 2 and first["id"] in report["removed"] and later["id"] in report["removed"]
    later_checks = [p for p in report["placements"] if p["id"] == later["id"]]
    assert later_checks[0]["picture_source_s"] == later_original[0]
    assert any(p["picture_source_s"] != later_original[0] for p in later_checks[3:])
    assert not any(i.get("pic") == "bin:0" for i in after.l1["items"])


def test_rejected_collage_child_removes_its_device_without_abandoning_the_film(tmp_path, monkeypatch):
    from score import pace
    from tests.test_score_compiler import talk_index
    from tests.test_score_taste import film, beats_over
    monkeypatch.setattr(pace, "on", lambda: False)
    idx = talk_index(12.)
    (tmp_path/"index.json").write_text(json.dumps(idx))
    rows = []
    (tmp_path/"bin").mkdir()
    for n in range(3):
        Image.new("RGB", (300, 500), (n*70, 80, 100)).save(tmp_path/"bin"/f"{n}.jpg")
        rows.append({"id": n, "path": f"bin/{n}.jpg", "kind": "image", "w": 300, "h": 500})
    (tmp_path/"bin.json").write_text(json.dumps({"items": rows, "wishes": []}))
    text = film(beats_over(idx, 2, lambda k, a, z: {
        "pic": "main" if k == 0 else "bin:0",
        "do": [{"g": "split", "pic": ["bin:1", "bin:2"],
                "at": f"w{a+2}..w{a+4}.e", "for": f"w{a+2}"}] if k == 0 else []}))
    before = compiler.compile_score(text, work_dir=str(tmp_path), govern=True)
    child = next(i for i in before.l1["items"] if i["id"] == "b01.split.0")
    monkeypatch.setattr(pc.supply, "check_fit", lambda w, c, wishes, d:
        ([x for x in c if x["path"] != "bin/1.jpg"], 0.,
         [{"id": x["id"], "ok": x["path"] != "bin/1.jpg"} for x in c]))
    after, _, report = pc.check(str(tmp_path), before, text, 1e20)
    assert child["id"] in report["removed"]
    assert not any(i["id"].startswith("b01.split") for i in after.l1["items"])
    assert any(i.get("pic") == "bin:0" for i in after.l1["items"])
    assert any(i.get("pic") == "main" and i["f"][0] <= child["f"][0]
               and i["f"][1] >= child["f"][1] for i in after.l1["items"])


def test_multiple_check_waves_share_the_film_budget_not_one_boards_timeout(tmp_path, monkeypatch):
    comp = scene(tmp_path)
    import time
    monkeypatch.setattr(time, "monotonic", lambda: 100.)
    def check_fit(work, candidates, wishes, deadline):
        # Four concurrent boards can finish within18s; the next wave still
        # has the remaining film budget. Per-call timeouts live in check_fit.
        assert deadline == 160.
        return candidates, 0., [{"id": c["id"], "ok": True} for c in candidates]
    monkeypatch.setattr(pc.supply, "check_fit", check_fit)
    after, _, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 160.)
    assert len(after.l1["items"]) == 2 and report["removed"] == []


def test_checker_unavailability_is_retained_separately_from_a_picture_mismatch(tmp_path, monkeypatch):
    comp = scene(tmp_path)
    monkeypatch.setattr(pc.supply, "check_fit", lambda *args:
                        ([], 0., [{"check_error": "model deadline"}]))
    monkeypatch.setattr(compiler, "compile_score", remove_cut_items(comp))
    after, _, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 1e20)
    assert report["errors"] == ["model deadline"]
    assert after.l1["items"] == []  # no missing verdict becomes approval
