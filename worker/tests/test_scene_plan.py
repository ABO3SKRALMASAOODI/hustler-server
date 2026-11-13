"""worker/scene without a GPU: the plan, the compile, curves, segments and type layout.
Everything here runs anywhere the worker suite runs. What draws is in test_scene_engine.py."""
import ast
import json
import math
import os
import shutil
import subprocess

import numpy as np
import pytest

import scene
import scene.bake as B
import scene.segments as S
import version
from scene.faults import SceneFault
from scene.plan import Plan, eligible

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
FPS = 30
VOICE = {"a": {"font": "InterDisplay-Bold.ttf", "px": 58, "track": -3, "shadow": {"a": 0.35, "blur": 0.012}},
         "b": {"font": "InstrumentSerif-Italic.ttf", "px": 62, "track": 0}}
try:
    import uharfbuzz  # noqa: F401
    HB = True
except Exception:      # noqa: BLE001
    HB = False
needs_hb = pytest.mark.skipif(not HB, reason="uharfbuzz is not installed")
needs_ffmpeg = pytest.mark.skipif(not shutil.which(S.FF), reason="no ffmpeg on this machine")


def film(items, dur, voices=None, frame=(1080, 1920), seed=7, film_=None):
    return {"l1": 1, "sid": "t", "seed": seed, "fps": FPS, "frame": list(frame), "dur_f": dur, "assets": {}, "spine": [],
            "voices": voices or VOICE, "film": film_ if film_ is not None else {"grain": 0.04}, "items": items, "sound": []}


def plates(lens, c="#102030"):
    """Shots end to end: plates, so nothing needs a source."""
    out, at = [], 0
    for i, n in enumerate(lens):
        out.append({"id": f"s{i:03d}", "k": "shot", "f": [at, at + n], "pic": "plate", "plate": {"c0": c, "c1": c, "grain": 0}})
        at += n
    return out, at


def word(i, a, b, t="chegando", **kw):
    return dict({"id": f"w{i}", "k": "word", "f": [a, b], "t": t, "v": "a", "x": 0.3, "y": 0.6, "ink": "#FFFFFF", "role": "cap", "on": a}, **kw)


def talk(seconds=60, seed=3):
    """A film shaped like a talking short: shots of 0.4 to 3 s and a caption word every 10 frames."""
    r = np.random.RandomState(seed)
    lens, total = [], 0
    while total < seconds * FPS:
        n = int(r.randint(12, 90))
        lens.append(n)
        total += n
    shots, dur = plates(lens)
    words = [word(i, f, min(f + 14, dur), t=f"w{i % 37}") for i, f in enumerate(range(5, dur - 20, 10))]
    return shots, words, dur


# ------------------------------------------------------------------ the rule: no model in the engine

