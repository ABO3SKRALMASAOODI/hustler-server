"""Track 1B acceptance (ROADMAP Wave 1, AT-1B-1 .. AT-1B-7) on the footage
library, local only.

Every device is written as apply_edit_batch operations (the stale-client
path) on a VFL window, rendered LOSSLESSLY next to a control render of the
same window through the real renderer, with the track-1A stand-in feeding a
source-space RVM person matte (tests/w1b_stub1a.py). Each test measures its
roadmap bar, writes a verdict JSON and 2-up boards against the named
reference moments (internal only; ``VALMERA_REFBENCH_OUT``).

Previews by default (270x480: minutes); ``W1B_ACCEPT_FINAL=1`` renders
1080x1920 finals (the numbers track 1B reports).
"""

import copy
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from localfix import fixture, local_only, reference           # noqa: E402

FINAL = os.environ.get("W1B_ACCEPT_FINAL") == "1"
NEEDS = [fixture("V12", "walkon.mp4"), fixture("V11", "karp_stage_60p.mp4"),
         fixture("V01", "thiel_ai_bigger.mp4"),
         fixture("V13", "colossus_aisle.mp4"), reference("22_"),
         os.path.expanduser("~/Documents/Valmera/.valmera/models/"
                            "rvm_mobilenetv3_fp32.onnx")]
pytestmark = [local_only(*NEEDS)]

os.environ.setdefault("MATTE_RVM_MODEL", NEEDS[-1])


@pytest.fixture(scope="module")
def A():
    import w1b_accept
    return w1b_accept


def _geom(res):
    p = res["probe"]
    return int(p["width"]), int(p["height"])


def _s(H):
    """Pixels per 1080-line pixel (bars are stated at 1080 lines)."""
    return H / 1080.0


def _rfps(res):
    num, den = (int(x) for x in res["probe"]["r_frame_rate"].split("/"))
    return num / den


def _diff_frames(a, b):
    return [i for i, (x, y) in enumerate(zip(a["frames"], b["frames"]))
            if x != y]


def _verb(A, win, base_edl, monkeypatch, **args):
    """What ONE add_subject_effect call writes on this window (the verb on a
    stand-in project context, browserless, model-free)."""
    import agent_tools
    import schemas
    from tools_ext import subject_tools as st
    idx = A.index(win)
    cuts = (win["mask_info"] or {}).get("cuts") or []
    idx["shots"] = [{"start": round(c / win["fps"], 3)} for c in cuts]
    dur = float(idx["video"]["duration"])

    class DB:
        def run(self, fn, *a):
            name = getattr(fn, "__name__", "")
            if name == "project_media_keys":
                return {}
            return None

    class Ctx:
        db, project_id, has_main_video, index = DB(), 1, True, idx
        duration, user_message = dur, ""

        def __init__(self):
            self.edl = schemas.validate_edl(copy.deepcopy(base_edl),
                                            dur).model_dump()

        def latest_edl(self):
            return {"version": 1, "json": copy.deepcopy(self.edl)}

        def write_edl(self, new, desc):
            self.edl = schemas.validate_edl(new, dur).model_dump()
            return f"EDL v1 -> v2: {desc}"
    monkeypatch.setattr(agent_tools, "_get_perception",
                        lambda ctx: A.perception(win))
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: None)
    ctx = Ctx()
    out = st.add_subject_effect(ctx, **args)
    assert out.startswith("EDL"), out
    return [{k: v for k, v in it.items() if v is not None}
            for it in ctx.edl["fx"]], out


# ── AT-1B-1: white silhouette flashes on the three strongest stresses ───

FILL_CASES = {
    "v12": ("V12", "walkon", [[15.2, 22.2]], "9:16", (0.52, 0.5)),
    "v11": ("V11", "karp_stage_60p", [[0.4, 12.4]], None, None),
}


