import subprocess

import agent_tools
import customer_craft as craft
import media
import renderer
import stitch
from harness2 import finish
from timeline import Timeline
from w0c_support import tool_context


def test_direct_on_current_cut_is_editable_and_retryable():
    ctx, _ = tool_context({"keep": [[10., 18.]]}, duration=25.)
    assert set(craft.TOOLS) <= agent_tools.compact_tool_names(ctx)
    beat = dict(id="turn", device="rgb_split", start=2, end=3)
    craft.direct(ctx, [beat])
    first = ctx.latest_edl()
    assert first["json"]["direction"][0]["start"] == 2
    craft.direct(ctx, [beat])
    assert ctx.latest_edl()["version"] == first["version"]
    craft.direct(ctx, [dict(beat, end=3.5)])
    assert len(ctx.latest_edl()["json"]["direction"]) == 1
    assert finish.events(first["json"])[0]["f"] == 60
    cut = stitch.window_edl(first["json"], Timeline([[10., 18.]]), 1, 5)
    assert cut["direction"][0]["start"] == 1
    craft.direct(ctx, [dict(id="turn", remove=True)])
    assert not ctx.latest_edl()["json"]["direction"]


def test_dress_sets_spoken_words_and_captions_together():
    ctx, _ = tool_context({"keep": [[0., 5.]]}, duration=5.)
    ctx.index["words"] = [{"w": "hello", "t0": 1.0, "t1": 1.5}]
    res = craft.dress(ctx, [{"id": "hello", "text": "Hello", "start": 1., "end": 2.}], True)
    edl = ctx.latest_edl()["json"]
    assert edl["captions"]["mode"] == "from_transcript", res
    assert any(m["id"] == "hello" for m in edl["motion"]), res


def test_palette_device_survives_normal_movie_render(tmp_path, monkeypatch):
    import cv2
    import numpy as np
    ff = "ffmpeg"
    src = str(tmp_path/"source.mp4")
    subprocess.run([ff, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=30:duration=2", "-f","lavfi","-i",
                    "sine=frequency=440:duration=2","-c:v","libx264","-pix_fmt","yuv420p",
                    "-c:a","aac",src], check=True)
    monkeypatch.setattr(renderer, "outro_seconds", lambda *_: 0)
    def frames(edl, name):
        folder = tmp_path/name
        folder.mkdir()
        out = str(folder/"result.mp4")
        renderer.render_edl(edl, {}, src, out, str(folder), True, suppress_outro=True)
        assert media.probe(out)["has_audio"]
        cap = cv2.VideoCapture(out)
        samples = []
        for ms in [250, 800, 1600]:
            cap.set(cv2.CAP_PROP_POS_MSEC, ms)
            ok, frame = cap.read()
            assert ok
            samples.append(frame.astype(float))
        cap.release()
        return samples
    base = {"keep": [[0, 2]], "frame": {"ratio": "source"}}
    original = frames(base, "base")
    directed = frames(dict(base, direction=[dict(id="hit",device="rgb_split",start=.7,end=1.3)]), "directed")
    delta = [np.mean(abs(a-b)) for a,b in zip(original,directed)]
    assert delta[1] > max(delta[0],delta[2]) + 1, delta


def test_score_uses_the_workflow_pool_and_directed_turn(monkeypatch):
    import music_choose
    from harness2 import commit, stage
    ctx, _ = tool_context({"keep": [[0., 5.]], "direction": [
        {"id": "reveal", "device": "rgb_split", "start": 2., "end": 2.5, "role": "turn"}]}, duration=5.)
    head = ctx.latest_edl()
    staged = stage.Stage(ctx, head["json"], head["version"])
    seen = []
    monkeypatch.setattr(music_choose, "workflow_pools", lambda film: [("set", ["set-track"]), ("library", ["backup"])])
    def choose(film, **kw):
        seen.append((film["turns"], kw["pool"]))
        return {"cid": kw["pool"][0]}
    monkeypatch.setattr(music_choose, "choose", choose)
    monkeypatch.setattr(music_choose, "ensure_project_asset", lambda ctx, choice: (
        ("music/1/backup.mp3", None) if choice["cid"] == "backup" else (None, "unavailable")))
    monkeypatch.setattr(music_choose, "find", lambda *_: {"title": "Backup", "author": "A", "license": "CC0-1.0"})
    assert finish._music(staged, {"workflow_pool": True}, commit.EditEnv(), 0, lambda *_: True)
    assert seen == [([{"t": 2., "role": "turn"}], ["set-track"]), ([{"t": 2., "role": "turn"}], ["backup"])]


def test_editor_reviews_its_own_frames(monkeypatch):
    ctx, _ = tool_context({"keep": [[0., 5.]]}, duration=5.)
    ctx.direct_sight, ctx.agent_model = True, "claude-opus-5-5"
    assert agent_tools._preview_critic_report(ctx, {}, []) is None
    assert agent_tools._preview_fallback_check(ctx, {}, [], False, None) is None
