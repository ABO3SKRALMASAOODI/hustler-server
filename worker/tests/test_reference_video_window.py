"""The owner-configured reference-video route keeps the original programme clock and sound."""
import shutil
from types import SimpleNamespace

import numpy as np
from PIL import Image

import agent_tools
import db
import media
import storage
import videorestyle
import videogen
from test_tool_contracts import _Ctx


def test_window_is_placed_once_with_original_audio_and_timing(tmp_path, monkeypatch):
    from w0b_probe import make_source
    from w1d_support import render
    import subprocess

    ctx = _Ctx(W=160, H=96, duration=4)
    ctx.workdir = str(tmp_path)
    ctx.project_id = 7
    ctx.job = {"id": 91}
    ctx.videos_generated, ctx.gen_extra_cost_usd = [], 0.
    src = make_source(str(tmp_path / "main.mp4"), w=160, h=96, dur=4)
    ctx.proxy_path = lambda: src
    ctx.index["video"].update(fps=25, duration=4)
    ref = tmp_path / "look.png"
    Image.new("RGB", (160, 96), "blue").save(ref)
    ctx._asset_locals = {1: str(ref)}
    assets = {"look": {"id": 1, "storage_key": "look", "kind": "image_ref"}}
    placed = tmp_path / "placed.mp4"
    def data(fn, *args):
        if fn is db.asset_by_key:
            return assets.get(args[1])
        if fn is db.kv_get:
            return None
        if fn is videorestyle._charge_once:
            return True
        if fn is videorestyle._save_asset:
            key = args[1]
            assets[key] = {"id": 2, "kind": "video_clip", "storage_key": key, "duration_s": args[3]}
            return
        raise AssertionError(fn)
    ctx.db = SimpleNamespace(run=data)
    monkeypatch.setattr(videorestyle.config, "VIDEO_RESTYLE_MODEL", "")
    monkeypatch.setattr(videorestyle.config, "VIDEO_RESTYLE_USD_PER_SECOND", "")
    assert agent_tools._tool_disabled("restyle_video")
    monkeypatch.setattr(videogen, "video_gen_available", lambda: True)
    monkeypatch.setattr(videorestyle.config, "VIDEO_RESTYLE_MODEL", "fixture/video-to-video/edit")
    monkeypatch.setattr(videorestyle.config, "VIDEO_RESTYLE_USD_PER_SECOND", "0.01")
    monkeypatch.setattr(agent_tools, "_gen_budget_reject", lambda *a: None)
    monkeypatch.setattr(agent_tools.llm, "charge_generation", lambda *a: None)
    monkeypatch.setattr(storage, "upload_file", lambda path, *_: shutil.copyfile(path, placed))
    requests = []
    def response(model, body, seconds, path, submit):
        assert body["video_url"].startswith("data:video/mp4;base64,")
        assert body["image_urls"][0].startswith("data:image/png;base64,")
        requests.append(seconds)
        media.run(["ffmpeg","-y","-f","lavfi","-i","color=blue:s=160x96:r=25:d=4",
                   "-an","-c:v","libx264","-pix_fmt","yuv420p",path])
        return True, None, seconds
    monkeypatch.setattr(videogen, "run_queue", response)
    for _ in range(2):
        videorestyle.restyle(ctx, "Use the reference look", "look", .5, 3.5)
    assert requests == [3]
    assert len(ctx.edl["overlays"]) == 1
    overlay = ctx.edl["overlays"][0]
    assert (overlay["start"], overlay["duration_s"], overlay["fit"]) == (.5,3,"cover")
    out = str(tmp_path / "delivered.mp4")
    duration, _ = render(ctx.edl, src, out, idx=ctx.index, duration=4,
                         asset_locals={overlay["asset_key"]:str(placed)})
    assert abs(duration - 4) < .06
    baseline = str(tmp_path / "original.mp4")
    render(dict(ctx.edl, overlays=[]), src, baseline, idx=ctx.index, duration=4)
    def pcm(path):
        return np.frombuffer(subprocess.check_output(["ffmpeg","-v","error","-i",path,
                             "-vn","-ac","1","-ar","8000","-f","f32le","-"]),np.float32)
    audio, original = pcm(out), pcm(baseline)
    assert len(audio) == len(original) and np.corrcoef(audio,original)[0,1] > .999
    pixel = subprocess.check_output(["ffmpeg","-v","error","-ss","1.5","-i",out,"-frames:v","1",
                                     "-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","-"])
    assert pixel[2] > max(pixel[:2]) + 125


def test_restyle_resumes_confirmed_queue_and_preserves_uncertain_submission(monkeypatch):
    import json
    state, posts = {}, []
    def data(fn, key, *args):
        if fn is videorestyle._reserve:
            fresh = key not in state
            return fresh, state.setdefault(key, {})
        if fn is videorestyle._release:
            del state[key]
        elif fn is db.kv_put:
            state[key] = json.loads(args[0])
    ctx = SimpleNamespace(db=SimpleNamespace(run=data))
    def accepted(model, body):
        posts.append(model)
        return {"request_id": "existing-job"}, None
    monkeypatch.setattr(videogen, "submit_request", accepted)
    for _ in range(2):
        sub, err = videorestyle._submit_once(ctx, "shot", "mock", {})
        assert err is None and sub["request_id"] == "existing-job"
    assert posts == ["mock"]
    monkeypatch.setattr(videogen, "submit_request", lambda *a: (None, "connection closed before acknowledgement"))
    assert videorestyle._submit_once(ctx, "uncertain", "mock", {})[0] is None
    monkeypatch.setattr(videogen, "submit_request", accepted)
    assert videorestyle._submit_once(ctx, "uncertain", "mock", {})[0] is None
    assert posts == ["mock"]