def test_the_engine_imports_no_model_code():
    """worker/scene/ reads no model (DESIGN 4.1). This fails the build if an import ever says otherwise."""
    banned = {"llm", "anthropic", "openai", "agent_loop", "agent_tools", "agent_prompt", "claude_lane", "score", "director", "litellm"}
    seen = []
    root = os.path.join(WORKER, "scene")
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(root, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [node.module or ""]
            seen += [(name, m) for m in mods if m.split(".")[0] in banned]
    assert not seen, seen


def test_scene_is_inside_the_code_fingerprint():
    rel = {os.path.relpath(p, WORKER).replace(os.sep, "/") for p in version._source_files()}
    for need in ("scene/__init__.py", "scene/compile.py", "scene/runtime.py", "scene/cost_table.json", "scene/glsl/pic.frag.glsl",
                 "scene/glsl/join.frag.glsl", "scene/glsl/common.glsl"):
        assert need in rel, need


# ------------------------------------------------------------------ E5: curves

@pytest.mark.parametrize("name", sorted(B.SETTLE_FITS))
def test_settle_is_the_measured_curve(name):
    """ATLAS 8.5: s(t) = s_inf + (1 - s_inf) e^(-t/tau), fitted on three films. The raw measurements
    are not on disk, so this pins the model: the baked samples, refitted, give back each film's two
    numbers within 0.3%, and there is no overshoot."""
    fit = B.SETTLE_FITS[name]
    s = np.array(B.settle(40, fit["s_inf"], fit["tau"]))
    assert s[0] == 1.0 and np.all(np.diff(s) < 0) and s[-1] > fit["s_inf"]
    t = np.arange(40)
    best = min(((abs(np.polyfit(t, np.log(s - si), 1)[0] + 1 / fit["tau"]), si) for si in np.linspace(fit["s_inf"] - 0.02, fit["s_inf"] + 0.02, 81)
                if np.all(s - si > 0)), key=lambda x: x[0])
    assert abs(best[1] - fit["s_inf"]) / fit["s_inf"] < 0.003
    tau = -1 / np.polyfit(t, np.log(s - fit["s_inf"]), 1)[0]
    assert abs(tau - fit["tau"]) / fit["tau"] < 0.003
    n = B.settle(40, fit["s_inf"], fit["tau"], norm=True)
    assert abs(n[0] - 1 / fit["s_inf"]) < 1e-12 and abs(n[-1] - 1.0) < 0.01


def test_flicker_burn_relay_step_reproduce_the_measured_passage():
    assert B.flicker(9) == [0.5, 0.9, 0.25, 0.85, 0.3, 1.0, 1.0, 1.0, 1.0]            # ATLAS 8.3
    assert B.burn(10)[:8] == [0.90, 0.76, 0.61, 0.50, 0.30, 0.14, 0.06, 0.02] and B.burn(10)[8:] == [0.0, 0.0]   # ATLAS 8.6
    th = B.burn_th(8)
    assert all(b > a for a, b in zip(th, th[1:])), "the white floor recedes: the threshold only rises"
    cells = [(round(r[0] * 3), round(r[1] * 3)) for r in B.relay(18)]
    assert cells[::2] == [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2), (2, 1), (2, 0), (1, 0), (1, 1)] and cells[0::2] == cells[1::2]
    assert B.step([0, 1, 2, 3, 4, 5, 6], 2) == [0, 0, 2, 2, 4, 4, 6]


def test_ui_zoom_peaks_early_and_bounce_overshoots_once():
    z = np.array(B.ui_zoom(101, 1.0, 1.3))
    v = np.diff(z)
    assert z[0] == 1.0 and abs(z[-1] - 1.3) < 1e-9 and np.all(v >= -1e-12)
    assert abs(int(np.argmax(v)) / 100 - 0.22) < 0.03, "velocity peaks near 22% of the move"
    y = np.array(B.bounce(14, rest=0.5))
    assert abs(y[0] - 0.53) < 1e-9 and abs(y[-1] - 0.5) < 1e-9
    assert -0.0056 < y.min() - 0.5 < -0.0035, "passes rest by about -0.005"
    assert int((np.diff(np.sign(y - 0.5 + 1e-12)) != 0).sum()) <= 2
    assert all(abs(q - 0.5) < 1e-9 for q in y[9:]), "held after 9 frames"
    b = B.bounce_blur(14)
    assert b[0] == 10.0 and b[8] == 0.0 and all(x >= y_ for x, y_ in zip(b, b[1:]))


def test_curve_specs_bake_to_one_number_per_frame():
    assert B.resolve(0.5, 10) == 0.5 and B.resolve([1, 2], 2) == [1, 2]
    s = B.resolve({"curve": "settle", "norm": True}, 12)
    assert len(s) == 12 and s == B.settle(12, norm=True)
    d = B.resolve({"curve": "linear", "a": 0, "b": 1, "delay": 3, "step": 2}, 9)
    assert len(d) == 9 and d[:4] == [0, 0, 0, 0] and d[4] == d[5]
    m = B.resolve({"curve": "flicker", "mul": 0.5, "add": 0.1}, 3)
    assert m == [0.35, 0.55, 0.225]
    assert B.resolve({"curve": "grow", "k": 1.9}, 4) == [1.0, 1.9, 1.9 ** 2, 1.9 ** 3]
    with pytest.raises(KeyError):
        B.resolve({"curve": "wobble"}, 4)


