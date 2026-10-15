"""Wave 2 track 2D: montage, arrangement, ramps, moving stills
(INTERFACES_W2.md §5). Synthetic rhythm maps and test signals only; the
owner's track and the V13 pool are measured by the dev harness
(WAVE2_RELEASE.md, AT 2D-1..2D-7)."""

import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import w2_support as S                                    # noqa: E402
import agent_tools                                        # noqa: E402,F401
import arrangement                                        # noqa: E402
import hooks                                              # noqa: E402
import library                                            # noqa: E402
import montage                                            # noqa: E402
import ramps                                              # noqa: E402
import schemas                                            # noqa: E402
from tools_ext import montage_tools as MT                 # noqa: E402


# ── a program-clock rhythm map with a known kick grid ───────────────────

def rmap(bpm=128.0, n=40, t0=0.2, kicks_every=1, intro=8, drop_at=24,
         strobe=False):
    per = 60.0 / bpm
    beats = [round(t0 + k * per, 4) for k in range(n)]
    kicks = [b + 0.004 for k, b in enumerate(beats)
             if k >= intro and k % kicks_every == 0]
    return {"version": 1, "source": "music", "clock": "media", "ref": {},
            "producer": "test@1", "bpm": bpm, "beats": beats,
            "downbeats": beats[::4],
            "onsets": [{"t": k, "cls": "kick", "strength": 0.9}
                       for k in kicks],
            "sections": [{"t0": beats[0], "t1": beats[intro],
                          "label": "intro", "energy": 0.3},
                         {"t0": beats[intro], "t1": beats[drop_at],
                          "label": "verse", "energy": 0.6},
                         {"t0": beats[drop_at], "t1": beats[-1],
                          "label": "drop", "energy": 0.9}],
            "drops": [beats[drop_at]], "gaps": [], "confidence": 0.9,
            "downbeat_confidence": 0.9, "duration": beats[-1] + 1.0}


def pool(n=9, dur=12.0, image=False):
    out = [{"storage_key": f"clips/c{k}.mp4", "kind": "video_clip",
            "duration_s": dur} for k in range(n)]
    if image:
        out.append({"storage_key": "img/a.jpg", "kind": "image_ref",
                    "duration_s": None})
    return out


def ctx_with(monkeypatch, rows, m=None, msg="", **edl_extra):
    keys = {r["storage_key"]: r for r in rows}
    keys["music/t.mp3"] = {"storage_key": "music/t.mp3", "duration_s": 60}
    e = S.edl(music=[{"id": "m1", "storage_key": "music/t.mp3", "start": 0.0,
                      "end": 20.0, "provenance": "user_upload"}],
              **edl_extra)
    ctx = S.Ctx(e, keys=keys)
    ctx.user_message = msg
    monkeypatch.setattr(MT, "_project_assets", lambda c, kinds: rows)
    monkeypatch.setattr(MT, "_library", lambda c, keys: {})
    MT._MAPS["music/t.mp3"] = m or rmap()
    return ctx


# ── slots: one frame before the kick, modes switch on the section line ──

def test_every_cut_opens_one_frame_before_its_kick():
    m = rmap()
    fps = 25.0
    sl = montage.slots(m, 0.0, 18.0, fps=fps)
    kick_f = {montage.frame(o["t"], fps) for o in m["onsets"]}
    pulses = [s for s in sl if s["mode"] == "pulse"]
    assert pulses and all(s["cut_f"] + 1 in kick_f for s in pulses)
    # the centisecond start opens exactly that frame under gte(t, start)
    for s in sl:
        k = s["cut_f"]
        assert (k - 1) / fps < s["start"] <= k / fps + 1e-9 or k == 0


@pytest.mark.parametrize("fps", [23.976, 25.0, 29.97, 30.0, 50.0, 59.94])
def test_ftime_opens_exactly_its_frame(fps):
    for k in range(1, 400):
        t = montage.ftime(k, fps)
        assert (k - 1) / fps < t < k / fps + 1e-12, (k, t)
        assert round(t, 2) == t