@pytest.fixture(scope="module")
def at1(A):
    out = {}
    for tag, (vfl, name, keep, ratio, focus) in FILL_CASES.items():
        win = A.window(vfl, name)
        picks, wds = A.stresses(win, keep, 3)
        fps = win["fps"]
        items = []
        for k, (t, i, _s) in enumerate(picks):
            a, b = A.span(A.frame_of(t, fps), 3, fps)
            items.append({"id": f"sx{k + 1}", "type": "subject",
                          "stage": "world", "start": a, "end": b,
                          "kind": "fill", "colour": "white", "decay_f": 2,
                          "device": "subject_fill_flash",
                          "bind": {"on": "stress", "ref": i,
                                   "rhythm": "speech"}})
        base = A.edl(keep, ratio=ratio, focus=focus)
        land = [A.frame_of(t, fps) for t, _i, _s in picks]
        rf = fps if FINAL else min(fps, 30.0)
        dumps = sorted({A.frame_of(it["start"], rf) + d for it in items
                        for d in (-1, 0, 1, 2, 3)})
        res = A.run([A.case(f"{tag}_ctl", win, base, preview=not FINAL,
                            dump_frames=dumps),
                     A.case(f"{tag}_fill", win, base,
                            [A.upsert(it) for it in items],
                            preview=not FINAL, dump_frames=dumps)],
                    f"at1_{tag}")
        out[tag] = {"win": win, "base": base, "items": items, "land": land,
                    "words": [wds[i]["w"] for _t, i, _s in picks],
                    "ctl": res[f"{tag}_ctl"], "fx": res[f"{tag}_fill"]}
    return out


@pytest.mark.parametrize("tag", sorted(FILL_CASES))
def test_at_1b_1_white_silhouette_flashes(A, at1, tag):
    d = at1[tag]
    ctl, fx = d["ctl"], d["fx"]
    assert ctl["error"] is None and fx["error"] is None, fx["error"]
    assert [r["rung"] for r in fx["report"]] == ["full"] * 3
    W, H = _geom(fx)
    s = _s(H)
    import fxprims
    rf = _rfps(fx)
    pm = A.program_matte(d["win"], d["base"], W, H, rf,
                         os.path.join(A.OUT, "mattes",
                                      f"at1_{tag}_{W}x{H}_{rf:.2f}.mkv"))
    diff = _diff_frames(ctl, fx)
    metrics = {"landings": [], "render_fps": round(rf, 3)}
    for it in d["items"]:
        f0, f1 = fxprims.frame_span(it["start"], it["end"], rf)
        f = f0
        # the peak frame(s) then the decay, nothing either side
        got = [k for k in diff if f0 - 3 <= k <= f1 + 3]
        assert got == list(range(f0, f1)), (f0, f1, got)
        m = A.matte_frames(pm, [f])[f]
        c = A.frames_yuv(ctl["path"], [f], W, H)[f]
        r = A.frames_yuv(fx["path"], [f], W, H)[f]
        core = A.shrink(m >= 230, max(1, round(2 * s)))
        mean_y = float(r[0][core].mean())
        filled = (r[0] >= 225) & (np.abs(r[0] - c[0]) > 8)
        truth = m >= 128
        iou = float((filled & truth).sum() / max(1, (filled | truth).sum()))
        out = ~A.grow(m > 0, max(2, round(2 * s)))
        bg = int(np.abs(r[0] - c[0])[out].max())
        oc = out[::2, ::2] & out[1::2, 1::2]
        bgc = int(max(np.abs(r[1] - c[1])[oc].max(),
                      np.abs(r[2] - c[2])[oc].max()))
        metrics["landings"].append({
            "frame": f, "land_err_f": got[0] - A.frame_of(
                it["start"], rf), "frames": f1 - f0,
            "subject_mean_Y": round(mean_y, 2), "fill_iou": round(iou, 4),
            "background_abs_delta_Y": bg, "background_abs_delta_UV": bgc})
        assert mean_y >= 234.5, mean_y          # Y 235 = RGB 255 (TV range)
        assert iou >= 0.95, iou
        assert bg <= 2 and bgc <= 2, (bg, bgc)
    A.write_verdict(f"AT-1B-1-{tag}", {
        "case": "AT-1B-1", "footage": tag, "final": FINAL,
        "words": d["words"], "metrics": metrics,
        "wall_s": {"with": fx["wall_s"], "without": ctl["wall_s"]},
        "cpu_s": {"with": fx.get("cpu_s"), "without": ctl.get("cpu_s")}})
    f = A.frame_of(d["items"][0]["start"], rf)
    A.board("AT-1B-1", fx["path"], f / round(rf, 3) + 0.001, "22", 8.43,
            footer=[f"{tag}: '{d['words'][0]}' frame {f}; IoU "
                    f"{metrics['landings'][0]['fill_iou']}"], tag=f"-{tag}")


