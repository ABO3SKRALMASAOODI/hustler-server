"""Wave 4 independent review: the verbs' solving logic (ramp, held frames),
the aspect verifier's faces lens, cutaway windows, the critic's B&W fix and
fresh ideas, the interpolate rung and the streamed slot footage.

Pure python + ffmpeg (one tiny lavfi clip); CI-safe.
"""

import copy
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# speech 0.5-5.5 s, a pause 5.5-9.0 s, speech 9.0-11.5 s (source = program)
_WORDS = []
for k in range(10):
    _WORDS.append({"w": f"w{k}", "t0": 0.5 + 0.5 * k,
                   "t1": 0.5 + 0.5 * k + 0.4})
_WORDS[4]["w"] = "growth"
_WORDS[9]["w"] = "decades."
for k in range(5):
    _WORDS.append({"w": f"v{k}", "t0": 9.0 + 0.5 * k,
                   "t1": 9.0 + 0.5 * k + 0.4})
_WORDS[-1]["w"] = "end."
IDX = {"words": _WORDS, "silences": [], "sentences": [],
       "video": {"width": 1920, "height": 1080, "fps": 25.0,
                 "duration": 14.0}}


def _edl():
    from schemas import validate_edl
    return validate_edl({"keep": [[0.0, 12.0]],
                         "frame": {"ratio": "9:16", "mode": "crop"}},
                        14.0).model_dump()


class FakeDB:
    def run(self, fn, *a, **kw):
        import edit_batch
        if fn is edit_batch.project_media_keys:
            return {}
        raise AssertionError(f"unexpected db call {fn}")


class Ctx:
    def __init__(self, edl):
        self.db = FakeDB()
        self.project_id = 7
        self.duration = 14.0
        self.has_main_video = True
        self._edl = edl
        self.version = 1
        self.index = IDX
        self.user_message = None

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self._edl)}

    def write_edl(self, edl, desc):
        self._edl = edl
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


@pytest.fixture
def ctx(monkeypatch):
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: True)
    from tools_ext import depth_tools
    monkeypatch.setattr(depth_tools, "_ensure_depth", lambda *a, **k: "")
    return Ctx(_edl())


# ── the ramp ─────────────────────────────────────────────────────────────

def test_a_ramp_over_speech_is_refused_it_would_slow_the_voice(ctx):
    from tools_ext import depth_tools
    out = depth_tools.add_time_effect(ctx, "ramp", start=1.0, end=3.0,
                                      factor=0.4)
    assert out.startswith("REJECTED") and "voice" in out
    assert not ctx._edl.get("speed")
    ok = depth_tools.add_time_effect(ctx, "ramp", start=1.0, end=3.0,
                                     factor=0.4, allow_speech=True)
    assert ok.startswith("EDL "), ok


def test_a_ramp_in_a_pause_writes_valid_spans_and_reanchors_later_items(ctx):
    from tools_ext import depth_tools
    ctx._edl["fx"] = [{"id": "late", "type": "time", "stage": "world",
                       "start": 10.0, "end": 11.0, "kind": "echo",
                       "device": "time_echo"}]
    out = depth_tools.add_time_effect(ctx, "ramp", start=6.0, end=8.6,
                                      factor=0.4)
    assert out.startswith("EDL "), out
    sp = ctx._edl["speed"]
    assert sp and all(s["end"] - s["start"] >= 0.2 for s in sp)
    assert all(0.25 <= s["factor"] < 0.98 for s in sp)
    late = next(i for i in ctx._edl["fx"] if i["id"] == "late")
    # the slowed pause adds program time: the later echo moved with its
    # footage instead of drifting onto other frames
    assert late["start"] > 10.5, late
    interp = [i for i in ctx._edl["fx"] if i.get("kind") == "interpolate"]
    assert len(interp) == 1
    # a second ramp gets its own ids (the first used ramp1_*)
    out = depth_tools.add_time_effect(ctx, "ramp", start=0.0, end=0.3,
                                      factor=0.5)
    assert out.startswith("REJECTED")          # < 0.4 s of footage
    ids = [s["id"] for s in ctx._edl["speed"]]
    assert len(ids) == len(set(ids))