def test_lead_is_zero_to_two_frames():
    m = rmap()
    for lead in (0, 1, 2):
        sl = montage.slots(m, 0.0, 18.0, fps=30.0, lead_f=lead)
        kick_f = {montage.frame(o["t"], 30.0) for o in m["onsets"]}
        assert all(s["cut_f"] + lead in kick_f for s in sl
                   if s["mode"] == "pulse")


def test_holds_last_and_modes_switch_on_the_section_line():
    m = rmap(intro=12)
    fps = 25.0
    sl = montage.slots(m, 0.0, 18.0, fps=fps,
                       modes={"intro": "hold", "verse": "strobe"})
    holds = [s for s in sl if s["mode"] == "hold"]
    assert holds and all(s["end"] - s["start"] >= montage.HOLD_MIN_S - 0.05
                         for s in holds)
    first_strobe = next(s for s in sl if s["mode"] == "strobe")
    line = m["sections"][1]["t0"]
    assert abs(first_strobe["cut_f"] - montage.frame(line, fps)) <= 1


def test_a_section_without_kicks_cuts_on_its_beats():
    m = rmap(intro=8)
    sl = montage.slots(m, 0.0, 4.0, fps=25.0, modes={"intro": "pulse"})
    assert len(sl) >= 4


def test_no_cluster_repeats_within_six_and_scale_alternates():
    sl = montage.slots(rmap(), 0.0, 18.0, fps=25.0)
    shots = [{"id": f"s{k}", "asset_key": f"k{k}", "kind": "video",
              "t0": 0.2, "t1": 9.0, "cluster": f"c{k % 7}",
              "scale": "close" if k % 2 else "wide", "score": 0.5}
             for k in range(14)]
    asg = montage.assign(sl, shots, seed=2)
    assert len(asg) == len(sl)
    assert montage.repeats_within(asg) == []
    scales = [sh["scale"] for _s, sh, _t in asg]
    assert sum(a != b for a, b in zip(scales, scales[1:])) >= \
        0.8 * (len(scales) - 1)


def test_a_long_shot_is_walked_through_not_repeated():
    sl = montage.slots(rmap(), 5.0, 15.0, fps=25.0)
    shots = [{"id": f"s{k}", "asset_key": f"k{k}", "kind": "video",
              "t0": 0.2, "t1": 30.0, "cluster": f"c{k}", "scale": None,
              "score": 0.5} for k in range(7)]
    asg = montage.assign(sl, shots, seed=1)
    seen = {}
    for _s, sh, t in asg:
        seen.setdefault(sh["id"], []).append(t)
    assert any(len(v) > 1 for v in seen.values())
    assert all(len(set(v)) == len(v) for v in seen.values())


# ── plan_to_music: one batch, intent recorded, flash safety ─────────────

def test_plan_to_music_writes_one_batch_with_its_intent(monkeypatch):
    ctx = ctx_with(monkeypatch, pool(image=True))
    out = MT.plan_to_music(ctx, 0, 18, seed=3)
    assert out.startswith("EDL v3 -> v4"), out
    e = ctx.edl
    covers = [o for o in e["overlays"] if o["id"].startswith("mt1_")]
    stills = [m for m in e["motion"] if m["template"] == "still_motion"]
    assert covers and all(o["fit"] == "cover" for o in covers)
    intent = [f for f in e["fx"] if f["type"] == "arrange"]
    assert len(intent) == 1 and intent[0]["kind"] == "montage_intent"
    assert intent[0]["params"]["pool"]
    # stills move, and not all the same way
    if len(stills) > 1:
        assert len({s["params"]["path"] for s in stills}) > 1
    assert len(ctx.lines) == 1 and "no sound" not in out.lower()