def test_at_1b_1_beyond_one_call_hits_every_shot_of_a_montage(A,
                                                              monkeypatch):
    """The same sentence on all 6 shots of a montage, in ONE call:
    add_subject_effect(kind=fill, on=stress, count=1, per=shot)."""
    win = A.window("V12", "walkon")
    # six jump-cut shots of the speech (every one has words)
    keep = [[15.3, 16.2], [16.3, 17.3], [18.6, 19.4], [19.4, 20.2],
            [20.2, 21.0], [21.3, 22.2]]
    base = A.edl(keep, focus=(0.5, 0.5))
    items, reply = _verb(A, win, base, monkeypatch, kind="fill",
                         on="stress", count=1, per="shot")
    from timeline import Timeline
    tl = Timeline(keep)
    edges = list(tl.offsets) + [tl.out_duration]
    shots = [sum(1 for it in items if a <= it["start"] < b)
             for a, b in zip(edges, edges[1:])]
    assert len(items) == 6 and shots == [1] * 6, (shots, reply)
    res = A.run([A.case("ctl", win, base, preview=True),
                 A.case("mont", win, base, [A.upsert(i) for i in items],
                        preview=True)], "at1_montage")
    assert res["mont"]["error"] is None
    assert [r["rung"] for r in res["mont"]["report"]] == ["full"] * 6
    diff = _diff_frames(res["ctl"], res["mont"])
    rf = _rfps(res["mont"])
    for it in items:
        f = A.frame_of(it["start"], rf)
        assert f in diff
    A.write_verdict("AT-1B-1-montage", {
        "case": "AT-1B-1", "beyond": "one call, every shot", "reply": reply,
        "items": items})


# ── AT-1B-7: subject-only negative, region_locality = 0 ─────────────────

@pytest.fixture(scope="module")
def at7(A):
    win = A.window("V01", "thiel_ai_bigger")
    keep = [[3.0, 9.0]]
    base = A.edl(keep, focus=(0.42, 0.5))
    fps = win["fps"]
    a, b = A.span(A.frame_of(2.0, fps), 2, fps)
    item = {"id": "sx1", "type": "subject", "stage": "world", "start": a,
            "end": b, "kind": "negate", "scope": "subject",
            "device": "subject_negate"}
    f = A.frame_of(2.0, fps)
    res = A.run([A.case("ctl", win, base, preview=not FINAL,
                        dump_frames=[f, f + 1]),
                 A.case("neg", win, base, [A.upsert(item)],
                        preview=not FINAL, dump_frames=[f, f + 1])], "at7")
    return {"win": win, "base": base, "res": res, "frames": [f, f + 1]}


def test_at_1b_7_subject_only_negative_is_local(A, at7):
    import qa_metrics
    ctl, fx = at7["res"]["ctl"], at7["res"]["neg"]
    assert fx["error"] is None and fx["report"][0]["rung"] == "full"
    W, H = _geom(fx)
    pm = A.program_matte(at7["win"], at7["base"], W, H, at7["win"]["fps"],
                         os.path.join(A.OUT, "mattes", f"at7_{W}x{H}.mkv"))
    fr = at7["frames"]
    assert _diff_frames(ctl, fx) == fr
    m = A.matte_frames(pm, fr)
    a = A.frames_rgb(fx["path"], fr)
    c = A.frames_rgb(ctl["path"], fr)
    region = [(A.grow(m[k] > 0, 1)).astype(np.uint8) for k in fr]
    loc = qa_metrics.region_locality(with_fx=[a[k] for k in fr],
                                     without_fx=[c[k] for k in fr],
                                     region=region)
    inside = float(np.mean([np.abs(a[k] - c[k])[m[k] >= 230].mean()
                            for k in fr]))
    A.write_verdict("AT-1B-7", {"case": "AT-1B-7", "final": FINAL,
                                "region_locality": loc,
                                "inside_mean_abs_delta": round(inside, 2)})
    assert loc["value"] == 0, loc
    assert inside > 60
    A.board("AT-1B-7", fx["path"], fr[0] / round(at7["win"]["fps"], 3)
            + 0.001, "13", 6.92,
            footer=[f"region_locality {loc['value']:.0f}; inside mean "
                    f"|delta| {inside:.0f}"])


# ── AT-1B-2: poster silhouette on the Look's accent field ───────────────

POSTER_CASES = {
    "v01": ("V01", "thiel_ai_bigger", [[3.0, 9.0]], (0.42, 0.5)),
    "v11": ("V11", "stage_two_figures", [[20.0, 26.0]], (0.5, 0.5)),
}
ACCENT = "#ff5a36"