# ------------------------------------------------------------------ E1: eligible, compile, plan

def test_eligible_is_false_for_anything_unknown():
    shots, dur = plates([30, 30])
    assert eligible(film(shots, dur)) == (True, [])
    assert eligible({"scene": film(shots, dur)})[0], "an EDL that carries a scene is read through it"
    cases = [
        (shots + [{"id": "x", "k": "pieces", "f": [0, 3]}], "unknown kind"),
        (shots + [{"id": "x", "k": "flash", "f": [0, 3], "mode": "sparkle"}], "unknown flash mode"),
        (shots + [word(0, 0, 9, blend="screen")], "unknown blend"),
        (shots + [{"id": "x", "k": "join", "f": [0, 3], "mode": "melt", "pic": "plate"}], "unknown join"),
        (shots + [word(0, 0, 9, op={"curve": "wobble"})], "unknown curve"),
        (shots[:1], "without exactly one base picture"),
        (shots + [dict(shots[0], id="again")], "without exactly one base picture"),
        (shots + [word(0, 50, 70)], "outside"),
        (shots + [dict(shots[0])], "repeated id"),
        ([dict(shots[0], plate={"kind": "lava"}), shots[1]], "unknown plate"),
        ([dict(shots[0], treat={"mode": "sepia9"}), shots[1]], "unknown treat"),
    ]
    for items, expect in cases:
        ok, why = eligible(film(items, dur))
        assert not ok and any(expect in w for w in why), (expect, why)
    assert eligible({"nothing": 1})[0] is False


@needs_hb
def test_same_input_same_plan_same_bytes(tmp_path):
    shots, words, dur = talk(12)
    words[3]["op"] = {"curve": "flicker"}
    words[5]["s"] = {"curve": "settle", "norm": True}
    words[5]["x"] = [0.3 + 0.001 * i for i in range(words[5]["f"][1] - words[5]["f"][0])]
    l1 = film(shots + words, dur)
    p1 = scene.compile_scene(json.loads(json.dumps(l1)), "/")
    p2 = scene.compile_scene(json.loads(json.dumps(l1)), "/")
    assert p1.sha == p2.sha and [s["sha"] for s in p1.segments] == [s["sha"] for s in p2.segments]
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    p1.save(a)
    p2.save(b)
    for name in ("plan.json", "curves.bin", "atlas.png"):
        assert open(os.path.join(a, name), "rb").read() == open(os.path.join(b, name), "rb").read(), name
    back = Plan.load(a, "/")
    assert back.sha == p1.sha and back.items == p1.items, "curves.bin gives back exactly what was drawn from memory"
    assert len(back.pages) == len(p1.pages) and all(np.array_equal(x, y) for x, y in zip(back.pages, p1.pages))
    moved = scene.compile_scene(dict(l1, seed=8), "/")
    assert moved.sha != p1.sha


@needs_hb
def test_every_frame_has_exactly_one_base_picture():
    shots, words, dur = talk(10)
    plan = scene.compile_scene(film(shots + words, dur), "/")
    cov = np.zeros(dur, int)
    for it in plan.items:
        if it["k"] == "shot":
            cov[it["f"][0]:it["f"][1]] += 1
    assert (cov == 1).all()
    ops = [s["ops"] for s in plan.doc["program"]]
    assert sum(o.get("plate", 0) + o.get("src", 0) for o in ops) == dur and sum(o["out"] for o in ops) == dur