def test_strobe_needs_the_users_ask(monkeypatch):
    modes = {"verse": "strobe"}
    ctx = ctx_with(monkeypatch, pool())
    out = MT.plan_to_music(ctx, 0, 18, modes=modes)
    assert "flash safety" in out
    assert not [f for f in ctx.edl["fx"] if f["type"] == "strobe"]
    ctx = ctx_with(monkeypatch, pool(), msg="strobe the verse please")
    out = MT.plan_to_music(ctx, 0, 18, modes=modes)
    st = [f for f in ctx.edl["fx"] if f["type"] == "strobe"]
    assert len(st) == 1 and all(fr["hold_f"] == 2 for fr in st[0]["frames"])


def test_without_a_user_track_it_cuts_to_the_speech(monkeypatch):
    rows = pool()
    ctx = S.Ctx(S.edl(), keys={r["storage_key"]: r for r in rows})
    monkeypatch.setattr(MT, "_project_assets", lambda c, kinds: rows)
    monkeypatch.setattr(MT, "_library", lambda c, keys: {})
    out = MT.plan_to_music(ctx, 0, 14, dry_run=True)
    assert "speech clock" in out and "stress" in out


def test_no_pool_is_refused(monkeypatch):
    ctx = ctx_with(monkeypatch, [])
    assert MT.plan_to_music(ctx, 0, 18).startswith("REJECTED")


def test_call_and_response_alternates_type_on_black_with_broll(
        monkeypatch):
    import motion_tools
    monkeypatch.setattr(motion_tools, "_canvas_size", lambda c, e: (1080,
                                                                   1920))
    m = rmap(intro=0, kicks_every=2)
    ctx = ctx_with(monkeypatch, pool(), m=m)
    out = MT.plan_to_music(ctx, 0, 14, style="call_response")
    assert out.startswith("EDL v3 -> v4"), out
    e = ctx.edl
    cards = [f for f in e["fx"] if f["type"] == "strobe"
             and f["frames"][0]["source"]["kind"] == "colour"]
    types = [mm for mm in e["motion"] if mm["template"] == "typeset"]
    assert cards and types
    voices = {t["params"]["voice"] for t in types}
    assert len(voices) == 1 and voices.isdisjoint(MT.ON_BLACK_AVOID)


def test_slot_text_keeps_the_words_that_carry_a_picture():
    ws = [{"w": w} for w in "of a narrow cone that".split()]
    assert MT.slot_text(ws) == "narrow cone"
    assert MT.slot_text([{"w": "a"}, {"w": "lot"}]) == ""


def test_a_recorded_montage_replans_deterministically(monkeypatch):
    ctx = ctx_with(monkeypatch, pool())
    MT.plan_to_music(ctx, 0, 18, seed=4)
    first = [(o["start"], o["asset_key"]) for o in ctx.edl["overlays"]]
    out = MT.plan_to_music(ctx, replan="mt1")
    assert out.startswith("EDL v4 -> v5"), out
    again = [(o["start"], o["asset_key"]) for o in ctx.edl["overlays"]]
    assert again == first


def test_a_song_swap_recuts_the_montage_on_the_new_track(monkeypatch,
                                                         tmp_path):
    ctx = ctx_with(monkeypatch, pool())
    MT.plan_to_music(ctx, 0, 18, seed=4)
    e = json.loads(json.dumps(ctx.edl))
    e["music"][0]["storage_key"] = "music/new.mp3"
    e["music"][0]["offset_s"] = None
    MT._MAPS["music/new.mp3"] = rmap(bpm=150.0, n=50, intro=0)
    assert montage.on_swap in hooks.registered("music_swap")
    e2, notes = hooks.on_music_swap(e, "m1", "m1")
    assert any("re-cut on the new track" in n for n in notes), notes
    e2 = schemas.validate_edl(e2, S.DUR).model_dump()
    new = rmap(bpm=150.0, n=50, intro=0)
    kick_f = {montage.frame(o["t"], 25.0) for o in new["onsets"]}
    starts = [montage.frame(o["start"] + 0.005, 25.0)
              for o in e2["overlays"]]
    on = [s for s in starts if s + 1 in kick_f]
    assert len(on) >= 0.95 * len(starts)