@pytest.fixture(scope="module")
def at2(A):
    out = {}
    for tag, (vfl, name, keep, focus) in POSTER_CASES.items():
        win = A.window(vfl, name)
        base = A.edl(keep, focus=focus,
                     captions={"style": {"highlight_color": ACCENT}})
        sil = {"id": "sx1", "type": "subject", "stage": "world",
               "start": 1.0, "end": 3.0, "kind": "poster",
               "field": "accent", "keep_footage": False, "colour": "black",
               "device": "subject_poster"}
        keepf = dict(sil, keep_footage=True, colour=None)
        keepf = {k: v for k, v in keepf.items() if v is not None}
        fps = win["fps"]
        fr = list(range(A.frame_of(1.0, fps), A.frame_of(3.0, fps)))
        # the Look's accent comes from the caption style; no transcript
        # words, so no caption is burned over the field being measured
        quiet = A.index(win, [])
        res = A.run([A.case(f"{tag}_ctl", win, base, preview=not FINAL,
                            idx=quiet),
                     A.case(f"{tag}_sil", win, base, [A.upsert(sil)],
                            preview=not FINAL, idx=quiet),
                     A.case(f"{tag}_keep", win, base, [A.upsert(keepf)],
                            preview=not FINAL, idx=quiet)], f"at2_{tag}")
        for r in res.values():
            r["idx_words"] = 0
        out[tag] = {"win": win, "base": base, "res": res, "frames": fr}
    return out