@needs_hb
def test_a_budget_degrades_and_never_fails():
    shots, words, dur = talk(8)
    for w in words:
        w["blur"] = [8.0, 4.0, 0.0] + [0.0] * (w["f"][1] - w["f"][0] - 3)
    l1 = film(shots + words, dur, film_={"grain": 0.05, "bloom": 0.2, "halation": 0.2})
    free = scene.compile_scene(l1, "/")
    tight = scene.compile_scene(l1, "/", budget_s=0.001)
    assert free.doc["degraded"] == [] and tight.doc["degraded"][:2] == ["lens", "easu"]
    assert "bloom" in free.film and "bloom" not in tight.film and tight.doc["cost_s"] <= free.doc["cost_s"]


def test_a_fault_names_its_code():
    f = SceneFault("stall", "no progress", progress="x")
    assert f.code == "stall" and f.as_dict() == {"code": "stall", "detail": "no progress", "progress": "x"}
    assert SceneFault("made-up").code == "crash"
    with pytest.raises(SceneFault) as ex:
        scene.compile_scene(film([{"id": "x", "k": "hologram", "f": [0, 3]}], 3), "/")
    assert ex.value.code == "ineligible"


# ------------------------------------------------------------------ E7: segments

@needs_hb
def test_segments_cover_the_film_at_shots_one_and_a_half_to_four_seconds():
    shots, words, dur = talk(60)
    plan = scene.compile_scene(film(shots + words, dur), "/")
    segs = plan.segments
    assert segs[0]["f"][0] == 0 and segs[-1]["f"][1] == dur and all(a["f"][1] == b["f"][0] for a, b in zip(segs, segs[1:]))
    starts = {s["f"][0] for s in shots}
    assert all(45 <= s["f"][1] - s["f"][0] <= 120 for s in segs), [s["f"] for s in segs]
    assert all(s["f"][0] in starts for s in segs), "every boundary is a shot's first frame"
    assert segs[0]["hook"] and S.order(segs)[0]["i"] == 0
    long_shot, d2 = plates([400])
    cut = scene.compile_scene(film(long_shot, d2), "/").segments
    assert all(45 <= s["f"][1] - s["f"][0] <= 120 for s in cut) and len(cut) >= 4, "a shot longer than 4 s is cut inside"


def _ripple(items, at, n):
    """Lengthen the film by n frames at frame ``at``: what an edit upstream does to everything after it."""
    out = []
    for it in items:
        it = json.loads(json.dumps(it))
        a, b = it["f"]
        if a >= at:
            it["f"] = [a + n, b + n]
        elif b > at:
            it["f"] = [a, b + n]
        if "on" in it and it["on"] >= at:
            it["on"] += n
        out.append(it)
    return out


@needs_hb
def test_a_ripple_edit_keeps_later_hashes():
    shots, words, dur = talk(60)
    before = scene.compile_scene(film(shots + words, dur), "/")
    victim = next(s for s in shots if s["f"][0] > 200)                      # make one early shot 20 frames longer
    words = [w for w in words if not (w["f"][0] < victim["f"][1] and w["f"][1] > victim["f"][0])]   # keep words off the stretched shot
    before = scene.compile_scene(film(shots + words, dur), "/")
    after = scene.compile_scene(film(_ripple(shots + words, victim["f"][0] + 1, 20), dur + 20), "/")
    old = {s["sha"] for s in before.segments}
    kept = [s for s in after.segments if s["sha"] in old]
    changed = [s for s in after.segments if s["sha"] not in old]
    assert len(changed) <= 2, [s["f"] for s in changed]
    late = [s for s in after.segments if s["f"][0] > victim["f"][1] + 20 + 120]
    assert late and all(s in kept for s in late), "segments after the edit keep their hashes although every frame number moved"


