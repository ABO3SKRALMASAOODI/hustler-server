"""chain.hear_devices: the sound design hears the Director's devices. The scene's asks are laid (lay_devices), the
palette's landings are copied onto the scene and its beats, and the sound is planned again on them; a reel's own
device sounds stay; a fault keeps the film's sound. film.replan_sound plans with the first plan's own turn and words."""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "valmera-stories"))
import chain  # noqa: E402


def _ctx(tmp_path):
    return types.SimpleNamespace(supply=str(tmp_path), run_id="t", log=lambda *a: None)


def _laid(monkeypatch, events):
    def lay(ctx, path):
        doc = json.load(open(path))
        out = path + ".laid.json"
        json.dump(dict(doc, device_events=events), open(out, "w"))
        return out
    monkeypatch.setattr(chain, "lay_devices", lay)
    monkeypatch.setattr(chain, "engine_kind", lambda: "scene")


def test_the_sound_is_planned_again_on_the_landings(tmp_path, monkeypatch):
    ev = [dict(f=40, kind="glitch", strength=0.9, dev="rgb_split", id="dr1")]
    _laid(monkeypatch, ev)
    l1 = dict(dur_f=120, devices=[dict(dev="rgb_split", f=40)], sound=[dict(id="old", event_f=10), dict(id="hero", event_f=80, device=True)])
    beats = dict(events=[10, 80])
    seen = {}

    def replan(x):
        seen["events"] = x.get("device_events")
        return [dict(id="n1", event_f=40), dict(id="n2", event_f=81)], dict(placed=2)
    rec = chain.hear_devices(_ctx(tmp_path), l1, beats, replan)
    assert seen["events"] == ev, "the sound design reads the palette's landings"
    assert [s["id"] for s in l1["sound"]] == ["n1", "hero"], "the fresh plan, the reel's own device sound kept, no double"
    assert l1["sound_design"] == dict(placed=2) and beats["events"] == [10, 40, 80] and beats["device_events"] == ev
    assert rec["on_devices"] == 1 and rec["frames"] == [40]


def test_a_fault_keeps_the_films_sound_and_a_scene_without_devices_is_left(tmp_path, monkeypatch):
    _laid(monkeypatch, [dict(f=5, kind="white", strength=0.9)])
    l1 = dict(dur_f=30, devices=[dict(dev="white", f=5)], sound=[dict(id="a", event_f=3)], sound_design=dict(x=1))
    rec = chain.hear_devices(_ctx(tmp_path), l1, None, lambda x: ([], {"fault": "no plan"}))
    assert rec["fault"] and l1["sound"] == [dict(id="a", event_f=3)] and l1["sound_design"] == dict(x=1)
    plain = dict(dur_f=30, sound=[])
    assert chain.hear_devices(_ctx(tmp_path), plain, None, lambda x: 1 / 0) == {} and plain == dict(dur_f=30, sound=[])


def test_replan_sound_follows_the_first_plan(monkeypatch):
    import film as F
    got = {}

    def design(l1, sup, d, turn_f=None, spoken=None, np10=None):
        got.update(d=d, turn_f=turn_f, spoken=spoken, np10=np10, events=l1.get("device_events"))
        return [], {}
    monkeypatch.setattr(F, "design_sound", design)
    l1 = dict(device_events=[dict(f=3)], sound_design=dict(replan=dict(turn_f=90, np10=7.5, bed=True, delivery_tail_s=1.2, tick_10s=2.0,
                                                                        spoken=[[0.1, 0.4], [0.5, 0.9]])))
    F.replan_sound(l1, {})
    assert got["turn_f"] == 90 and got["np10"] == 7.5 and got["spoken"] == [(0.1, 0.4), (0.5, 0.9)]
    assert got["d"] == dict(bed=True, delivery_tail_s=1.2, tick_10s=2.0) and got["events"] == [dict(f=3)]
    assert F.replan_sound(dict(sound_design={}), {})[1]["fault"]


def test_the_palette_runs_with_this_macs_models_named_in_its_environment(monkeypatch):
    monkeypatch.delenv("VALMERA_MODELS", raising=False)
    env = chain.model_env()
    assert f"VALMERA_MODELS='{chain.MODELS_DIR}'" in env
    monkeypatch.setenv("VALMERA_MODELS", "/elsewhere")
    assert "VALMERA_MODELS='/elsewhere'" in chain.model_env()


def test_verified_device_picture_survives_sound_replan_and_reopens_on_source_change(tmp_path,monkeypatch):
    import shlex
    ctx=_ctx(tmp_path)
    ctx.workflow="wf1"
    scene=tmp_path/"l1.json"
    source=tmp_path/"source.mp4"
    source.write_bytes(b"take A")
    doc=dict(assets={"main":{"path":str(source)}},items=[],devices=[{"dev":"whip"}],
             workflow_text_layout=True,sound=[{"id":"old"}])
    calls=[]
    def apply(ctx,cmd,**kwargs):
        flags=shlex.split(cmd[-1])
        def path(flag):return flags[flags.index(flag)+1]
        with open(path("--l1")) as f: drawn=json.load(f)
        with open(path("--out"),"w") as f:json.dump(dict(drawn,device_events=[{"f":811}]),f)
        with open(path("--log"),"w") as f:json.dump([{"done":True}],f)
        calls.append(1)
    monkeypatch.setattr(chain,"sh",apply)
    monkeypatch.setattr(chain,"engine_kind",lambda:"scene")
    for name in ("heard","render","changed"):
        path=tmp_path/(name+".json")
        path.write_text(json.dumps(doc))
        drawn=chain.lay_devices(ctx,str(path))
        assert json.load(open(drawn))["sound"]==doc["sound"]
        if name=="heard":doc["sound"]=[{"id":"new"}]
        if name=="render":source.write_bytes(b"take B")
    assert len(calls)==2