# ── arrangement: the music bus ──────────────────────────────────────────

def test_fit_lands_the_drop_on_the_payoff_frame():
    m = rmap()
    item = {"id": "m1", "start": 0.0, "end": 20.0}
    f, rep = arrangement.fit(item, m, 6.13, 20.0, fps=25.0)
    assert f is not None, rep
    pay = round(6.13 * 25) / 25
    drop = arrangement.drop_on_program(dict(item, **f), m)[0]
    assert abs(drop - pay) <= 1 / 25.0
    assert f["end"] <= 20.0


def test_arrange_refuses_a_track_the_user_did_not_choose():
    e = S.edl(music=[{"id": "m1", "storage_key": "music/a.mp3", "start": 0,
                      "end": 10, "provenance": "legacy"}])
    e["fx"] = [{"id": "h", "type": "arrange", "stage": "music_bus",
                "kind": "hole", "music_id": "m1", "start": 2, "end": 3}]
    with pytest.raises(Exception, match="user"):
        schemas.validate_edl(e, S.DUR)


def test_shape_music_refuses_without_a_user_track():
    ctx = S.Ctx(S.edl())
    assert MT.shape_music(ctx, payoff=5.0).startswith("REJECTED")


def test_shape_music_writes_fields_hold_and_filter(monkeypatch):
    ctx = ctx_with(monkeypatch, pool())
    out = MT.shape_music(ctx, payoff=9.40, hold_s=0.45,
                         intro_filter="lowpass")
    assert out.startswith("EDL v3 -> v4"), out
    e = ctx.edl
    kinds = sorted(f["kind"] for f in e["fx"] if f["type"] == "arrange")
    assert kinds == ["filter", "fit_intent", "hold"]
    assert e["music"][0]["offset_s"] is not None


class _Bus:
    def __init__(self):
        self.label, self.parts, self.n = "m0", [], 0

    def uid(self, p):
        self.n += 1
        return f"{p}{self.n}"


def _run_bus(items, dur=3.0, sr=48000):
    bus = _Bus()
    out = arrangement.ARRANGE.music_bus(bus, items)
    graph = ";".join([f"sine=f=440:r={sr}:d={dur}[m0]"] + bus.parts)
    r = subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", graph,
                        "-map", f"[{out}]", "-f", "f32le", "-ac", "1", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, "<f4"), sr


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_hold_is_silent_sample_accurately_without_clicks():
    y, sr = _run_bus([{"kind": "hold", "start": 1.0, "end": 1.5}])
    gap = y[int(1.0 * sr) + 10:int(1.5 * sr) - 10]
    assert np.abs(gap).max() < 1e-3
    # 10 ms ramps either side: no sample jump bigger than the tone's own
    tone = float(np.abs(np.diff(y[:int(0.9 * sr)])).max())
    assert float(np.abs(np.diff(y)).max()) <= tone * 1.05
    # back at the tone's own level after the hold (relative: the bus is
    # set to stereo before its gains, W2 review — a mono track crashed
    # ffmpeg — and this harness folds it back to mono)
    level = float(np.abs(y[:int(0.9 * sr)]).max())
    assert np.abs(y[int(1.52 * sr):int(1.6 * sr)]).max() > 0.8 * level


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_lowpass_intro_opens_on_its_end_frame():
    bus = _Bus()
    out = arrangement.ARRANGE.music_bus(bus, [{"kind": "filter",
                                               "filter": "lowpass",
                                               "start": 0.0, "end": 1.0}])
    graph = ";".join(["anoisesrc=r=48000:d=2:a=0.3[m0]"] + bus.parts)
    r = subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", graph,
                        "-map", f"[{out}]", "-f", "f32le", "-ac", "1", "-"],
                       capture_output=True, check=True)
    y = np.frombuffer(r.stdout, "<f4")
    sr = 48000

    def hi(a, b):
        seg = y[int(a * sr):int(b * sr)]
        sp = np.abs(np.fft.rfft(seg)) ** 2
        f = np.fft.rfftfreq(len(seg), 1 / sr)
        return sp[f > 8000].sum() / sp.sum()
    assert hi(0.9, 0.99) < 0.01          # muffled up to the frame before
    assert hi(1.01, 1.1) > 0.5           # open on the drop


