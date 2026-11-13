"""The earned-pictures plan pass on a small plan: no network, no model, a synthetic clip.

    $PY -m pytest -q plugins/valmera/skills/valmera-podcast-shorts/scripts/test_earned.py
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "alien"))

import earned as E  # noqa: E402
import l1pass as L  # noqa: E402
import sidecars as S  # noqa: E402

FPS, N = 30, 660
CROP = [0.3418, 0.0, 0.6582, 1.0]


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("clip")
    p = str(d / "source.mp4")
    subprocess.run([S.FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r={FPS}:d=22", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=22", "-af", "volume='0.2+0.8*lt(mod(t,1),0.5)':eval=frame", "-c:v", "libx264", "-preset",
                    "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", p], check=True)
    from PIL import Image
    os.makedirs(d / "earned")
    Image.new("RGB", (900, 1400), (120, 90, 60)).save(d / "earned" / "0.jpg")
    Image.new("RGB", (1800, 1000), (40, 80, 160)).save(d / "earned" / "1.jpg")
    Image.new("RGB", (1000, 1200), (200, 200, 190)).save(d / "earned" / "3.jpg")
    return str(d)


def words():
    out = []
    for i in range(60):
        out.append(dict(w=f"w{i}", t0=round(0.2 + i * 0.35, 3), t1=round(0.2 + i * 0.35 + 0.25, 3)))
    return out


def plan():
    items = [dict(id="b01.s0", k="shot", f=[0, 300], pic="main", src=[0.0, 10.0], crop=list(CROP)),
             dict(id="b02.s0", k="shot", f=[300, 360], pic="bin:2", crop=[0, 0, 1, 1]),
             dict(id="b02.s1", k="shot", f=[360, N], pic="main", src=[12.0, 22.0], crop=list(CROP)),
             dict(id="b01.big.0", k="word", f=[0, 40], t="HELLO", v="a", x=0.1, y=0.3, ink="#FFFFFF", role="big", on=0),
             dict(id="b03.chip", k="card", f=[20, 80], z=1, pic="plate", plate=dict(c0="#000000", c1="#000000"), x=0.5, y=0.74, w=0.3, h=0.04, s=1, rot=0, op=1),
             dict(id="b03.chip.0", k="word", f=[20, 80], t="Mark", v="a", x=0.4, y=0.75, ink="#FFFFFF", role="big", on=20),
             dict(id="b05.flash.0", k="flash", f=[100, 102], mode="fill", c="#FFFFFF")]
    for i, w in enumerate(words()):
        f = int(w["t0"] * FPS)
        items.append(dict(id=f"b01.c{i // 3}.w{i}", k="word", f=[f, f + 30], t=w["w"], v="a", x=0.3 + 0.1 * (i % 3), y=0.62, ink="#FFFFFF", role="cap",
                          chunk=f"b01.c{i // 3}", on=f))
    return dict(l1=1, sid="t", seed=1, fps=FPS, frame=[1080, 1920], dur_f=N,
                assets={"main": dict(kind="video", path="source.mp4", w=640, h=360, dur=22.0, fps=FPS), "bin:2": dict(kind="image", path="bin/2.jpg", w=10, h=10)},
                spine=[dict(f=[0, N], src=[0.0, 22.0])], voices={"a": dict(font="InterDisplay-Bold.ttf", px=46, track=-2)},
                film=dict(grain=0, vignette=0), items=sorted(items, key=lambda it: it["f"][0]),
                sound=[dict(id="b01.snd0", s="tick_1", file="tick_1.flac", event_f=10, start_s=0.3, trim_s=0, db=-20, peak_s=0.02)])


SCRIPT = dict(window=[100.0, 122.0], accent="#FFC857", hook=dict(picture=0, line="One day that light didn't exist", accent="light"),
              payoff=dict(on="w50 w51", at=117.7),
              pictures=[dict(on="w2", at=100.9, kind="named", name="A"), dict(on="w14 w15", at=105.1, hold=2.0, kind="named", name="B"),
                        dict(on="w22", at=107.9, hold=2.2, kind="design", design=dict(kind="versus", title="what it cost", parts=[
                            dict(value="$169K", label="with the PBM", n=169), dict(value="$19K", label="at cost plus", n=19)])),
                        dict(on="w30", at=110.7, hold=1.5, kind="thing", name="C"),
                        dict(on="w38", at=113.5, hold=2.0, kind="design", design=dict(kind="document", title="the email", lines=[
                            "Mark, would you look at this", "I still have the emails", "Never came back to me"], mark="I still have the emails")),
                        dict(on="w44", at=115.6, kind="named", name="nothing found")])
EARNED = dict(rows=[dict(i=0, ok=True, name="A", path="earned/0.jpg", w=900, h=1400, faces=[[0.4, 0.2, 0.2, 0.15]], box=[0.3, 0.1, 0.7, 0.6]),
                    dict(i=1, ok=True, name="B", path="earned/1.jpg", w=1800, h=1000, faces=[], box=[0.05, 0.1, 0.95, 0.9]),
                    dict(i=3, ok=True, name="C", path="earned/3.jpg", w=1000, h=1200, faces=[], box=[0.3, 0.1, 0.7, 0.9]),
                    dict(i=5, ok=False, name="nothing found", said="none")], credits=[], said=["none"])


def manifest():
    d = os.path.dirname(HERE)
    for _ in range(6):
        d = os.path.dirname(d)
        p = os.path.join(d, "worker", "sound_library", "manifest.json")
        if os.path.exists(p):
            return json.load(open(p))
    raise RuntimeError("no sound library")


def run(clip, **kw):
    l1 = plan()
    idx = dict(words=words(), video=dict(duration=22.0))
    rec = E.apply(l1, SCRIPT, EARNED, idx, os.path.join(clip, "source.mp4"), manifest(), **kw)
    return l1, rec


def test_one_shot_on_every_frame_and_lists_fit(clip):
    l1, rec = run(clip)
    cov = [0] * N
    for it in l1["items"]:
        a, b = it["f"]
        assert 0 <= a < b <= N, it["id"]
        if it["k"] == "shot":
            for f in range(a, b):
                cov[f] += 1
        for key, v in it.items():
            if key in ("f", "src") or not isinstance(v, list):
                continue
            if key in ("crop", "rect") and not isinstance(v[0], list):
                continue
            assert len(v) == b - a, (it["id"], key, len(v), b - a)
    assert set(cov) == {1}
    ids = [it["id"] for it in l1["items"]]
    assert len(ids) == len(set(ids))


def test_the_hook_is_a_picture_on_frame_zero_with_its_line_sharp(clip):
    l1, rec = run(clip)
    first = next(it for it in l1["items"] if it["k"] == "shot" and it["f"][0] == 0)
    assert first["pic"] == "earned:0" and rec["hook"]["picture"] == 0
    hook = [it for it in l1["items"] if it["id"].startswith("hook.") and it["k"] == "word"]
    assert hook and all(it["f"][0] == 0 for it in hook)
    for it in hook:                                               # at rest and sharp on frame 0: no blur, no fade, scale 1
        assert "blur" not in it and "op" not in it
        assert L._span(it.get("s", 1))[0] >= 1.0 and (not isinstance(it.get("s"), list) or it["s"][0] == 1.0)
    assert any(it["v"] == "hka" and it["ink"] == "#FFC857" for it in hook)
    assert not L.outside_safe(l1)


def test_the_directors_extras_leave_and_the_chip_waits_for_the_take(clip):
    l1, rec = run(clip)
    assert rec["stripped"] == dict(cards=0, display=1, flashes=1, sounds=1, pictures=1)
    assert not any(it.get("pic") == "bin:2" for it in l1["items"])
    assert not any(it["id"] == "b01.big.0" for it in l1["items"])
    chip = [it for it in l1["items"] if it["id"].startswith("b03.chip")]
    assert len(chip) == 2 and all(it["f"][0] >= rec["hook_frames"] for it in chip)
    pics = L.picture_shots(l1)
    assert not any(a < chip[0]["f"][1] and chip[0]["f"][0] < b for a, b in pics)


def test_pictures_land_on_their_words_and_what_was_not_found_is_not_shown(clip):
    l1, rec = run(clip)
    by = {p["i"]: p for p in rec["pictures"]}
    assert set(by) == {1, 3}
    assert by[1]["frames"][0] == int(round((0.2 + 14 * 0.35) * FPS)) - 2
    assert by[1]["layout"] == "card" and any(it["id"] == "e1.card" for it in l1["items"])
    assert {d["i"] for d in rec["designs"]} == {2, 4}
    assert dict(i=5, why="no picture passed the check") in rec["skipped"]
    assert rec["ok"] and rec["new_pictures"] >= 4
    card = next(it for it in l1["items"] if it["id"] == "e1.card")
    box = L.card_box(card)
    assert box[1] >= 0.30 and box[3] <= 0.80 and box[0] >= 0.05 and box[2] <= 0.95      # under his chin, inside the safe area
    a, b = by[1]["frames"]
    under = [it for it in l1["items"] if it["k"] == "shot" and it["f"][0] < b and it["f"][1] > a]
    assert under and all(it["pic"] == "main" for it in under)           # the take goes on under a card
    assert isinstance(card["s"], list) and card["s"][0] < 1.0 and card["s"][-1] == 1.0
    for key, ws in L.units(l1).items():                                 # no caption stands on the card while it is there
        if all(w.get("role") == "cap" for w in ws) and min(w["f"][1] for w in ws) > a and max(w["f"][0] for w in ws) < b:
            cb = L.unit_box(ws, l1)
            assert cb[1] >= box[3] or cb[3] <= box[1], key
            assert cb[3] <= 0.80


def test_nothing_after_the_payoff_but_his_own_footage_closer(clip):
    l1, rec = run(clip)
    pay = rec["own"]["frame"]
    assert all(b <= pay for a, b in L.picture_shots(l1))
    tail = [it for it in l1["items"] if it["k"] == "shot" and it["f"][0] >= pay]
    assert tail and all(it["pic"] == "main" for it in tail)
    c = tail[0]["crop"]
    assert abs((CROP[2] - CROP[0]) / (c[2] - c[0]) - E.PUNCH) < 0.01 and 0 <= c[0] and c[2] <= 1 and 0 <= c[1] and c[3] <= 1
    src = tail[0]["src"]
    assert abs(src[0] - (tail[0]["f"][0] / FPS)) < 0.02                    # still in sync with the spine
    l1b, recb = run(clip, own=False)
    assert recb["own"] is None and len(L.take_stretches(l1b)) >= 1


def test_sounds_only_on_picture_events_thinned_and_under_the_voice(clip):
    l1, rec = run(clip)
    snd = l1["sound"]
    lib = {s["file"] for s in manifest()}
    assert snd and all(s["file"] in lib for s in snd)
    events = {p["frames"][0] for p in rec["pictures"]} | {d["frames"][0] + 2 for d in rec["designs"]} | {rec["own"]["frame"]}
    assert all(s["event_f"] in events for s in snd)
    assert len(snd) <= min(rec["sound"]["allowed"], 2) and all(s["event_f"] > 30 for s in snd)     # the owner's two, and none on the opening
    assert {s["role"] for s in snd} == {"payoff", "design"}
    assert all(s["db"] <= 0 for s in snd)
    by = {}
    for s in snd:
        by.setdefault(s["s"], []).append(s["event_f"])
    assert all(b - a >= 3 * FPS for fs in by.values() for a, b in zip(sorted(fs), sorted(fs)[1:]))


def test_a_design_panel_moves_and_holds_only_his_words(clip):
    l1, rec = run(clip)
    panel = next(it for it in l1["items"] if it["id"] == "e2.panel")
    assert isinstance(panel["s"], list) and panel["s"][0] < 1.0 and panel["s"][-1] == 1.0
    bars = [it for it in l1["items"] if it["id"].startswith("e2.bar")]
    assert len(bars) == 2
    w0 = [r[2] - r[0] for r in bars[0]["rect"]]
    w1 = [r[2] - r[0] for r in bars[1]["rect"]]
    assert w0[0] < w0[-1] and abs(w1[-1] / w0[-1] - 19 / 169) < 0.02       # the bars end in proportion
    texts = {it["t"] for it in l1["items"] if it["id"].startswith("e2.") and it["k"] == "word"}
    assert {"$169K", "$19K"} <= texts
    doc = {it["t"] for it in l1["items"] if it["id"].startswith("e4.") and it["k"] == "word"}
    assert "emails" in doc and any(it["id"].startswith("e4.mark") for it in l1["items"])
    box = L.card_box(panel)
    assert box[1] >= 0.29 and box[3] <= E.CAP_TOP


def test_cut_shot_keeps_the_source_clock():
    it = dict(id="s", k="shot", f=[10, 110], pic="main", src=[1.0, 4.3333], crop=[[0, 0, 1, 1]] * 100)
    part = E.cut_shot(it, 40, 70)
    assert part["f"] == [40, 70] and len(part["crop"]) == 30 and abs(part["src"][0] - 2.0) < 1e-3 and abs(part["src"][1] - 3.0) < 1e-3


def test_a_found_picture_fills_the_frame_only_when_its_subject_fits():
    # a portrait with a face: full frame, the eyes high
    lay, c = E.solve_frame(1600, 2400, [[0.4, 0.25, 0.2, 0.15]], None)
    assert lay == "full" and abs((c[2] - c[0]) * 1600 / ((c[3] - c[1]) * 2400) - 9 / 16) < 0.01
    eyes = (0.25 + 0.15 * 0.45 - c[1]) / (c[3] - c[1])
    assert 0.2 <= eyes <= 0.4
    # a paper keeps its words: a print wider than the window is a card, where an object of the same box fills the frame
    assert E.solve_frame(944, 1300, [], [0.07, 0.05, 0.93, 0.95], paper=True)[0] == "card"
    assert E.solve_frame(944, 1300, [], [0.07, 0.05, 0.93, 0.95])[0] == "full"
    # a wide picture whose subject spans it: whole, as a card
    lay, c = E.solve_frame(1920, 1275, [], [0.05, 0.1, 0.95, 0.9])
    assert lay == "card" and c == [0.0, 0.0, 1.0, 1.0]
    # a wide picture with one person in it: the frame goes to the person
    lay, c = E.solve_frame(2400, 1600, [[0.6, 0.2, 0.1, 0.15]], [0.52, 0.1, 0.78, 0.95])
    assert lay == "full" and c[0] <= 0.6 and c[2] >= 0.7
    # too small to fill a phone: a card
    lay, _ = E.solve_frame(900, 520, [], [0.4, 0.1, 0.6, 0.9])
    assert lay == "card"


def test_the_hook_is_never_a_card(clip):
    l1 = plan()
    idx = dict(words=words(), video=dict(duration=22.0))
    script = dict(SCRIPT, hook=dict(picture=1, line="One day", accent="day"))      # the script chose the wide picture
    rec = E.apply(l1, script, EARNED, idx, os.path.join(clip, "source.mp4"), manifest())
    assert rec["hook"]["picture"] == 0 and rec["hook"]["layout"] == "full"         # the first picture that fills the phone opens the film
    assert next(it for it in l1["items"] if it["k"] == "shot" and it["f"][0] == 0)["pic"] == "earned:0"


def test_a_pull_quote_is_never_drawn(clip):
    l1 = plan()
    assert E.panel_items("q", dict(kind="quote", parts=[dict(value="Why not you", label="Mark")], mark="you"), 100, 160, l1, "#FFC857", 0.3, 0.23) == []


def test_a_caption_across_a_cut_to_another_picture_becomes_two():
    l1 = dict(items=[dict(id="s0", k="shot", f=[0, 50], pic="main"), dict(id="s1", k="shot", f=[50, 90], pic="earned:1"),
                     dict(id="s2", k="shot", f=[90, 120], pic="earned:1"),
                     dict(id="c0.w0", k="word", role="cap", chunk="c0", f=[40, 70], t="the", v="a", x=[0.3] * 30, y=0.62, ink="#FFFFFF", on=40),
                     dict(id="c0.w1", k="word", role="cap", chunk="c0", f=[48, 70], t="idea", v="a", x=0.4, y=0.62, ink="#FFFFFF", on=48),
                     dict(id="c0.w2", k="word", role="cap", chunk="c0", f=[56, 70], t="now", v="a", x=0.5, y=0.62, ink="#FFFFFF", on=56),
                     dict(id="c1.w0", k="word", role="cap", chunk="c1", f=[49, 80], t="x", v="a", x=0.3, y=0.62, ink="#FFFFFF", on=49),
                     dict(id="big.0", k="word", role="big", f=[40, 70], t="B", v="a", x=0.3, y=0.3, ink="#FFFFFF", on=40)])
    assert L.picture_cuts(l1) == [50]
    assert L.split_captions_at(l1, [50]) == 1
    parts = [it for it in l1["items"] if it["id"].startswith("c0.w0")]
    assert [p["f"] for p in parts] == [[40, 50], [50, 70]] and [len(p["x"]) for p in parts] == [10, 20]
    assert parts[1]["chunk"] == "c0~50" and parts[1]["on"] == 50
    w1 = [it for it in l1["items"] if it["id"].startswith("c0.w1")]
    assert len(w1) == 1 and w1[0]["chunk"] == "c0~50" and w1[0]["f"] == [48, 70]        # two frames early: whole, on the later side
    assert next(it for it in l1["items"] if it["id"] == "c0.w2")["chunk"] == "c0~50"    # after the cut: the later chunk
    groups = L.groups(l1)
    assert sorted(len(v) for k, v in groups.items() if k.startswith("c0")) == [1, 3]    # the halves are two units
    assert len([it for it in l1["items"] if it["id"].startswith("c1.w0")]) == 1          # one frame before the cut: left whole
    assert len([it for it in l1["items"] if it["id"].startswith("big.0")]) == 1          # display type is not a caption


def test_the_hook_is_never_a_card(clip):
    l1 = plan()
    idx = dict(words=words(), video=dict(duration=22.0))
    script = dict(SCRIPT, hook=dict(picture=1, line="One day", accent="day"))      # the script chose the wide picture
    rec = E.apply(l1, script, EARNED, idx, os.path.join(clip, "source.mp4"), manifest())
    assert rec["hook"]["picture"] == 0 and rec["hook"]["layout"] == "full"         # the first picture that fills the phone opens the film
    assert next(it for it in l1["items"] if it["k"] == "shot" and it["f"][0] == 0)["pic"] == "earned:0"


def test_a_pull_quote_is_never_drawn(clip):
    l1 = plan()
    assert E.panel_items("q", dict(kind="quote", parts=[dict(value="Why not you", label="Mark")], mark="you"), 100, 160, l1, "#FFC857", 0.3, 0.23) == []


def test_a_caption_across_a_cut_to_another_picture_becomes_two():
    l1 = dict(items=[dict(id="s0", k="shot", f=[0, 50], pic="main"), dict(id="s1", k="shot", f=[50, 90], pic="earned:1"),
                     dict(id="s2", k="shot", f=[90, 120], pic="earned:1"),
                     dict(id="c0.w0", k="word", role="cap", chunk="c0", f=[40, 70], t="idea", v="a", x=[0.3] * 30, y=0.62, ink="#FFFFFF", on=40),
                     dict(id="c1.w0", k="word", role="cap", chunk="c1", f=[49, 80], t="x", v="a", x=0.3, y=0.62, ink="#FFFFFF", on=49),
                     dict(id="big.0", k="word", role="big", f=[40, 70], t="B", v="a", x=0.3, y=0.3, ink="#FFFFFF", on=40)])
    assert L.picture_cuts(l1) == [50]
    assert L.split_captions_at(l1, [50]) == 1
    parts = [it for it in l1["items"] if it["id"].startswith("c0.w0")]
    assert [p["f"] for p in parts] == [[40, 50], [50, 70]] and [len(p["x"]) for p in parts] == [10, 20]
    assert parts[1]["chunk"] == "c0~50" and parts[1]["on"] == 50
    assert len([it for it in l1["items"] if it["id"].startswith("c1.w0")]) == 1        # one frame before the cut: left whole
    assert len([it for it in l1["items"] if it["id"].startswith("big.0")]) == 1        # display type is not a caption


def test_a_chart_without_figures_is_not_drawn(clip):
    l1 = plan()
    d = dict(kind="versus", parts=[dict(value="Happy", label="when broke", n=1), dict(value="Really happy", label="when rich", n=3)])
    assert E.panel_items("v", d, 100, 180, l1, "#FFC857", 0.55, 0.23) == []


def test_the_hook_line_never_stands_on_a_face(clip):
    idx = dict(words=words(), video=dict(duration=22.0))
    # a close portrait: the face reaches under the seat above the captions, so the line goes under the chin and the captions under it
    big = dict(EARNED, rows=[dict(i=0, ok=True, name="A", path="earned/0.jpg", w=900, h=1400, faces=[[0.25, 0.12, 0.5, 0.34]], box=[0.2, 0.05, 0.8, 0.7])] + EARNED["rows"][1:])
    l1 = plan()
    rec = E.apply(l1, SCRIPT, big, idx, os.path.join(clip, "source.mp4"), manifest())
    hook = [it for it in l1["items"] if it["id"].startswith("hook.") and it["k"] == "word"]
    row = next(r for r in big["rows"] if r["i"] == 0)
    lay, c = E.solve_frame(900, 1400, row["faces"], row["box"])
    f = row["faces"][0]
    chin = (f[1] + f[3] - c[1]) / (c[3] - c[1])
    if hook:
        assert rec["hook"]["line_set"] and min(L.word_box(it, l1)[1] for it in hook) >= chin
        caps = [it for it in l1["items"] if it["k"] == "word" and it.get("role") == "cap" and it["f"][1] <= rec["hook_frames"] and it["f"][0] >= 3]
        assert caps and all(L.word_box(it, l1)[1] >= max(L.word_box(h, l1)[3] for h in hook) for it in caps)
    else:
        assert rec["hook"]["line_set"] is False
    # the same picture with a small face high in the frame keeps the seat above the captions
    l1 = plan()
    rec = E.apply(l1, SCRIPT, EARNED, idx, os.path.join(clip, "source.mp4"), manifest())
    hook = [it for it in l1["items"] if it["id"].startswith("hook.") and it["k"] == "word"]
    assert hook and max(L.word_box(it, l1)[3] for it in hook) <= E.CAP_TOP


def test_a_wide_hook_picture_opens_on_its_face():
    c = E.face_frame(1920, 828, [0.35, 0.10, 0.128, 0.40])              # a wide picture: the window goes to the face
    assert c and abs((c[2] - c[0]) * 1920 / ((c[3] - c[1]) * 828) - 9 / 16) < 0.01 and c[0] <= 0.35 and c[2] >= 0.478
    assert E.face_frame(1920, 817, [0.375, 0.18, 0.176, 0.53]) is None  # a banknote's portrait would reach the caption seat
    assert E.solve_frame(1200, 1600, [[0.2, 0.15, 0.6, 0.6]], [0.1, 0.05, 0.9, 0.95])[0] == "card"      # so would a close portrait
    assert E.face_frame(1920, 828, None) is None
    assert E.face_frame(1920, 828, [0.3, 0.1, 0.4, 0.8]) is None       # a face wider than the window stays a card
    assert E.face_frame(1000, 500, [0.4, 0.2, 0.1, 0.3]) is None       # too small to fill a phone


def test_a_footnote_waits_for_a_panel_and_is_waived_when_no_time_is_left():
    import footnotes
    notes = [dict(n=1, t_s=3.0, note="Founded in January 2022", source="Wikipedia, Cost Plus Drugs"),
             dict(n=2, t_s=9.0, note="Three PBMs hold 80% of the market", source="Wikipedia, Pharmacy benefit management")]
    l1 = plan()
    free = footnotes.apply(l1, notes)
    assert [p["n"] for p in free["placed"]] == [1, 2] and free["waived"] == []
    first = min(it["f"][0] for it in l1["items"] if it["id"].startswith("fn1.t."))
    l1 = plan()
    held = footnotes.apply(l1, notes, hold=[(first - 4, first + 60)])            # a panel stands where note 1 would be typed
    t1 = [it for it in l1["items"] if it["id"].startswith("fn1.t.")]
    assert t1 and min(it["f"][0] for it in t1) >= first + 60                      # it waits for the panel to leave
    l1 = plan()
    gone = footnotes.apply(l1, notes, hold=[(first - 4, 262)])                    # the panel stays until the next note's word
    assert [p["n"] for p in gone["placed"]] == [2] and gone["waived"][0]["n"] == 1
    assert not any(it["id"].startswith("fn1.") for it in l1["items"])            # no superscript without its note


def test_the_shells_model_call_takes_the_routes_arguments(monkeypatch):
    """worker/broll_source._ask grew a ``model`` argument on 13 Nov (the product's check moved to another provider);
    the shell answers on its own Claude key whatever model the route names, and writes the spend."""
    import types

    class Usage:
        input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens = 1000, 100, 0, 0

    seen = {}

    class Client:
        def __init__(self, **kw):
            self.messages = self

        def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text='{"fit":[]}')], usage=Usage())

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    monkeypatch.setitem(sys.modules, "broll_source", types.SimpleNamespace(NAMED_MODEL="claude-haiku-5-5"))
    llm = types.SimpleNamespace(PRICES={"claude-haiku-5-5": 1, "claude-opus-5-5": 1}, cost=lambda model, u: 0.001)
    spend = []
    ask = E._ask_with(llm, spend, "named picture")
    text, usd = ask("system", [{"type": "text", "text": "x"}], "named_check", 10.0, 100, model="gpt-6-sol")
    assert text == '{"fit":[]}' and usd == 0.001 and seen["model"] == "claude-haiku-5-5"
    assert spend and spend[0]["model"] == "claude-haiku-5-5" and spend[0]["what"] == "named picture: named_check"
    ask("system", [], "frame_boxes", 10.0, 100)                                   # and the old call still works
    assert len(spend) == 2
