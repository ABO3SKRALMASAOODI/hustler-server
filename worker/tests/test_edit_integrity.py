from types import SimpleNamespace as NS
import pytest
import edit_integrity as ei
from score import asks, patch
import agent_loop


def test_removed_spans_measures_exact_source_coverage():
    assert ei.removed_spans([[0, 11.264]], [[0, 3.6]]) == [(3.6, 11.264)]
    assert ei.removed_spans([[0, 2], [4, 7]], [[0, 7]]) == []
    assert ei.removed_spans([[0, 10]], [[1, 3], [5, 8]]) == [(0, 1), (3, 5), (8, 10)]


def test_eye_patch_rejected_on_source_even_if_render_lost_face():
    ctx = NS(duration=5.6, index={"spatial": {"samples": [{"t": 2., "faces": [[.3, .1, .7, .4]]}]}})
    assert "face" in ei.patch_face_refusal(ctx, [{"x": .3, "y": .2, "w": .2, "h": .1}])
    assert ei.patch_face_refusal(ctx, [{"x": .3, "y": .6, "w": .2, "h": .1}]) is None
    assert ei.patch_face_refusal(ctx, [{"x": .3, "y": .2, "w": .2, "h": .1, "start": 3, "end": 4}]) is None


def test_cut_checks_walk_pixels_not_just_silence(monkeypatch, tmp_path):
    import media, llm
    seen = []
    monkeypatch.setattr(media, "frame_at", lambda *a, **k: seen.append(a[1]))
    monkeypatch.setattr(llm, "image_part", lambda p: {"type": "image_url", "image_url": {"url": p}})
    def call(ctx, name, schema, system, parts, **kw):
        assert name == "check_cut_evidence"
        assert len([p for p in parts if p['type'] == 'image_url']) == 3
        return {"allow": False, "reason": "continuous walking, not a wait"}, {}
    monkeypatch.setattr(asks, "_call", call)
    ctx = NS(user_message="Remove waiting gaps, preserve natural pauses", llm_client=object(), agent_model="any",
             workdir=str(tmp_path), proxy_path=lambda: "source.mp4", index={})
    assert "continuous walking" in ei.cut_refusal(ctx, {"keep": [[0,11.264]]}, {"keep": [[0,3.6]]})
    assert len(seen) == 3
    assert ei.cut_refusal(ctx, {"keep": [[0,3.6]]}, {"keep": [[0,11.264]]}) is None


def test_failed_truth_rewrite_never_sends_done(monkeypatch):
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    verdict = {"asks": [{"id": "a1", "state": "not_done", "seen": "dancers darkened", "line": "The background change is unfinished."}]}
    assert asks.true_reply(NS(), {"asks": []}, verdict, "Done!") == "The background change is unfinished."


def test_stale_check_cannot_back_completion():
    ctx = NS(ask_ledger={"asks": [{"id":"a1", "kind":"edit"}]},
             ask_verdict={"edl_version":1, "asks": [{"id":"a1","state":"done"}]},
             latest_edl=lambda: {"version":2})
    reply, outcome = agent_loop._asks_settle(ctx, "Done!", "fulfilled")
    assert "can't confirm" in reply and outcome == "partial"


def test_unused_image_pixels_reach_ledger(monkeypatch):
    import llm
    monkeypatch.setattr(patch, "_own_media", lambda *a: [{"n":2,"key":"images/new.jpg","kind":"image_ref","name":"hair-down.jpg","frame":"image.jpg"}])
    monkeypatch.setattr(llm, "image_part", lambda p: {"type":"image_url", "image_url":{"url":p}})
    def call(ctx, name, schema, system, content, **kw):
        assert isinstance(content,list)
        assert any("images/new.jpg" in p.get('text','') for p in content)
        assert any(p['type'] == 'image_url' for p in content)
        return {"asks":[{"kind":"edit","want":"use uploaded hair-down image"}]}, {}
    monkeypatch.setattr(asks, "_call", call)
    ledger = asks.build(NS(latest_edl=lambda: {"json":{"keep":[[0,7]]}}, index={}, duration=7), "Use the hair-down version")
    assert ledger['asks'][0]['want'] == "use uploaded hair-down image"


def test_background_rectangle_cannot_dim_face():
    ctx = NS(index={"spatial":{"samples":[{"t":2,"faces":[[.4,.2,.6,.4]]}]}})
    before={"keep":[[0,11]]}
    after=dict(before,fx=[{"id":"rg1","type":"region","start":0,"end":11,
                          "effect":"exposure","ev":-2,"region":{"shape":"rect","box":[0,0,1,.7]}}])
    assert "covers a measured face" in ei.face_cover_refusal(ctx,before,after)
    after['fx'][0]['region']['box']=[0,0,.15,.7]
    assert ei.face_cover_refusal(ctx,before,after) is None


def test_missing_frames_cannot_pass_visual_ask(monkeypatch):
    monkeypatch.setattr(asks,"frame_pages", lambda *a: [])
    monkeypatch.setattr(asks,"_call", lambda *a,**k: ({"asks":[{"id":"a1","state":"done","rests_on":"frames","seen":"changed","line":"Done"}],"unasked":[]},{}))
    ledger=asks.settle({"asks":[{"kind":"edit","how":"look","want":"background"}]}, {"keep":[[0,10]]})
    v=asks.check(NS(index={},duration=10),ledger,"Done",{"keep":[[0,10]]},preview={"edl_version":2})
    assert v['asks'][0]['state']=='cannot_tell'
    assert v['asks'][0]['line']!='Done'
