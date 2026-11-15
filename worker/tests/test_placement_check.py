import json
from types import SimpleNamespace

from PIL import Image

from score import compiler, placement_check as pc


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
    monkeypatch.setattr(compiler, "compile_score", lambda text, **kw: text)
    result, text, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 1e20)
    assert [w["at"] for w in seen["wishes"]] == [21, 31]
    assert [w["placed_words"] for w in seen["wishes"]] == ["qualified airline pilot", "aviation"]
    cuts = [json.loads(line) for line in text.splitlines()][1:]
    assert [r["id"] for r in cuts] == ["b1.s1"]
    assert report["removed"] == ["b1.s1"] and result == text
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
    monkeypatch.setattr(compiler, "compile_score", lambda text, **kw: text)
    _, _, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 1e20)
    assert frames == [4., 5., 6.-1/30]
    assert report["removed"] == ["b1.s1"]


def test_unreadable_video_cannot_inherit_the_search_thumbnails_approval(tmp_path, monkeypatch):
    comp = scene(tmp_path, "video")
    comp.l1["items"] = comp.l1["items"][:1]
    monkeypatch.setattr(pc.boards, "grab", lambda *a, **k: None)
    monkeypatch.setattr(compiler, "compile_score", lambda text, **kw: text)
    _, text, report = pc.check(str(tmp_path), comp, '{"k":"film"}', 1e20)
    assert report["removed"] == ["b1.s1"]
    assert json.loads(text.splitlines()[-1])["id"] == "b1.s1"