# ── ramps ───────────────────────────────────────────────────────────────

def test_a_ramp_is_contiguous_monotonic_and_never_one():
    spans, rep = ramps.plan(10.0, 14.0, ramps.swing(0.4, 0.3),
                            fps_src=59.94, fps_out=30.0,
                            allow_duplicates=True)
    assert 1 < len(spans) <= ramps.MAX_STEPS
    assert all(abs(s["factor"] - 1.0) >= 0.01 for s in spans)
    assert all(s["end"] - s["start"] >= 0.2 - 1e-9 for s in spans)
    for a, b in zip(spans, spans[1:]):
        assert b["start"] >= a["end"] - 1e-9
    assert min(rep["factors"]) == pytest.approx(0.4, abs=0.02)
    # the steps keep the eased curve's integrated output length
    exact = sum(4.0 / 2000 / ramps.swing(0.4, 0.3)(k / 2000)
                for k in range(2000))
    assert rep["out_s"] == pytest.approx(exact, rel=0.01)


def test_a_ramp_holds_the_clean_floor_unless_told():
    _s, rep = ramps.plan(0.0, 4.0, ramps.swing(0.3, 0.3), fps_src=59.94,
                         fps_out=30.0)
    assert rep["clamped"] and rep["dup_frames"] == 0
    assert min(rep["factors"]) >= ramps.floor(59.94, 30.0) - 1e-3


def test_an_insert_ramp_is_contiguous_pieces_of_whole_frames():
    item = {"id": "ss", "asset_key": "k", "kind": "video",
            "at_output_s": 4.0, "duration_s": 2.6, "source_start_s": 7.2}
    sr = ramps.plan(7.2, 9.8, ramps.swing(0.4, 0.35), fps_src=59.94,
                    fps_out=25.0)
    pieces = MT.ramp_insert_pieces(item, sr, 25.0)
    assert pieces[0]["id"] == "ss" and all(p["split_parent"] == "ss"
                                           for p in pieces)
    src = 7.2
    for p in pieces:
        assert p["source_start_s"] == pytest.approx(src, abs=0.006)
        assert round(p["duration_s"] * 25.0, 6) == int(round(
            p["duration_s"] * 25.0))
        src += p["duration_s"] * p.get("rate", 1.0)
    assert src == pytest.approx(9.8, abs=0.05)


def test_set_speed_ramp_writes_spans_and_moves_the_program(monkeypatch):
    ctx = S.Ctx(S.edl())
    # over speech a ramp drags the voice and its joins are audible: refused
    # unless the user wants it there (integration: Wave 4's time-ramp rule)
    out = MT.set_speed_ramp(ctx, 3.0, 7.0, low=0.6)
    assert out.startswith("REJECTED") and "spoken words" in out, out
    # in a pause it needs no permission (a 25 fps source in a 25 fps
    # program has no clean slow motion: said)
    assert MT.set_speed_ramp(ctx, 15.0, 19.0, low=0.6).startswith(
        "NO CHANGE")
    assert MT.set_speed_ramp(ctx, 3.0, 7.0, low=0.6,
                             allow_speech=True).startswith("NO CHANGE")
    out = MT.set_speed_ramp(ctx, 3.0, 7.0, low=0.6, hold=0.3,
                            allow_duplicates=True, allow_speech=True)
    assert out.startswith("EDL v3 -> v4"), out
    assert len(ctx.edl["speed"]) >= 2
    assert "Program length" in out and "repeat" in out