@needs_hb
def test_a_one_word_change_redraws_at_most_two_segments_of_a_minute():
    shots, words, dur = talk(60)
    assert dur >= 1800
    before = scene.compile_scene(film(shots + words, dur), "/")
    for pick in (7, len(words) // 2, len(words) - 3):
        w2 = json.loads(json.dumps(words))
        w2[pick]["t"] = "outra"
        after = scene.compile_scene(film(shots + w2, dur), "/")
        diff = [a["i"] for a, b in zip(after.segments, before.segments) if a["sha"] != b["sha"]]
        assert [s["f"] for s in after.segments] == [s["f"] for s in before.segments]
        assert 1 <= len(diff) <= 2, diff


def test_the_cache_ages_out_and_holds_its_cap(tmp_path):
    root = str(tmp_path)
    now = 1_000_000_000.0
    paths = []
    for i in range(6):
        p = S.seg_path(root, f"sha{i}", "enc")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"x" * 1000)
        os.utime(p, (now - i * 86400, now - i * 86400))                     # 0..5 days since last use
        paths.append(p)
    gone, left = S.sweep(root, cap=10_000, max_age_s=3.5 * 86400, now=now)
    assert gone == 2 and left == 4000 and not os.path.exists(paths[5]) and os.path.exists(paths[3])
    gone, left = S.sweep(root, cap=2500, max_age_s=99 * 86400, now=now)
    assert left == 2000 and os.path.exists(paths[0]) and os.path.exists(paths[1]) and not os.path.exists(paths[2]), "the oldest use goes first"
    assert S.seg_path(root, "a", "x264-veryfast") != S.seg_path(root, "a", "x264-superfast"), "an encode is part of a segment's name"