def test_ramp_factors_outside_the_core_range_are_refused(ctx):
    from tools_ext import depth_tools
    for f in (0.1, 0.95):
        out = depth_tools.add_time_effect(ctx, "ramp", start=6.0, end=8.6,
                                          factor=f)
        assert out.startswith("REJECTED") and "0.25" in out, out


def test_two_ramps_never_collide_on_ids(ctx):
    from tools_ext import depth_tools
    a = depth_tools.add_time_effect(ctx, "ramp", start=5.6, end=6.8,
                                    factor=0.5)
    assert a.startswith("EDL "), a
    prog = depth_tools._program_s(ctx._edl)
    b = depth_tools.add_time_effect(ctx, "ramp", start=prog - 1.4,
                                    end=prog - 0.2, factor=0.5,
                                    allow_speech=True)
    assert b.startswith("EDL "), b
    ids = [s["id"] for s in ctx._edl["speed"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 2
    fx_ids = [i["id"] for i in ctx._edl["fx"]]
    assert len(fx_ids) == len(set(fx_ids))
    assert sum(1 for i in ctx._edl["fx"] if i["kind"] == "interpolate") == 2


# ── held frames (payoff holds) ─────────────────────────────────────────────

def test_a_payoff_hold_starts_as_the_word_ends_and_never_over_speech(ctx):
    from tools_ext import depth_tools
    # 'growth' is followed by more words: a hold there freezes the lips
    out = depth_tools.add_depth_effect(ctx, "parallax", word="growth")
    assert out.startswith("REJECTED") and "lips" in out, out
    # 'decades.' ends the thought; a 3.5 s pause follows
    out = depth_tools.add_depth_effect(ctx, "parallax", word="decades")
    assert out.startswith("EDL "), out
    it = ctx._edl["fx"][-1]
    assert it["start"] >= 5.4 - 1e-6           # 'decades.' ends at 5.4 s
    assert it["end"] <= 9.0 - 0.03             # stops before the next word
    assert it["bind"]["on"] == "phrase_end"
    # a push is a zoom: a held frame of the speaker travels sideways/up
    assert it["move"]["path"] in depth_tools.HOLD_MOVES
    assert it["move"]["path"] != "push_in"


def test_an_explicit_hold_over_words_needs_allow_speech(ctx):
    from tools_ext import depth_tools
    out = depth_tools.add_depth_effect(ctx, "ken_burns", start=1.0, end=3.0)
    assert out.startswith("REJECTED")
    out = depth_tools.add_depth_effect(ctx, "ken_burns", start=1.0, end=3.0,
                                       allow_speech=True)
    assert out.startswith("EDL "), out


def test_consecutive_holds_take_different_paths(ctx):
    from tools_ext import depth_tools
    a = depth_tools.add_depth_effect(ctx, "parallax", start=5.6, end=6.8)
    b = depth_tools.add_depth_effect(ctx, "parallax", start=7.2, end=8.6)
    assert a.startswith("EDL ") and b.startswith("EDL "), (a, b)
    paths = [i["move"]["path"] for i in ctx._edl["fx"]]
    assert len(set(paths)) == 2


def test_a_cutaway_still_may_sit_over_speech(ctx, monkeypatch):
    from tools_ext import depth_tools
    import edit_batch
    monkeypatch.setattr(ctx.db, "run", lambda fn, *a, **k: (
        {"media/7/p.jpg": {"storage_key": "media/7/p.jpg"}}
        if fn is edit_batch.project_media_keys else None))
    out = depth_tools.add_depth_effect(ctx, "parallax", start=1.0, end=3.0,
                                       asset_key="media/7/p.jpg")
    assert out.startswith("EDL "), out
    assert ctx._edl["fx"][-1]["move"]["path"] == "push_in"


# ── delivery: the faces lens really runs on a master ─────────────────────

def test_aspect_verify_counts_type_on_faces(monkeypatch, tmp_path):
    import aspects
    import motion_critic
    import qa_metrics
    monkeypatch.setattr(motion_critic, "critique",
                        lambda *a, **k: {"findings": [], "score": 9.0,
                                         "measures": {}})
    monkeypatch.setattr(motion_critic, "lens_faces", lambda p, e, i: [
        {"code": "type_on_face", "lens": "faces", "t": 1.0,
         "severity": "fix", "message": "m1 on the face"}])
    monkeypatch.setattr(qa_metrics, "probe", lambda p: {
        "width": 1080, "height": 1920, "fps": 30.0, "duration": 4.0})
    monkeypatch.setattr(aspects, "safe_area_violations",
                        lambda *a, **k: [])
    got = aspects.verify(str(tmp_path / "x.mp4"), {}, {}, "9:16")
    assert got["faces"] == 1


# ── cutaways: windows already taken ──────────────────────────────────────

def test_cover_overlays_block_a_new_cutaway_by_their_duration():
    from tools_ext import broll_tools
    edl = {"keep": [[0.0, 12.0]], "fx": [],
           "overlays": [{"id": "o1", "start": 4.0, "duration_s": 2.5,
                         "fit": "cover"}]}
    taken = broll_tools._windows_taken(edl)
    assert (4.0, 6.5) in taken


# ── the critic ───────────────────────────────────────────────────────────

def test_the_bw_fix_is_a_section_not_another_look():
    import motion_critic
    edl = {"keep": [[0.0, 30.0]], "effects": {"grade": "bw"}}
    _why, ops = motion_critic._bw_cause(edl, {"t": 27.0})
    gw = next(o for o in ops if o.get("layer") == "fx")["value"]
    share = (gw["end"] - gw["start"]) / 30.0
    assert gw["end"] == 27.0 and share < motion_critic.BW_LOOK_SHARE
    edl = {"keep": [[0.0, 30.0]], "fx": [{
        "id": "g1", "type": "grade_window", "stage": "world", "start": 0.0,
        "end": 27.0, "saturation": 0.0}]}
    _why, ops = motion_critic._bw_cause(edl, {"t": 27.0})
    v = ops[0]["value"]
    assert (v["end"] - v["start"]) / 30.0 < motion_critic.BW_LOOK_SHARE


def test_fresh_ideas_skip_devices_the_run_already_used():
    import motion_critic
    plan = {"moments": [{"t": 3.0, "kind": "stress", "strength": 0.9,
                         "tags": ["contrast"], "word": "but",
                         "word_raw": "but"}], "payoff": None}
    edl = {"keep": [[0.0, 10.0]]}
    _o, first = motion_critic.lens_range(edl, plan, [])
    assert first["ideas"][0]["verb"] == "add_depth_effect"
    prev = [{"keep": [[0.0, 10.0]], "fx": [{
        "id": "r", "type": "depth", "stage": "world", "start": 1.0,
        "end": 2.0, "kind": "rack_focus", "device": "depth_rack_focus"}]}]
    _o, again = motion_critic.lens_range(edl, plan, prev)
    assert again["ideas"][0]["verb"] != "add_depth_effect"
    assert again["ideas"][0]["fresh_for_run"]


# ── time: native frames are not a degrade ───────────────────────────────

def test_interpolate_on_native_frames_is_full_not_reduced():
    import timewarp

    class Src:
        fps = 60.0

    from timeline import Timeline

    class Plan:
        fps = 30.0
        fps3 = 30.0
        source = Src()
        tl = Timeline([[0.0, 4.0]], [], [{"id": "s", "start": 0.0,
                                          "end": 2.0, "factor": 0.5}])

    got = timewarp.TIME.prepare(Plan(), {"id": "i1", "kind": "interpolate",
                                         "start": 0.5, "end": 3.0})
    assert got[0] == "full" and "native" in got[1]


# ── slot footage is streamed ─────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_slot_footage_streams_frame_by_frame(tmp_path):
    import ui_media
    clip = str(tmp_path / "c.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:r=25:d=1", "-pix_fmt", "yuv420p",
                    clip], check=True)
    fr = ui_media._Frames(clip, 0.0, 40, 25.0, w=160)
    assert fr.frames                            # readable
    a = fr.at(0).copy()
    b = fr.at(10).copy()
    assert a.shape == (90, 160, 3) and (a != b).any()
    last = fr.at(39)                            # past the end: held
    assert last is not None and last.shape == (90, 160, 3)
    fr.close()


# ── catalog shares (INTERFACES_W4.md §1: tested) ─────────────────────────

def test_wave4_department_shares_stay_within_their_pins():
    import json
    import agent_tools
    from tools_ext import broll_tools, critic_tools, depth_tools, ui_tools

    def size(names):
        return len(json.dumps(agent_tools.openai_tools(
            "x", compact=True, names=list(names)), separators=(",", ":")))
    world = list(depth_tools.VERBS) + list(ui_tools.VERBS) + \
        list(broll_tools.VERBS)
    # their own department since the engines release ("picture"); the
    # share pin stands
    assert size(world) <= 5_000
    assert set(world) == set(agent_tools.TOOL_DOMAINS["picture"])
    assert size(critic_tools.VERBS) <= 1_200
    for name in world + list(critic_tools.VERBS):
        assert len(agent_tools.TOOLS[name][1]) <= 900, name


# ── parallax: no seam through a continuous surface ───────────────────────

def test_a_2_5d_move_never_tears_a_continuous_surface():
    """A face is a continuous depth ramp: depth slices split it along a
    line (Wave 4 review: a seam down the speaker's forehead on the payoff
    hold). The two-plane move warps everything nearer continuously: a
    luma ramp on a depth ramp stays a monotonic ramp (the slices folded
    it: steps of -3 to -8 levels, the far wall showing between slices)."""
    import numpy as np
    import depth_fx
    W, H = 480, 640
    xs = np.linspace(0, 1, W, dtype=np.float32)
    img = np.dstack([np.tile((60 + 180 * xs)[None], (H, 1))] * 3).astype(
        np.uint8)
    depth = np.tile((0.35 + 0.6 * xs)[None], (H, 1)).astype(np.float32)
    depth[:, : W // 5] = 0.05                      # a far, dark wall
    img[:, : W // 5] = 20

    class Grab:
        def __init__(self):
            self.f = []

        def write(self, x):
            self.f.append(np.asarray(x, np.float32))
    for path in ("drift_left", "orbit_left", "push_in"):
        move = {"path": path, "amount": 1.0, "ease": "linear"}
        planes = depth_fx.two_planes(img, depth)
        g = Grab()
        depth_fx.render_parallax_planes(img, planes, W, H, 3, move, g)
        row = g.f[2][H // 2, :, 0]
        steps = np.diff(row[W // 3: W - 12])
        assert steps.min() >= -0.5 and steps.max() <= 2.0, (path, steps.min(),
                                                           steps.max())
        assert depth_fx.visible_inpaint_planes(planes, W, H, move, 3) < 0.2


# ── audio-only rebuilds draw no W4 picture ───────────────────────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_an_audio_only_rebuild_precomputes_no_w4_picture(tmp_path,
                                                         monkeypatch):
    """Stitched previews rebuild the track with render_edl(audio_only):
    the picture chains are pruned, so the depth / cutaway / slot
    precompute (clips, maps, clouds: seconds per device) must not run."""
    import depth_fx
    import media
    import renderer
    import w4a_support as sup
    src = sup.make_split_source(str(tmp_path / "s.mp4"), dur=4.0)
    still = sup.make_split_still(str(tmp_path / "p.png"))
    info = media.probe(src)
    idx = {"words": [], "silences": [], "sentences": [],
           "video": {"width": info["width"], "height": info["height"],
                     "fps": info["fps"], "duration": info["duration"]}}
    edl = {"keep": [[0.0, 4.0]], "captions": {"enabled": False}, "fx": [
        {"id": "p", "type": "depth", "stage": "screen", "start": 0.5,
         "end": 1.5, "kind": "parallax", "source": {
             "kind": "asset", "asset_key": "media/7/p.png"}},
        {"id": "k", "type": "depth", "stage": "world", "start": 1.6,
         "end": 2.6, "kind": "ken_burns", "source": {
             "kind": "source", "src_t": 1.0}},
        {"id": "b", "type": "broll", "stage": "screen", "start": 2.7,
         "end": 3.5, "media": "still", "source": {
             "kind": "asset", "asset_key": "media/7/p.png"},
         "credit": {"provider": "owner", "license": "Public domain"}}]}
    written = []
    real = depth_fx.ClipWriter.__init__

    def spy(self, *a, **k):
        written.append(a[0] if a else k.get("path"))
        return real(self, *a, **k)
    monkeypatch.setattr(depth_fx.ClipWriter, "__init__", spy)
    from schemas import validate_edl
    e = validate_edl(edl, 4.0).model_dump()
    out = str(tmp_path / "a.m4a")
    renderer.render_edl(e, idx, src, out, str(tmp_path / "w"), True,
                        audio_only=True, asset_locals={"media/7/p.png":
                                                       still})
    assert os.path.exists(out) and os.path.getsize(out) > 1000
    assert written == []
    vid = str(tmp_path / "v.mp4")
    renderer.render_edl(e, idx, src, vid, str(tmp_path / "w2"), True,
                        asset_locals={"media/7/p.png": still})
    assert len(written) == 3                      # the picture render draws


# ── UI scenes and the captions ───────────────────────────────────────────

def _three_scene_graph():
    import ui_scene
    scenes = [
        {"at": 0.0, "layout": "stack", "objects": [
            {"id": "pill", "kind": "pill", "label": "Growth engine",
             "state": "idle"},
            {"id": "net", "kind": "card", "title": "The internet",
             "body": "a growth driver"}]},
        {"at": 4.0, "layout": "stack", "objects": [
            {"id": "pill", "kind": "pill", "label": "Growth engine",
             "state": "live"},
            {"id": "ai", "kind": "card", "title": "AI",
             "body": "bigger than the internet", "from": "net"}]}]
    return ui_scene.solve(scenes, 8.0, 1080, 1920)


def test_only_text_on_screen_for_the_whole_scene_counts_as_shown():
    """The caption plan reads params.shown as on screen for the item's
    whole window: a passing card listed there hid heard words the screen
    was not showing (V01: 'It was bigger than anything else' muted at 6 s
    by a card that appears at 14 s)."""
    import ui_scene
    g = _three_scene_graph()
    shown = ui_scene.caption_shown(g)
    assert "Growth engine" in shown
    assert "bigger than the internet" not in shown
    assert "The internet" not in shown
    assert "bigger than the internet" in ui_scene.shown_texts(g)


def test_a_ui_scene_carries_its_cards_box_not_the_backdrop():
    """A probe measures a solid backdrop as a full-frame cover and the
    caption plan then mutes every word under the scene: the item carries
    the union of its cards instead (fresh at this aspect, not estimated)."""
    import caption_carry
    import ui_scene
    g = _three_scene_graph()
    fp = ui_scene.footprint(g)
    box = caption_carry.footprint_box({"footprint": fp},
                                      caption_carry.frame_ar(1080, 1920))
    assert box is not None and not fp.get("estimated")
    assert box[3] < 0.8 and box[1] > 0.05      # the caption band is free


def test_critique_short_takes_the_pack_it_judges_against(monkeypatch):
    """A short built on a pack (apply_format_pack E/F/...) was judged against a
    talking head's rhythm band: the verb now takes the pack."""
    from tools_ext import critic_tools
    import motion_critic
    out = critic_tools.critique_short(Ctx(_edl()), format="Z")
    assert out.startswith("REJECTED")
    seen = {}
    monkeypatch.setattr(motion_critic, "critique",
                        lambda *a, **k: seen.update(k) or {"findings": []})
    monkeypatch.setattr(motion_critic, "critique_text", lambda r: "CRITIC")

    class RCtx(Ctx):
        workdir = "/tmp"
        pending_images = []
    import storage
    monkeypatch.setattr(storage, "download_to", lambda k, p: None)
    monkeypatch.setattr(critic_tools, "_render_row", lambda *a: {
        "storage_key": "r/1.mp4", "meta": {"variant": "preview"}})
    ctx = RCtx(_edl())
    ctx.db = type("D", (), {"run": lambda self, fn, *a, **k: []})()
    out = critic_tools.critique_short(ctx, format="e")
    assert "CRITIC" in out and seen.get("fmt") == "E"


def test_the_critic_reads_the_ending_and_a_single_span_opening():
    """lens_words judged the opening only for multi-span edits and never
    read the ending: a short cut inside its last word ends on a
    fragment before the end card."""
    import motion_critic
    words = [{"w": "so", "t0": 0.0, "t1": 0.3},
             {"w": "this", "t0": 0.32, "t1": 0.6},
             {"w": "matters.", "t0": 0.62, "t1": 1.2},
             {"w": "Yeah,", "t0": 4.0, "t1": 4.6}]
    edl = {"keep": [[0.31, 4.3]]}
    got = motion_critic.lens_words(edl, {"words": words}, {"words": []},
                                   3.9)
    codes = {f["code"]: f for f in got}
    assert "ends_mid_word" in codes and "opens_mid_sentence" in codes
    op = codes["ends_mid_word"]["op"]
    assert op["value"] == [[0.31, 3.98]]
