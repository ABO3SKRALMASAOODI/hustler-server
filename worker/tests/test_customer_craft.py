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
    assert set(craft.TOOLS) <= agent_tools.TOOL_DOMAINS["direction"]
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


# ---- round 2 (26 Nov): the plan is made and applied before the editor's first step ----

PLAN = {"story": "Opens on the jump, turns on the fall, pays off on the laugh.", "cut": "new",
        "keep": [[2., 6.], [10., 14.]],
        "beats": [{"role": "hook", "start": 2.1, "end": 2.3, "what": "the jump", "device": "rgb_split", "text": "", "color": ""},
                  {"role": "payoff", "start": 12., "end": 12.4, "what": "the laugh", "device": "white", "text": "", "color": ""},
                  {"role": "turn", "start": 7., "end": 8., "what": "cut away", "device": "neg", "text": "", "color": ""}],
        "words": [{"text": "Hello", "start": 3., "end": 3.8}],
        "captions": "on", "music": True, "sounds": False, "sound_brief": "bright lift on the payoff"}


def _planned(monkeypatch, value):
    from score import asks
    calls, scored = [], []
    monkeypatch.setattr(asks, "_call", lambda ctx, name, schema, system, content, **kw: (
        calls.append((name, kw.get("model"), content)) or (value, {"usd": 0.0})))
    monkeypatch.setattr(craft, "score", lambda ctx, **kw: scored.append(kw) or "EDL v9 -> v10: music")
    return calls, scored


def test_plan_first_applies_cut_beats_words_and_score_on_the_new_cut(monkeypatch):
    ctx, _ = tool_context({"keep": [[0., 20.]]}, duration=20.)
    ctx.agent_model = "claude-opus-5-5"
    ctx.index["words"] = [{"w": "hello", "t0": 3.0, "t1": 3.5}, {"w": "there", "t0": 12.0, "t1": 12.4}]
    calls, scored = _planned(monkeypatch, PLAN)
    seen = []
    note = craft.plan_first(ctx, {"asks": [{"said": "make it fire", "want": "a reel"}]}, "make it fire",
                            record=lambda name, args, res: seen.append(name))
    edl = ctx.latest_edl()["json"]
    assert calls[0][:2] == ("record_piece_plan", "claude-opus-5-5")
    assert edl["keep"] == [[2., 6.], [10., 14.]]
    beats = {m["id"]: m for m in edl["direction"]}
    assert abs(beats["hook-1"]["start"] - 0.1) < 1e-6          # source 2.1 s is film 0.1 s on the new cut
    assert abs(beats["payoff-2"]["start"] - 6.0) < 1e-6        # source 12 s is film 6 s
    assert "turn-3" not in beats and "fell outside the cut" in note
    assert any(m.get("id") == "plan-word-1" for m in edl["motion"])
    assert edl["captions"]["mode"] == "from_transcript"
    assert scored == [{"brief": "bright lift on the payoff", "music": True, "sounds": False}]
    assert seen == ["plan_piece", "direct", "dress", "score"]
    assert "PIECE PLAN" in note and "Opens on the jump" in note


def test_plan_keeps_the_current_cut_when_the_story_is_already_there(monkeypatch):
    ctx, _ = tool_context({"keep": [[0., 10.]]}, duration=10.)
    _planned(monkeypatch, dict(PLAN, cut="current", keep=[[0., 1.]], words=[], captions="as_is",
                               music=False, sounds=False, beats=PLAN["beats"][:1]))
    craft.plan_first(ctx, {"asks": []}, "add effects")
    edl = ctx.latest_edl()["json"]
    assert edl["keep"] == [[0., 10.]] and edl["direction"][0]["start"] == 2.1


def test_a_plan_that_does_not_answer_leaves_the_editor_to_plan(monkeypatch):
    from score import asks
    ctx, _ = tool_context({"keep": [[0., 10.]]}, duration=10.)
    before = ctx.latest_edl()["version"]
    def fail(*a, **k):
        raise ValueError("record_piece_plan: no structured answer")
    monkeypatch.setattr(asks, "_call", fail)
    note = craft.plan_first(ctx, {"asks": []}, "make it better")
    assert ctx.latest_edl()["version"] == before
    assert "did not answer" in note and "plan_piece" in note