@needs_ffmpeg
def test_concat_with_stream_copy_keeps_every_frame_and_takes_the_sound(tmp_path):
    import scene.audio as AU
    import scene.run as RUN
    w, h, lens = 96, 160, [20, 31, 17]
    parts, k = [], 0
    for i, n in enumerate(lens):
        p = str(tmp_path / f"s{i}.mp4")
        enc = RUN.encoder(p, w, h, FPS, "final")
        for _ in range(n):
            fr = np.zeros((h, w, 4), np.uint8)
            fr[..., 0] = k * 3 % 256
            fr[(k * 2) % h, :, 1] = 255
            fr[..., 3] = 255
            enc.stdin.write(fr.tobytes())
            k += 1
        enc.stdin.close()
        assert enc.wait() == 0
        S.verify(p, n, FPS, mean_out=60.0)
        parts.append(p)
    with pytest.raises(SceneFault):
        S.verify(parts[0], lens[0] + 1, FPS)
    with pytest.raises(SceneFault):
        S.verify(parts[0], lens[0], FPS, mean_out=0.5, mean_src=90.0)        # black where the source is not
    S.verify(parts[0], lens[0], FPS, mean_out=0.5, planned_dark=True)       # a plate may be black
    S.verify(parts[0], lens[0], FPS, mean_out=0.5, mean_src=2.0)            # and so may a black source
    joined = S.concat(parts, str(tmp_path / "v.mp4"))
    n, dur = S.count(joined)
    assert n == sum(lens) and abs(dur - sum(lens) / FPS) < 0.5 / FPS
    r = subprocess.run([S.FP, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", joined],
                       capture_output=True, text=True)
    pts = sorted(float(x.strip(",")) for x in r.stdout.split())
    assert len(pts) == sum(lens) and np.allclose(np.diff(pts), 1 / FPS, atol=1e-3), "even gaps, no frame lost at a join"
    a = str(tmp_path / "a.m4a")
    subprocess.run([S.FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=440:d={sum(lens) / FPS}", "-c:a", "aac", a], check=True)
    out = str(tmp_path / "o.mp4")
    AU.mux(joined, a, out)
    assert S.count(out)[0] == sum(lens)
    q = subprocess.run([S.FP, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out], capture_output=True, text=True)
    assert sorted(q.stdout.split()) == ["audio", "video"]


@needs_hb
def test_grain_clock_belongs_to_the_segment():
    """Grain is seeded per segment by the shot it starts on, so a ripple upstream moves no grain."""
    shots, words, dur = talk(20)
    a = scene.compile_scene(film(shots, dur), "/")
    b = scene.compile_scene(film(_ripple(shots, shots[1]["f"][0] + 1, 9), dur + 9), "/")
    sa, sb = a.segments[-1], b.segments[-1]
    assert sa["key"] == sb["key"] and sa["f"][0] + 9 == sb["f"][0]
    assert [a.noise_at(sa["f"][0] + i) for i in range(20)] == [b.noise_at(sb["f"][0] + i) for i in range(20)]
    whole = scene.compile_scene(film(shots, dur, seed=7), "/", segment=False)
    rng = np.random.RandomState(7 ^ 0x5EED)
    rng.standard_normal((1024, 1024))
    ofs = rng.randint(0, 1024, size=(dur + 2, 2))
    assert whole.noise_at(5) == tuple(int(x) for x in ofs[5]) and whole.noise_at(5, 1) == tuple(int(x) for x in ofs[6][::-1])


# ------------------------------------------------------------------ T2: type

@needs_hb
def test_coverage_is_exact_area_and_the_distance_field_is_true():
    import scene.type_atlas as TA
    sq = np.array([[2.5, 2.25, 6.5, 2.25], [6.5, 2.25, 6.5, 7.0], [6.5, 7.0, 2.5, 7.0], [2.5, 7.0, 2.5, 2.25]], float)
    cov = TA.coverage(sq, 10, 10)
    assert abs(cov.sum() - 4.0 * 4.75) < 1e-5 and cov[4, 4] == 1.0 and abs(cov[2, 2] - 0.5 * 0.75) < 1e-6
    big = np.zeros((64, 64), np.float32)
    big[16:48, 16:48] = 1.0
    d = TA.sdf(big, 8.0)
    assert abs(d[32, 32] - 1.0) < 1e-6 and d[2, 2] == 0.0
    assert abs(d[32, 16] - (0.5 + 0.5 / 16)) < 0.01 and abs(d[32, 15] - (0.5 - 0.5 / 16)) < 0.01, "0.5 lies on the outline"
    assert abs(d[32, 12] - (0.5 - 3.5 / 16)) < 0.01 and abs(d[32, 20] - (0.5 + 4.5 / 16)) < 0.01, "and it is linear in distance"


@needs_hb
def test_sprites_pack_on_the_grid_without_touching():
    import scene.type_atlas as TA
    sprites = {f"k{i}": TA.build_sprite(t, VOICE["a"], 1080, ss, 0.0) for i, (t, ss) in
               enumerate([("chegando", 2), ("NOVO", 4), ("i", 2), ("um texto bem mais comprido", 2), ("?", 8)])}
    pages, meta = TA.pack(sprites)
    assert len(pages) == 1 and pages[0].shape[2] == 4
    boxes = []
    for k, m in meta.items():
        arr = sprites[k][0]
        assert m["ax"] % 8 == 0 and m["ay"] % 8 == 0 and (m["aw"], m["ah"]) == (arr.shape[1], arr.shape[0])
        assert np.array_equal(pages[0][m["ay"]:m["ay"] + m["ah"], m["ax"]:m["ax"] + m["aw"]], arr)
        boxes.append((m["ax"], m["ay"], m["ax"] + m["aw"], m["ay"] + m["ah"]))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] + 16 <= b[0] or b[2] + 16 <= a[0] or a[3] + 16 <= b[1] or b[3] + 16 <= a[1], "a gutter between sprites"
    arr, m = sprites["k0"]
    assert arr[..., 0].max() == 255 and arr[..., 1].max() > 100 and arr[..., 2].max() == 0, "coverage, a shadow copy, no glow asked"
    assert arr[..., 3].max() == 255 and 100 < arr[..., 3][arr[..., 0] > 250].min(), "the distance field is past 0.5 inside the letters"


@needs_hb
def test_a_line_of_two_voices_on_one_baseline_and_the_four_reflow_rules():
    import scene.type as ST
    v = {"a": dict(VOICE["a"], shaped=True), "b": dict(VOICE["b"], shaped=True)}
    runs = [("a", ["the", "most"]), ("b", ["important"]), ("a", ["photo"])]
    ons = [10, 14, 20, 27]
    W, H = 1080, 1920
    pre = ST.lay_line(runs, v, (W, H), y=0.5, f=(10, 60), ons=ons, rule="pre_laid")
    assert [i["t"] for i in pre] == ["the", "most", "important", "photo"] and [i["v"] for i in pre] == ["a", "a", "b", "a"]
    assert {i["y"] for i in pre} == {0.5}, "one baseline for both voices"
    assert [i["f"][0] for i in pre] == ons and all(isinstance(i["x"], float) for i in pre), "each word holds its place from the frame it lands"
    xs = [i["x"] * W for i in pre]
    wid = [ST.Face.of(v[i["v"]]).measure(i["t"], v[i["v"]]["px"], v[i["v"]]["track"]) for i in pre]
    assert all(b > a + w for a, b, w in zip(xs, xs[1:], wid)), "words in reading order with a gap"
    assert abs((xs[0] + xs[-1] + wid[-1]) / 2 - W / 2) < 0.5, "the finished line is centred"
    car = ST.carets(pre, v, (W, H))
    assert all(abs(c - (x + w)) < 1e-6 for c, x, w in zip(car, xs, wid))

    flow = ST.lay_line(runs, v, (W, H), y=0.5, f=(10, 60), ons=ons, rule="reflow_centre")
    first = flow[0]["x"]
    assert isinstance(first, list) and len(first) == 50 and first[0] > first[-1], "the line grows from its middle, so the first word moves left"
    assert abs(first[0] * W + wid[0] / 2 - W / 2) < 0.5 and abs(first[-1] - pre[0]["x"]) < 1e-9
    assert len(set(first)) == 4, "it jumps when a word lands and holds between"

    ghost = ST.lay_line(runs, v, (W, H), y=0.5, f=(10, 60), ons=ons, rule="ghost", ghost=0.26)
    assert all(i["f"] == [10, 60] for i in ghost), "the whole line is laid before it is spoken"
    assert ghost[0]["op"] == 1.0 and ghost[2]["op"][:10] == [0.26] * 10 and ghost[2]["op"][10] == 1.0

    just = ST.lay_line(runs, v, (W, H), y=0.5, f=(10, 60), ons=ons, rule="justify_on_fill", fill=0.9)
    last = just[-1]["x"]
    assert abs(last * W + wid[-1] - (W / 2 + 0.45 * W)) < 0.5, "once the last word lands the line fills the width"
    assert isinstance(just[0]["x"], list) and just[0]["x"][0] == pre[0]["x"] and just[0]["x"][-1] < pre[0]["x"]


@needs_hb
def test_tiers_follow_the_frame_and_a_family_resolves_through_the_typeset_registry():
    import scene.type as ST
    assert abs(ST.tier_px("caption", 1920, 1080) - 57.6) < 1e-6 and ST.tier_px("micro", 1920, 1080) < ST.tier_px("caption", 1920, 1080)
    assert 1.8 < ST.tier_px("caption", 1080, 1920) / (ST.tier_px("caption", 1920, 1080) * 1080 / 1920) < 2.1, "landscape captions are about twice the share"
    from typeset import shaping
    fam = sorted(shaping.families())[0]
    path, var = ST.voice_font({"family": fam, "weight": 700})
    assert os.path.exists(path)
    f = ST.Face.of({"family": fam, "weight": 700})
    assert f.measure("Valmera", 100) > 100 and len(f.quads("Valmera", 100)) == 7
    asc, desc, cap = f.metrics(100)
    assert asc > cap > 40 and desc < 0


@needs_hb
def test_right_to_left_lines_are_laid_right_to_left():
    """RTL golden on p5297's own words: shaped in visual order, and the first word spoken sits rightmost."""
    import scene.type as ST
    try:
        ST.find_font("NotoSansArabic-Bold.ttf")
    except FileNotFoundError:
        pytest.skip("no Arabic-script font on this machine")
    v = {"a": {"font": "NotoSansArabic-Bold.ttf", "px": 96, "track": 0, "shaped": True}}
    words = ["غيّر", "لون", "السيارة"]
    line = ST.lay_line([("a", words)], v, (1080, 1920), y=0.5, f=(0, 30), rule="pre_laid")
    xs = [i["x"] for i in line]
    assert xs[0] > xs[1] > xs[2], "reading order runs right to left"
    f = ST.Face.of(v["a"])
    glyphs, adv = f.shape(words[2], 96)
    assert [g[1] for g in glyphs] == sorted(g[1] for g in glyphs), "glyphs come back in visual order: x only rises"
    joined = [g[0] for g in glyphs]
    alone = [f.shape(c, 96)[0][0][0] for c in words[2]]
    assert joined != alone and joined[::-1] != alone, "contextual forms, not the isolated letters"
    golden = json.load(open(os.path.join(HERE, "scene_golden", "rtl_p5297.json"), encoding="utf-8"))
    import hashlib
    if hashlib.sha256(open(f.path, "rb").read()).hexdigest()[:16] != golden["font_sha"]:
        return                                   # another cut of the font: the structure above is the test
    for w in words:
        got = {"glyphs": len(f.shape(w, 96)[0]), "adv_em": round(f.measure(w, 1000) / 1000, 3)}
        assert got == golden["words"][w], (w, got, golden["words"][w])

@needs_hb
def test_tiny_type_is_truer_from_coverage_than_from_the_distance_field():
    """Why there are two rasters (DESIGN 4.6): at caption size on a phone, 0.03 h, the outline read
    back from a distance field loses to plain coverage. Both are compared with the same word drawn
    at sixteen times the size and averaged down."""
    import scene.type as ST
    import scene.type_atlas as TA
    face = ST.Face.get("InterDisplay-Bold.ttf")
    errs = {"coverage": [], "field": []}
    for text in ("chegando", "armário", "Valmera"):
        px = 0.03 * 844                                         # a caption on a phone 844 points tall
        lines, _ = face.outline(text, px * 2)
        x0, y0 = math.floor(lines[:, [0, 2]].min()) - 8, math.floor(lines[:, [1, 3]].min()) - 8
        w, h = int(math.ceil(lines[:, [0, 2]].max())) - x0 + 8, int(math.ceil(lines[:, [1, 3]].max())) - y0 + 8
        w, h = w + w % 2, h + h % 2
        ln = lines.copy()
        ln[:, [0, 2]] -= x0
        ln[:, [1, 3]] -= y0
        cov2 = TA.coverage(ln, w, h)                            # what the atlas holds: twice the target size
        truth = TA.coverage(ln * 8, w * 8, h * 8).reshape(h // 2, 16, w // 2, 16).mean(axis=(1, 3))
        from_cov = cov2.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
        d = (TA.sdf(cov2, 8.0) - 0.5) * 16.0                    # texels, positive inside
        from_field = np.clip(d + 0.5, 0, 1).reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
        errs["coverage"].append(float(np.abs(from_cov - truth).mean()))
        errs["field"].append(float(np.abs(from_field - truth).mean()))
    assert max(errs["coverage"]) < 1e-3, errs
    assert min(errs["field"]) > 4 * max(errs["coverage"]), errs
