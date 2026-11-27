"""The Director on a built story film (wf3.director_devices) and round a marketing reel's hero device
(wf3.with_director): its asks ride the scene under ``devices`` and the palette lays every one of them."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [p for p in (os.path.join(os.path.dirname(HERE), "_shell"), HERE) if p not in sys.path]
import film as F  # noqa: E402
import wf3  # noqa: E402

SAY = ["He sold the company for one dollar.", "Ten years later it was worth a billion.", "Nobody remembers his name."]


def built(tmp_path):
    from PIL import Image
    words, t = [], 0.0
    for i, s in enumerate(SAY):
        for w in s.split():
            words.append(dict(w=w, start=t, end=t + 0.3, seg=i))
            t += 0.36
        t += 0.4
    wj = tmp_path / "words.json"
    wj.write_text(json.dumps(dict(words=words, duration=t)))
    pics = []
    for k, c in enumerate(((30, 60, 90), (90, 40, 20), (20, 80, 30))):
        p = tmp_path / f"p{k}.png"
        Image.new("RGB", (360, 640), c).save(p)
        pics.append(str(p))
    dur = F.Clock.f(t + F.LEAD_F / F.FPS) + 15
    third = dur // 3
    items = [dict(id=f"s{k}", k="shot", f=[k * third, dur if k == 2 else (k + 1) * third], pic=f"p{k}", crop=[0, 0, 1, 1]) for k in range(3)]
    l1 = dict(l1=1, sid="t", seed=3, fps=F.FPS, frame=[1080, 1920], dur_f=dur, spine=[], voices={}, film={}, items=items, sound=[],
              assets={f"p{k}": dict(kind="image", path=pics[k]) for k in range(3)})
    beats = dict(pictures=[dict(f=it["f"], path=pics[k], kind="photo", shows=f"picture {k}") for k, it in enumerate(items)])
    pack = dict(words=str(wj), narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg")
    sc = dict(title="t", segments=[dict(say=s) for s in SAY])
    return l1, beats, pack, sc


@pytest.fixture
def directed(monkeypatch):
    module = wf3._director()
    direct = module.direct
    def answer(*args, **kwargs):
        return json.dumps(dict(asks=[dict(dev="white", at=dict(w=0)), dict(dev="neg", at=dict(w=17))])), {}
    monkeypatch.setattr(module, "direct", lambda *args, **kwargs: direct(*args, ask=answer, **kwargs))


def test_the_director_lays_devices_on_a_built_story(tmp_path, directed):
    l1, beats, pack, sc = built(tmp_path)
    ctx = SimpleNamespace(supply=str(tmp_path), log=lambda _: None, args={}, workflow="wf3")
    rec = wf3.director_devices(ctx, dict(style=dict(id="reel")), sc, pack, l1, beats)
    assert rec["by"] == "model" and rec["asked"]                      # a test never reaches a model: the signal director
    assert l1["devices"] and all(isinstance(a["f"], (int, list)) for a in l1["devices"])
    import director
    assert json.load(open(tmp_path / "director.json"))["contract"] == director.CONTRACT
    worker = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE)))), "worker")
    if not os.path.isdir(os.path.join(worker, "scene", "palette")):
        pytest.skip("no worker beside this plugin")
    sys.path[:0] = [worker]
    from scene import palette
    out, log = palette.apply(l1, str(tmp_path))
    assert log and all(r["done"] for r in log), [r for r in log if not r["done"]]


def test_a_marketing_reel_gets_the_directors_devices_round_its_hero(tmp_path, monkeypatch, directed):
    l1, beats, pack, sc = built(tmp_path)
    click = dict(id="timeline.send", device="timeline", event_f=21, s="click")
    l1["sound"].append(click)
    (tmp_path / "l1.json").write_text(json.dumps(l1))
    monkeypatch.setattr(wf3, "load_pack", lambda ctx: pack)
    ctx = SimpleNamespace(supply=str(tmp_path), log=lambda _: None, args={}, workflow="wf2")
    def compose(ctx, source):
        wf3._director().palette_module()
        from scene import palette
        doc, _ = palette.apply(json.load(open(source)), ctx.supply)
        path = tmp_path / "laid.json"
        path.write_text(json.dumps(doc))
        return str(path)
    monkeypatch.setattr(wf3.chain, "lay_devices", compose)
    hero = [0, l1["dur_f"] // 2]
    path = wf3.with_director(ctx, dict(script=sc, style=dict(id="reel")), dict(l1=str(tmp_path / "l1.json"), beats=beats,
                                                                             devices=dict(laid=[dict(frames=hero)])))
    got = json.load(open(path))
    assert path.endswith("l1_director.json") and got["director_performance"]["asked"]
    assert click in got["sound"]
    rec = got["director_performance"]
    assert rec["performed"][0]["frames"] == hero
    starts = [a["f"][0] if isinstance(a["f"], list) else a["f"] for a in rec["placements"]]
    assert all(not (hero[0] <= f < hero[1]) for f in starts)            # the hero device keeps its frames
    other = SimpleNamespace(supply=str(tmp_path), log=lambda _: None, args={}, workflow="wf3")
    assert wf3.with_director(other, {}, dict(l1=str(tmp_path / "l1.json"))) == str(tmp_path / "l1.json")