# ── moving stills (defect #21) ──────────────────────────────────────────

def _still_edl():
    return S.edl(overlays=[{"id": "ph", "asset_key": "img/a.jpg",
                            "kind": "image", "start": 2.0,
                            "duration_s": 3.0, "fit": "cover"},
                           {"id": "pip", "asset_key": "img/a.jpg",
                            "kind": "image", "start": 6.0,
                            "duration_s": 2.0}])


def test_a_cover_photo_becomes_a_moving_still(monkeypatch):
    ctx = S.Ctx(_still_edl(), keys={"img/a.jpg": {"storage_key":
                                                  "img/a.jpg"}})
    out = MT.animate_still(ctx, "ph", path="push_in", focus_x=0.3,
                           focus_y=0.3)
    assert out.startswith("EDL v3 -> v4"), out
    e = ctx.edl
    assert not [o for o in e["overlays"] if o["id"] == "ph"]
    mo = next(m for m in e["motion"] if m["id"] == "ph")
    assert mo["template"] == "still_motion" and mo["layer"] == \
        "below_captions"
    assert (mo["start"], mo["end"]) == (2.0, 5.0)
    assert mo["params"]["image"] == "img/a.jpg"
    assert MT.animate_still(ctx, "pip").startswith("REJECTED")


def test_still_moves_vary_and_follow_a_face():
    looks = [MT.still_look(1600, 1000, None, 1920, 1080, k)["path"]
             for k in range(4)]
    assert len(set(looks)) >= 3
    # a landscape photo on a 9:16 canvas travels across it
    assert MT.still_look(1600, 1000, None, 1080, 1920, 0)["path"] in (
        "pan_left", "pan_right")
    p = MT.still_look(1600, 1000, [0.6, 0.2, 0.7, 0.35], 1080, 1920, 0)
    assert p["path"] in ("push_in", "pull_out") and p["focus_x"] == 0.65
    assert MT.still_look(4000, 1000, None, 1920, 1080, 0)["path"] in (
        "pan_left", "pan_right")


def test_still_motion_template_spec_checks():
    import keepout
    import motion_templates
    sp = motion_templates.spec("still_motion")
    assert sp["layer"] == "below_captions" and not sp["sfx"]
    motion_templates.check_params("still_motion", sp["example"])
    assert not keepout.applicable("still_motion", sp, "below_captions")


# ── the library: shots, clusters, scale ─────────────────────────────────

def test_library_finds_hard_cuts_not_pans():
    x = np.linspace(0, 6 * np.pi, 192)
    a = (127 + 100 * np.sin(x)[None, :] * np.ones((54, 1)))
    yy = np.linspace(0, 3 * np.pi, 54)[:, None]
    b = (127 + 100 * np.cos(yy) * np.ones((1, 192)))
    frames = [a[:, k:k + 96] for k in range(20)] + \
        [b[:, k:k + 96] for k in range(20)]
    # a smooth pan inside each shot; one hard cut at frame 20
    cts = library.cuts(np.stack(frames).astype(np.uint8))
    assert cts == [pytest.approx(2.0)]


def test_library_clusters_alike_shots_across_clips():
    rng = np.random.default_rng(1)
    base = rng.integers(0, 255, (108, 192, 3)).astype(np.uint8)
    other = np.zeros_like(base)
    other[:, :96] = 230
    d1, d2, d3 = (library.describe(x) for x in
                  (base, np.clip(base.astype(int) + 6, 0, 255).astype(
                      np.uint8), other))
    ents = [{"desc": d1}, {"desc": d2}, {"desc": d3}]
    library.cluster(ents)
    assert ents[0]["cluster"] == ents[1]["cluster"] != ents[2]["cluster"]
