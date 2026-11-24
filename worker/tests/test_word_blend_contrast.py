from types import SimpleNamespace
from scene.palette import kit, punct


def test_multiply_preserves_readable_staircase_on_dark_and_light_ground(monkeypatch):
    word = dict(id="steps.r1",k="word",role="big",v="dsp",f=[510,543],ink="#F2C14E",t="PROMOTION")
    sense = SimpleNamespace(words_in=lambda *args: [word])
    scene = dict(frame=[1080,1920],fps=30,frames=900,items=[word],voices={})
    monkeypatch.setattr(punct,"span",lambda *args:(532,543))
    monkeypatch.setattr(kit,"item_box",lambda *args:[.08,.4,.92,.5])
    for paper,expected in (("#151A20",False),("#EEEEEE",True)):
        scene["items"] = [word,dict(k="shot",pic="plate",f=[0,900],plate=dict(c0=paper))]
        item, = punct.word_blend(scene,".",dict(blend="multiply"),sense)["items"]
        assert (item.get("blend")=="multiply") is expected
        assert kit.lum(item["ink"]) > .60 if not expected else kit.lum(item["ink"]) < .35
