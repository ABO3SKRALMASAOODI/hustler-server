"""The supply that never leaves a moment bare (creativity lane B, 20 Nov 2026): every picture the supply holds rides the
scene as the palette's pool (a clip the film did not place loses another editor's seated text first), a contradicted
first candidate gives its window the next one, and a picture of what the words mean is a workflow's own choice. (The
per-window chooser that ranks association, type-only beats and take treatments as alternatives is not built yet.)"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import placement_check as pc  # noqa: E402
import wf1_coverage as cov  # noqa: E402


def _files(tmp_path, names):
    out = {}
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"x")
        out[n] = str(p)
    return out


def _pack(tmp_path):
    f = _files(tmp_path, ["a.mp4", "b.mp4", "dull.mp4", "c.mp4", "p.jpg", "q.jpg", "cleared.mp4"])
    windows = [dict(first=0, last=3, name="Mark Cuban", t0=0.0, t1=2.0, say="w", must_show="m"),
               dict(first=4, last=9, name="Shark Tank", t0=3.0, t1=6.0, say="w", must_show="s")]
    footage = [dict(i=0, clips=[dict(path=f["a.mp4"], seconds=1.6, w=1920, h=1080, x=0.62, subject="Mark Cuban", shows="close-up",
                                     faces=[dict(box=[0.5, 0.2, 0.7, 0.5], eyes=[[0.55, 0.3], [0.65, 0.3]])]),
                                dict(path=f["dull.mp4"], seconds=1.6, dull=True)]),
               dict(i=1, clips=[dict(path=f["b.mp4"], seconds=1.5, subject="Shark Tank"), dict(path=f["c.mp4"], seconds=1.5),
                                dict(path=str(tmp_path / "gone.mp4"), seconds=1.0)])]
    pictures = {"Shark Tank": [dict(path=f["p.jpg"], w=1200, h=800, title="The set")], "Elsewhere": [dict(path=f["q.jpg"], w=800, h=800)]}
    return dict(coverage=dict(windows=windows), footage=footage, pictures=pictures), f


def test_every_supply_picture_reaches_the_pool_with_what_was_measured_of_it(tmp_path):
    pack, f = _pack(tmp_path)
    report = dict(rejected_paths=[f["c.mp4"]], text_cleared=[dict(from_path=f["b.mp4"], path=f["cleared.mp4"])])
    pool = cov.device_pool(pack, report)
    by = {os.path.basename(r["path"]): r for r in pool}
    assert set(by) == {"a.mp4", "cleared.mp4", "p.jpg", "q.jpg"}          # dull, contradicted and missing pictures stay out
    a = by["a.mp4"]
    assert a["kind"] == "clip" and a["seconds"] == 1.6 and a["x"] == 0.62 and a["faces"] and a["window"] == 0
    assert a["t"] == [0.1, 2.1]                                             # programme seconds of its window (the lead in)
    assert by["cleared.mp4"]["window"] == 1 and "w" not in by["cleared.mp4"]      # the cleared copy, sized from its own file
    assert by["p.jpg"]["kind"] == "photo" and by["p.jpg"]["window"] == 1 and by["q.jpg"]["t"] is None
    assert len({r["id"] for r in pool}) == len(pool)


def test_a_contradicted_first_candidate_gives_its_window_the_next_one(tmp_path):
    f = _files(tmp_path, [f"w{i}c{k}.jpg" for i in range(3) for k in range(3)])
    windows = [dict(first=i * 4, last=i * 4 + 3, name=f"S{i}", t0=i * 3.0, t1=i * 3.0 + 2.5, say=f"words {i}", must_show="x") for i in range(3)]
    pictures = {f"S{i}": [dict(path=f[f"w{i}c{k}.jpg"], w=800, h=800) for k in range(3)] for i in range(3)}

    def checker(rows):
        # the first-ranked candidate of every window contradicts its words: each window takes its next one
        out = []
        for r in rows:
            bad = r[4]["path"].endswith("c0.jpg")
            out.append(dict(ok=not bad, fit=8 if not bad else 0, subject_x=None, motion_ok=False))
        return [r for r, d in zip(rows, out) if d["ok"]], dict(placements=out, spend=[])
    ins, report = cov.checked_proposals(dict(windows=windows), {}, pictures, 12.0, checker)
    assert {r[4]["window"] for r in ins} == {0, 1, 2}
    assert not any(r[4]["path"].endswith("c0.jpg") for r in ins)


def test_a_private_claim_can_have_a_finished_picture_plan_without_external_windows(tmp_path):
    import json
    from types import SimpleNamespace
    calls = []
    def plan_once(system, content, **kwargs):
        calls.append(content)
        return json.dumps(dict(entities=[dict(name="Investors", word_ids=[1])], windows=[], moves=[])), {}
    words = [dict(w=w, start=i, end=i+1) for i, w in enumerate("those investors passed".split())]
    result = cov.plan(words, "Fundraising", str(tmp_path),
                      SimpleNamespace(ask=plan_once, json_block=json.loads))
    assert result["windows"] == [] and len(calls) == 1
    assert cov.require_coverage([], result, 3)["real_seconds"] == 0


FF = os.environ.get("FFMPEG_BIN") or "/Users/masaoodi/Documents/Valmera/.valmera/week-1106/gauntlet/tools/ff/ffmpeg"


def _label_clip(path):
    """A moving picture with another channel's lower third seated on it for the whole cut."""
    import subprocess
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1920, 1080
    p = subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "30", "-i", "-",
                          "-c:v", "libx264", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 90)
    except OSError:
        font = ImageFont.load_default()
    base = (np.random.default_rng(1).random((H // 24, W // 24, 3)) * 255).astype(np.uint8)
    for i in range(45):
        im = Image.fromarray(np.roll(np.kron(base, np.ones((24, 24, 1), np.uint8)), i * 18, axis=1))
        d = ImageDraw.Draw(im)
        d.rectangle([90, 840, 1260, 990], fill=(10, 10, 10))
        d.text((120, 855), "TALKS ABOUT TANK", font=font, fill=(255, 255, 255))
        p.stdin.write(np.asarray(im).tobytes())
    p.stdin.close()
    p.wait()
    return path


def test_an_unplaced_clip_loses_another_editors_seated_text_before_a_device_uses_it(tmp_path):
    import pytest
    if not os.path.exists(FF):
        pytest.skip("no ffmpeg")
    clip = _label_clip(str(tmp_path / "tank.mp4"))
    pack = dict(coverage=dict(windows=[dict(first=0, last=3, name="Shark Tank", t0=0.0, t1=2.0)]),
                footage=[dict(i=0, clips=[dict(path=clip, seconds=1.5, w=1920, h=1080, subject="Shark Tank")])], pictures={})
    work = tmp_path / "supply"
    work.mkdir()
    pool = cov.device_pool(pack, {}, work=str(work), ffmpeg=FF, placed=set())
    r = pool[0]
    assert r["path"] != clip and r["from_path"] == clip and "text-cleared" in r["path"] and os.path.isfile(r["path"])
    assert r["h"] < 1080 * 0.85 and not r.get("text_kept")                  # cut down to the band above the lower third
    assert json.load(open(work / "pool_text.json"))                         # measured once
    again = cov.device_pool(pack, {}, work=str(work), ffmpeg=FF, placed=set())
    assert again[0]["path"] == r["path"]
    placed = cov.device_pool(pack, {}, work=str(work), ffmpeg=FF, placed={clip})
    assert placed[0]["path"] == r["path"]           # a placed clip's lower third goes too: a device shows it beside our own label


def test_a_clip_whose_text_cannot_be_cut_away_is_offered_last_not_lost(tmp_path, monkeypatch):
    f = _files(tmp_path, ["a.mp4"])
    monkeypatch.setattr(pc, "clear_seated_text", lambda rec, ffmpeg, work: (dict(rec, text_kept="label"), "text_kept"))
    pack = dict(coverage=dict(windows=[]), footage=[dict(i=None, clips=[dict(path=f["a.mp4"], seconds=1.5)])], pictures={})
    pool = cov.device_pool(pack, {}, work=str(tmp_path), placed=set())
    assert len(pool) == 1 and pool[0]["text_kept"] == "label"


def test_a_lower_third_on_a_still_shot_is_found_by_its_still_band(tmp_path):
    """An interview with a channel's lower third (a bar and a logo beside the words): the bar holds pixel for pixel while
    the picture moves round it, and the clip is cut down above it."""
    import pytest
    import subprocess
    import numpy as np
    if not os.path.exists(FF):
        pytest.skip("no ffmpeg")
    W, H = 1920, 1080
    path = str(tmp_path / "talk.mp4")
    p = subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "30", "-i", "-",
                          "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    rng = np.random.default_rng(2)
    wall = (rng.random((H // 40, W // 40, 3)) * 120 + 40).astype(np.uint8)
    bar = np.zeros((130, W, 3), np.uint8)
    bar[:] = (40, 20, 70)
    bar[30:100:10, 200:900] = 255                                       # rows of type on the bar
    bar[20:110, 1500:1520] = 230                                        # the channel's logo block
    for i in range(45):
        im = np.roll(np.kron(wall, np.ones((40, 40, 1), np.uint8)), i * 7, axis=1).copy()      # a handheld camera drifting
        x = 700 + int(60 * np.sin(i / 5))
        im[250:850, x:x + 420] = (200, 160, 140)                        # the speaker moving
        im[900:1030] = bar
        p.stdin.write(im.tobytes())
    p.stdin.close()
    p.wait()
    overlay = pc._overlay()
    bands = pc.static_bands(overlay, path)
    assert bands and 0.82 <= min(b[1] for b in bands) <= 0.84 and 0.94 <= max(b[3] for b in bands) <= 0.97
    new, how = pc.clear_seated_text(dict(path=path, w=W, h=H), FF, str(tmp_path))
    assert how == "cleared" and new["text_cleared"]["rect"][3] <= 0.84 and os.path.isfile(new["path"])