def test_only_a_piece_is_planned_and_the_ledger_carries_the_reading(monkeypatch):
    import agent_loop
    from score import asks
    ctx, _ = tool_context({"keep": [[0., 10.]]}, duration=10.)
    ran = []
    monkeypatch.setattr(craft, "plan_first", lambda *a, **k: ran.append(1) or "planned")
    plain = asks.settle({"lang": "en", "piece": "plain", "asks": [{"kind": "edit", "said": "cut the silences"}]},
                        ctx.latest_edl()["json"])
    assert plain["piece"] == "plain"
    assert agent_loop._plan_piece_first(ctx, None, {"id": 1}, 1, plain, "cut the silences") == ""
    made = asks.settle({"lang": "en", "piece": "made", "asks": [{"kind": "film", "said": "make a reel"}]},
                       ctx.latest_edl()["json"])
    monkeypatch.setattr(agent_loop, "_activity", lambda *a, **k: None)
    assert agent_loop._plan_piece_first(ctx, None, {"id": 1}, 1, made, "make a reel") == "planned"
    assert ran == [1]
    assert set(craft.TOOLS) <= agent_tools.compact_tool_names(ctx)       # the editor refines with them
    assert "piece" in asks.LEDGER_SCHEMA["required"]


def test_the_editor_reads_each_craft_tools_whole_contract():
    assert not set(craft.TOOLS) & agent_tools.planning_tool_names()     # a plain request never pays for them
    tools = agent_tools.openai_tools("claude-opus-5-5", compact=True, names=set(craft.TOOLS))
    got = {t["function"]["name"]: t["function"]["description"] for t in tools}
    for name in craft.TOOLS:
        assert got[name] == craft.COMPACT[name] and not got[name].endswith("...")
    from scene import palette
    listed = [line.split("] ", 1)[1].split(" ", 1)[0] for line in craft.piece_palette().splitlines()]
    assert "rgb_split" in listed and "rgb_split" in craft.piece_system()
    assert all(not craft.FOOTAGE_ONLY & set(palette.DEVICES[n].get("needs") or ()) for n in listed)
    assert any(craft.FOOTAGE_ONLY & set(e.get("needs") or ()) for e in palette.DEVICES.values())


def test_plan_evidence_reads_real_frames_words_shots_and_sound(tmp_path, monkeypatch):
    import llm
    src = str(tmp_path / "source.mp4")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=6", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", src], check=True)
    ctx, _ = tool_context({"keep": [[0., 6.]]}, duration=6.)
    ctx.workdir = str(tmp_path)
    monkeypatch.setattr(type(ctx), "proxy_path", lambda self: src, raising=False)
    ctx.index.update(video={"duration": 6.0, "width": 640, "height": 360},
                     words=[{"w": "watch", "t0": 1.0, "t1": 1.3}, {"w": "this", "t0": 1.3, "t1": 1.6},
                            {"w": "jump", "t0": 3.0, "t1": 3.4}],
                     shots=[{"start": 0.0, "end": 6.0, "caption": {"text": "a test card"}}])
    content, on_source = craft.evidence(ctx, ctx.latest_edl()["json"], {"asks": [{"said": "make it fire", "want": "a reel"}]},
                                        "make it fire")
    text = content[0]["text"]
    assert on_source and "[1.00-1.60] watch this" in text and "[3.00-3.40] jump" in text
    assert "[0.0-6.0] a test card" in text and "CLOCK: source seconds" in text
    images = [part for part in content if part.get("type") == "image_url"]
    assert len(images) == 1 and images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    from PIL import Image
    import base64, io
    sheet = Image.open(io.BytesIO(base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1])))
    assert max(sheet.size) <= 1280                       # low resolution: one small sheet, not eight frames


def test_a_failing_step_is_named_and_the_others_still_apply(monkeypatch):
    ctx, _ = tool_context({"keep": [[0., 20.]]}, duration=20.)
    _planned(monkeypatch, PLAN)
    def boom(ctx, **kw):
        raise RuntimeError("music library unreachable")
    monkeypatch.setattr(craft, "score", boom)
    seen = []
    note = craft.plan_first(ctx, {"asks": []}, "make a promo", record=lambda n, a, r: seen.append((n, str(r)[:9])))
    edl = ctx.latest_edl()["json"]
    assert edl["keep"] == [[2., 6.], [10., 14.]] and edl["direction"]
    assert seen[-1] == ("score", "REJECTED:") and "Not applied:" in note and "music library unreachable" in note