@pytest.mark.parametrize("tag", sorted(POSTER_CASES))
def test_at_1b_2_poster_on_the_accent_field(A, at2, tag):
    import qa_metrics
    import subject_fx
    d = at2[tag]
    sil, keep = d["res"][f"{tag}_sil"], d["res"][f"{tag}_keep"]
    for r in (sil, keep):
        assert r["error"] is None and r["report"][0]["rung"] == "full", r
    W, H = _geom(sil)
    s = _s(H)
    pm = A.program_matte(d["win"], d["base"], W, H, d["win"]["fps"],
                         os.path.join(A.OUT, "mattes",
                                      f"at2_{tag}_{W}x{H}.mkv"))
    fr = d["frames"]
    sample = fr[::max(1, len(fr) // 8)]
    ms = A.matte_frames(pm, fr)
    space = A.colour_space(sil["path"])
    want = A.yuv_of(subject_fx._hex_rgb(ACCENT), space)
    sig, dev, halo = [], [], []
    rs = A.frames_rgb(sil["path"], fr)
    rk = A.frames_rgb(keep["path"], sample)
    ys = A.frames_yuv(sil["path"], sample, W, H)
    acc = np.array(subject_fx._hex_rgb(ACCENT), float)
    for k in sample:
        y, u, v = ys[k]
        field = ~A.grow(ms[k] > 0, max(3, round(3 * s)))
        fc = field[::2, ::2] & field[1::2, 1::2]
        sig.append(max(float(np.std(y[field])), float(np.std(u[fc])),
                       float(np.std(v[fc]))))
        dev.append(max(abs(float(np.median(y[field])) - want[0]),
                       abs(float(np.median(u[fc])) - want[1]),
                       abs(float(np.median(v[fc])) - want[2])))
        # no halo: just outside the subject's 50 % edge, the kept-footage
        # poster shows the field, not the footage's own background
        edge = ms[k] >= 128
        inner = max(1, round(1 * s))
        band = A.grow(edge, max(inner + 1, round(3 * s))) & \
            ~A.grow(edge, inner)
        # the frame border is not an edge between the subject and the field
        bm = max(3, round(6 * s))
        band[:bm], band[-bm:], band[:, :bm], band[:, -bm:] = (
            False, False, False, False)
        dist = np.sqrt(((rk[k].astype(float) - acc) ** 2).sum(axis=2))
        halo.append(round(float(np.percentile(dist[band], 95)), 2))
    # edge chatter of the silhouette as drawn (black on the field)
    masks = [(rs[k].astype(float).mean(axis=2) < 80).astype(np.float32)
             for k in fr]
    ch = qa_metrics.edge_chatter(masks=masks, fps=d["win"]["fps"])
    met = {"colour_space": space, "field_sigma": round(max(sig), 3),
           "field_vs_accent_max": round(max(dev), 2),
           "edge_chatter": ch["value"],
           "halo_band_p95_rgb_distance": round(max(halo), 2),
           "halo_per_frame": halo}
    A.write_verdict(f"AT-1B-2-{tag}", {"case": "AT-1B-2", "final": FINAL,
                                       "metrics": met})
    assert met["field_sigma"] <= 1.0, met
    assert met["field_vs_accent_max"] <= 2, met
    assert met["edge_chatter"] <= 0.002, met
    assert met["halo_band_p95_rgb_distance"] <= 40, met
    t = (fr[len(fr) // 2]) / round(d["win"]["fps"], 3) + 0.001
    A.board("AT-1B-2", sil["path"], t, "23", 2.5, tag=f"-{tag}-sil",
            footer=[f"field sigma {met['field_sigma']}, chatter "
                    f"{met['edge_chatter']}"])
    A.board("AT-1B-2", keep["path"], t, "38", 0.3, tag=f"-{tag}-keep",
            footer=[f"halo p95 {met['halo_band_p95_rgb_distance']}"])


# ── AT-1B-3: subject held while the world swaps to three plates ─────────

PLATES = {"media/w1b/starship.mp4": ("V13", "starship_launch_4k60", 9.0),
          "media/w1b/colossus.mp4": ("V13", "colossus_aisle", 6.0),
          "media/w1b/dragon.mp4": ("V13", "dragon_unveil", 6.0)}


@pytest.fixture(scope="module")
def at3(A):
    win = A.window("V12", "walkon")
    keep = [[15.2, 21.2]]
    base = A.edl(keep, focus=(0.52, 0.5))
    fps = win["fps"]
    hold = A.frame_of(2.0, fps) - 1
    targets = [A.frame_of(2.0, fps) + 10 * i for i in range(3)]
    items = []
    for i, (key, (_v, _n, off)) in enumerate(PLATES.items()):
        a = round(targets[i] / round(fps, 3), 2)
        b = round((targets[i] + 10) / round(fps, 3), 2)
        items.append({"id": f"bg{i + 1}", "type": "background",
                      "stage": "world", "start": a, "end": b,
                      "kind": "source", "source": {
                          "kind": "asset", "asset_key": key,
                          "offset_s": off},
                      "hold_subject": True,
                      "hold_t": round(hold / round(fps, 3), 2),
                      "device": "background_subject_hold"})
    locs = {k: fixture(v, f"{n}.mp4") for k, (v, n, _o) in PLATES.items()}
    fr = list(range(targets[0] - 2, targets[-1] + 12))
    res = A.run([A.case("ctl", win, base, preview=not FINAL,
                        asset_locals=locs),
                 A.case("hold", win, base, [A.upsert(i) for i in items],
                        preview=not FINAL, asset_locals=locs)], "at3")
    return {"win": win, "base": base, "res": res, "hold": hold,
            "targets": targets, "frames": fr}


def test_at_1b_3_subject_hold_background_swap(A, at3):
    import qa_metrics
    ctl, fx = at3["res"]["ctl"], at3["res"]["hold"]
    assert fx["error"] is None, fx["error"]
    assert [r["rung"] for r in fx["report"]] == ["full"] * 3
    W, H = _geom(fx)
    pm = A.program_matte(at3["win"], at3["base"], W, H, at3["win"]["fps"],
                         os.path.join(A.OUT, "mattes", f"at3_{W}x{H}.mkv"))
    hold, t = at3["hold"], at3["targets"]
    m = A.matte_frames(pm, [hold])[hold]
    core = A.shrink(m >= 240, max(3, round(3 * _s(H))))
    win_frames = list(range(t[0], t[-1] + 10))
    got = A.frames_rgb(fx["path"], win_frames)
    held = A.frames_rgb(ctl["path"], [hold])[hold]
    # frozen: the cut-out does not change across the window (0 px of
    # motion), and it IS the program's frame at hold_t
    motion = max(int(np.abs(got[k] - got[win_frames[0]])[core].max())
                 for k in win_frames)
    vs_hold = float(np.percentile(np.abs(got[win_frames[0]] - held)[core],
                                  99))
    # the world swaps exactly on the target frames
    world = ~A.grow(m > 0, max(3, round(4 * _s(H))))
    jumps = {}
    seq = A.frames_rgb(fx["path"], list(range(t[0] - 1, t[-1] + 11)))
    for k in range(t[0], t[-1] + 11):
        jumps[k] = float(np.abs(seq[k] - seq[k - 1])[world].mean())
    top = sorted(sorted(jumps, key=lambda k: -jumps[k])[:4])
    dur = qa_metrics.duration_exact(with_fx=fx["path"],
                                    without_fx=ctl["path"])
    met = {"held_subject_max_abs_delta": motion,
           "held_vs_hold_frame_p99": vs_hold,
           "swap_frames": top, "target_frames": t + [t[-1] + 10],
           "duration_exact": dur["value"],
           "audio_identical": dur["detail"]["audio_identical"]}
    A.write_verdict("AT-1B-3", {"case": "AT-1B-3", "final": FINAL,
                                "metrics": met})
    assert motion == 0, met                  # frozen: 0 px of motion
    assert vs_hold <= 2, met
    assert top == t + [t[-1] + 10], met      # in at each target, out after
    assert dur["value"] == 0 and dur["pass"], dur
    fps = round(at3["win"]["fps"], 3)
    A.sheet("AT-1B-3", [(A.ref_path("20"), 7.36, "ref 20 @7.36"),
                        (A.ref_path("20"), 7.40, "ref 20 @7.40"),
                        (fx["path"], t[0] - 1, f"ours frame {t[0] - 1}"),
                        (fx["path"], t[0], f"ours swap 1 (frame {t[0]})"),
                        (fx["path"], t[1], f"ours swap 2 (frame {t[1]})"),
                        (fx["path"], t[2], f"ours swap 3 (frame {t[2]})")])


# ── AT-1B-4: red ghost clones k = 4 and 8 frames back ───────────────────

H1 = fixture("footage", "HC-H", "h1-locked-loft-man-walks-then-sits",
             "pexels-6219762-6219762-hd_2048_1080_30fps.mp4")


@pytest.fixture(scope="module")
def at4(A):
    """Clone geometry on the house-corpus twin stand-in HC-H/h1 (a locked
    loft, a man walking across a bright wall: interim, ROADMAP §4.3), and
    the red ghost look on the V12 crowd for the board. Always 1080-line
    finals: where an earlier copy shows is a sliver at preview size."""
    out = {}
    if os.path.exists(H1):
        win = A.window("HC-H", "h1", path=H1)
        keep = [[0.3, 4.3]]
        base = A.edl(keep, ratio=None)
        out["h1"] = {"win": win, "base": base}
    v12 = A.window("V12", "walkon")
    out["v12"] = {"win": v12, "base": A.edl([[0.3, 6.3]], focus=(0.5, 0.5))}

    def echo(fid, **e):
        e = dict({"mode": "clone", "offsets_f": [4, 8]}, **e)
        return {"id": fid, "type": "subject", "stage": "world",
                "start": 1.0, "end": 3.0, "kind": "echo", "echo": e,
                "device": "subject_echo"}
    for tag, d in out.items():
        cases = [A.case("ctl", d["win"], d["base"], preview=False),
                 A.case("one", d["win"], d["base"],
                        [A.upsert(echo("e1", opacity=1.0))], preview=False),
                 A.case("half", d["win"], d["base"],
                        [A.upsert(echo("e2", opacity=0.5))], preview=False),
                 A.case("red", d["win"], d["base"], [A.upsert(echo(
                     "e3", opacity=0.75, tint="#ff2a2a"))], preview=False)]
        if tag == "v12":
            cases = [c for c in cases if c["name"] == "red"]
        d["res"] = A.run(cases, f"at4_{tag}")
    return out


@local_only(H1)
def test_at_1b_4_clones_sit_where_the_subject_was(A, at4):
    d = at4["h1"]
    res = d["res"]
    for k in ("one", "half", "red"):
        assert res[k]["error"] is None and res[k]["report"][0]["rung"] == \
            "full", res[k]["report"]
    assert at4["v12"]["res"]["red"]["error"] is None
    W, H = _geom(res["one"])
    fps = d["win"]["fps"]
    pm = A.program_matte(d["win"], d["base"], W, H, fps,
                         os.path.join(A.OUT, "mattes", f"at4_{W}x{H}.mkv"))
    frames = list(range(A.frame_of(1.3, fps), A.frame_of(2.95, fps), 4))
    need = sorted({f - d for f in frames for d in range(0, 11)})
    ms = A.matte_frames(pm, need)
    ctl = A.frames_rgb(res["ctl"]["path"], need)
    one = A.frames_rgb(res["one"]["path"], frames)
    half = A.frames_rgb(res["half"]["path"], frames)
    found, alphas = {4: [], 8: []}, []
    for f in frames:
        # where copy k is the only thing drawn, fully: its matte solid
        # (firmed alpha 1), the subject's and the nearer copy's clear (0)
        for k in (4, 8):
            vis = (ms[f - k] >= 230) & (ms[f] <= 13)
            if k == 8:
                vis &= ms[f - 4] <= 13
            if vis.sum() < max(40, 100 * _s(H) ** 2):
                continue
            ys, xs = np.nonzero(vis)
            best = None
            # where, and from when, is the copy: search time and shift
            for kk in range(k - 2, k + 3):
                ref = ctl[f - kk].astype(float)
                for dy in (-2, -1, 0, 1, 2):
                    for dx in (-2, -1, 0, 1, 2):
                        yy = np.clip(ys + dy, 0, H - 1)
                        xx = np.clip(xs + dx, 0, W - 1)
                        e = float(np.abs(one[f][ys, xs].astype(float)
                                         - ref[yy, xx]).mean())
                        if best is None or e < best[0]:
                            best = (e, kk, dx, dy)
            found[k].append({"frame": f, "k": best[1], "dx": best[2],
                             "dy": best[3], "err": round(best[0], 2),
                             "px": int(vis.sum())})
            dfull = (one[f] - ctl[f]).astype(float)[vis]
            dhalf = (half[f] - ctl[f]).astype(float)[vis]
            ok = np.abs(dfull) > 20
            if ok.sum() > 50:
                alphas.append(float(np.median(dhalf[ok] / dfull[ok])))
    met = {"clones": found, "opacity_spec": 0.5,
           "opacity_measured": round(float(np.median(alphas)), 3)
           if alphas else None}
    A.write_verdict("AT-1B-4", {"case": "AT-1B-4", "final": True,
                                "interim": "HC-H/h1 stand-in",
                                "metrics": met})
    assert found[4] and found[8], met
    for k in (4, 8):
        for r in found[k]:
            assert r["k"] == k and abs(r["dx"]) <= 1 and abs(r["dy"]) <= 1, \
                (k, r)
    assert abs(met["opacity_measured"] - 0.5) <= 0.06, met
    A.sheet("AT-1B-4", [(A.ref_path("40"), 18.5, "ref 40 @18.5"),
                        (A.ref_path("22"), 6.6, "ref 22 @6.6"),
                        (res["red"]["path"], frames[len(frames) // 2],
                         "ours red clones (h1)"),
                        (at4["v12"]["res"]["red"]["path"], 60,
                         "ours red clones (V12)")], height=480)


# ── AT-1B-5: glowing silhouettes (the screen formula, a bounded halo) ───

GLOW_CASES = {"v12": ("V12", "walkon", [[6.6, 9.9], [15.2, 22.2]],
                      (0.52, 0.5)),
              "v13": ("V13", "optimus_walk", [[0.5, 4.8], [5.0, 13.0]],
                      (0.5, 0.5))}


@pytest.fixture(scope="module")
def at5(A):
    out = {}
    for tag, (vfl, name, keep, focus) in GLOW_CASES.items():
        win = A.window(vfl, name)
        base = A.edl(keep, focus=focus)
        it = {"id": "sx1", "type": "subject", "stage": "world",
              "start": 4.5, "end": 5.5, "kind": "glow", "colour": "white",
              "radius": 0.04, "intensity": 1.0, "device": "subject_glow"}
        res = A.run([A.case("ctl", win, base, preview=not FINAL),
                     A.case("glow", win, base, [A.upsert(it)],
                            preview=not FINAL)], f"at5_{tag}")
        out[tag] = {"win": win, "base": base, "res": res, "item": it}
    return out


@pytest.mark.parametrize("tag", sorted(GLOW_CASES))
def test_at_1b_5_glow_is_screen_light_with_a_bounded_halo(A, at5, tag):
    import cv2
    d = at5[tag]
    ctl, fx = d["res"]["ctl"], d["res"]["glow"]
    assert fx["error"] is None and fx["report"][0]["rung"] == "full"
    W, H = _geom(fx)
    fps = d["win"]["fps"]
    pm = A.program_matte(d["win"], d["base"], W, H, fps,
                         os.path.join(A.OUT, "mattes",
                                      f"at5_{tag}_{W}x{H}.mkv"))
    frames = [A.frame_of(t, fps) for t in (4.6, 5.0, 5.4)]
    ms = A.matte_frames(pm, frames)
    a = A.frames_rgb(fx["path"], frames)
    c = A.frames_rgb(ctl["path"], frames)
    resid, extent, beyond, darker = [], [], [], []
    r_px = d["item"]["radius"] * H
    for f in frames:
        P, O = c[f].astype(float), a[f].astype(float)
        darker.append(float((O - P).min()))
        # screen with a white light: O = P + L (255 - P) on every channel
        room = 255.0 - P
        ok = (room > 60).all(axis=2) & ((O - P).max(axis=2) > 6)
        if ok.sum() > 50:
            L = (O - P)[ok] / room[ok]
            resid.append(float(np.median(L.std(axis=1) * 255.0)))
        outside = (ms[f] < 13).astype(np.uint8)
        dist = cv2.distanceTransform(outside, cv2.DIST_L2, 5)
        gain = (O - P).max(axis=2)
        rings = []
        for dd in range(1, int(r_px * 1.6)):
            ring = (dist >= dd) & (dist < dd + 1)
            if ring.sum() > 20:
                rings.append((dd, float(gain[ring].mean())))
        vis = [dd for dd, g in rings if g >= 2.0]
        extent.append((max(vis) if vis else 0) / H)
        far = dist > r_px + 3
        beyond.append(float(np.abs(O - P).max(axis=2)[far].max())
                      if far.any() else 0.0)
    met = {"screen_residual_levels": round(max(resid), 3) if resid else None,
           "darkest_change": round(min(darker), 2),
           "halo_extent_share_of_height": round(max(extent), 4),
           "max_abs_delta_beyond_radius": round(max(beyond), 2),
           "radius": d["item"]["radius"]}
    A.write_verdict(f"AT-1B-5-{tag}", {"case": "AT-1B-5", "final": FINAL,
                                       "metrics": met})
    # screen never darkens: what is left is 4:2:0 rounding of the blend
    assert met["darkest_change"] >= -4, met
    assert met["screen_residual_levels"] is not None and \
        met["screen_residual_levels"] <= 3.0, met
    assert 0.02 <= met["halo_extent_share_of_height"] <= 0.04, met
    assert met["max_abs_delta_beyond_radius"] <= 2, met
    A.board("AT-1B-5", fx["path"], 5.0, "36", 5.0, tag=f"-{tag}",
            footer=[f"halo {met['halo_extent_share_of_height'] * 100:.1f}% "
                    f"of height; residual {met['screen_residual_levels']}"])


# ── AT-1B-6: background replaced by a UI recording (owner board) ────────

UI = os.path.join(os.path.expanduser("~/Documents/Valmera/.valmera/"
                                     "fixtures"), "derived", "w1b_ui",
                  "valmera_io.mp4")


@local_only(UI, fixture("V02", "thiel_flying_cars.mp4"))
def test_at_1b_6_background_replaced_by_a_ui_recording(A):
    win = A.window("V02", "thiel_flying_cars")
    keep = [[6.0, 26.0]]
    base = A.edl(keep, focus=(0.41, 0.31))
    it = {"id": "bg1", "type": "background", "stage": "world", "start": 2.0,
          "end": 4.0, "kind": "source", "source": {
              "kind": "asset", "asset_key": "media/w1b/ui.mp4"},
          "device": "background_swap"}
    res = A.run([A.case("ctl", win, base, preview=not FINAL),
                 A.case("ui", win, base, [A.upsert(it)], preview=not FINAL,
                        asset_locals={"media/w1b/ui.mp4": UI})], "at6")
    fx = res["ui"]
    assert fx["error"] is None and fx["report"][0]["rung"] == "full"
    W, H = _geom(fx)
    fps = win["fps"]
    pm = A.program_matte(win, base, W, H, fps,
                         os.path.join(A.OUT, "mattes", f"at6_{W}x{H}.mkv"))
    f = A.frame_of(3.0, fps)
    m = A.matte_frames(pm, [f])[f]
    a = A.frames_rgb(fx["path"], [f])[f]
    c = A.frames_rgb(res["ctl"]["path"], [f])[f]
    core = A.shrink(m >= 240, 2)
    world = ~A.grow(m > 0, 3)
    met = {"subject_core_max_abs_delta": int(np.abs(a - c)[core].max()),
           "world_mean_abs_delta": round(float(np.abs(a - c)[world].mean()),
                                         2)}
    A.write_verdict("AT-1B-6", {"case": "AT-1B-6", "final": FINAL,
                                "metrics": met, "judge": "owner board"})
    assert met["subject_core_max_abs_delta"] == 0
    assert met["world_mean_abs_delta"] > 20
    A.board("AT-1B-6", fx["path"], 3.0, "18", 10.72)
