"""No two type items share a place at the same time (p5565, 8 Nov 2026).

The first trial film drew a ticker as a column through STEM HORIZONS and a caption, FORENSIC SCIENTIST
over PILOT with a chip's label behind both, and "next generation" over WELCOME. A 16:9 video call sat
at 46 % of the 9:16 frame's height on a dark band. The film below has that shape."""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import score.compiler as S  # noqa: E402
from score import lower as L  # noqa: E402

FPS = 30


def call_index(seconds=30.0):
    words = [dict(w=f"w{i}", t0=round(0.3 + i * 0.4, 2), t1=round(0.3 + i * 0.4 + 0.32, 2), confidence=1.0, filler=False, speaker=0)
             for i in range(int((seconds - 1) / 0.4))]
    return dict(video=dict(width=1920, height=1080, duration=seconds, fps=30.0, has_audio=True), language="en", speakers=2,
                shots=[dict(id=1, start=0.0, end=seconds)], words=words,
                sentences=[dict(id="s1", wi0=0, wi1=len(words) - 1, t0=words[0]["t0"], t1=words[-1]["t1"], text="")])


def call_perception(seconds=30.0):
    # the call's empty band above the two faces, in source fractions
    return dict(W=1920, H=1080, dur=seconds, shots=[dict(id=1, t=[0, seconds], free=[[0.05, 0.0, 0.95, 0.2]])], samples=[])


FACES = [{"t": float(t), "boxes": [[0.30, 0.22, 0.40, 0.42], [0.55, 0.22, 0.65, 0.42]]} for t in range(31)]


def film_5565(beats):
    head = ['{"k":"film","v":1,"fps":30,"frame":"9:16","spine":"main","band":"own","own":{"med":1.5,"p10":0.5,"p90":3},"seed":5}',
            json.dumps({"k": "dna", "plate": "#061129", "ink": "#F2EFE9", "pair": {"a": "grotesk-bold@.03", "b": "serif-italic@.034"}, "big": "black", "seat": [0.5, 0.62]})]
    return "\n".join(head + [json.dumps(b) for b in beats]) + "\n"


SHAPE = [
    {"k": "b", "id": "b03", "w": "0-20", "job": "the event", "cut": [90, 90, 90], "pic": ["main", "main", "main"], "type": "0-20",
     "do": [{"g": "ticker", "t": "INTERNATIONAL STEM DAY ·", "at": "w0..w20.e", "y": 0.3, "bg": "#061129"},
            {"g": "big", "t": "STEM HORIZONS", "at": "w6..w20.e", "seat": [0.5, 0.3]}]},
    {"k": "b", "id": "b04", "w": "21-45", "job": "her careers", "cut": [120, 120], "pic": ["main", "main"],
     "do": [{"g": "big", "t": "PILOT", "at": "w21..w45.e", "seat": [0.5, 0.3]},
            {"g": "big", "t": "FORENSIC SCIENTIST", "at": "w30..w45.e", "seat": [0.5, 0.3]},
            {"g": "chip", "t": "toxicology · narcotics", "at": "w36", "d": 45, "seat": [0.5, 0.3]}]},
    {"k": "b", "id": "b07", "w": "46-70", "job": "welcome", "cut": [150, 150], "pic": ["main", "main"],
     "do": [{"g": "big", "t": "WELCOME", "at": "w46..w70.e", "seat": [0.5, 0.3]},
            {"g": "big", "t": "next generation", "at": "w62..w70.e", "seat": [0.5, 0.3]}]},
]


def compiled(beats=SHAPE, faces=()):
    """p5565's perception found no face boxes the type had to clear: it sat on the call's top band."""
    return S.compile_score(film_5565(beats), index=call_index(), perception=call_perception(), sid="p5565", bin_items={},
                           faces=list(faces))


def _units(r):
    ctx = types.SimpleNamespace(voices=r.l1["voices"], W=r.l1["frame"][0], H=r.l1["frame"][1])
    out = {}
    for it in r.l1["items"]:
        if it["k"] not in ("word", "card") or it.get("role") == "cap":
            continue
        rect = S.type_rect(ctx, it)
        if not rect:
            continue
        key = ".".join(it["id"].split(".")[:2])
        u = out.setdefault(key, {"a": it["f"][0], "z": it["f"][1], "r": list(rect)})
        u["a"], u["z"] = min(u["a"], it["f"][0]), max(u["z"], it["f"][1])
        u["r"] = [min(u["r"][0], rect[0]), min(u["r"][1], rect[1]), max(u["r"][2], rect[2]), max(u["r"][3], rect[3])]
    return out


def collisions(r):
    u = sorted(_units(r).items())
    bad = []
    for i, (ka, a) in enumerate(u):
        for kb, b in u[i + 1:]:
            if a["a"] < b["z"] and b["a"] < a["z"] and S.rects_touch(a["r"], b["r"]):
                bad.append((ka, kb))
    return bad


def test_the_5565_shape_compiles_with_no_type_on_type():
    r = compiled()
    assert not S.validate_l1(r.l1)
    assert collisions(r) == [], collisions(r)


def test_the_meter_says_what_moved_waited_or_went():
    r = compiled()
    did = {t["id"]: t for t in r.meter["type_traffic"]}
    assert did, r.meter["findings"]
    assert {"b04.big1", "b07.big1"} <= set(did), did          # the later big of each pair gave way
    assert all(t["did"] in ("moved", "delayed", "dropped") for t in did.values())
    assert "PILOT" in {i["t"] for i in r.l1["items"] if i["k"] == "word"}       # the earlier one keeps its seat


def test_the_score_asks_for_type_on_type():
    """The regression is real: this Score seats the ticker, both bigs of a beat and the chip on one place,
    as p5565's did (every display word at y 0.29). Each later one gave way, and the earlier kept its seat."""
    asked = [g for b in SHAPE for g in b["do"]]
    assert sum(1 for g in asked if g.get("seat") == [0.5, 0.3] or g.get("y") == 0.3) == len(asked)
    r = compiled()
    moved = {t["id"] for t in r.meter["type_traffic"]}
    assert {"b03.big", "b04.big1", "b04.chip", "b07.big1"} <= moved, r.meter["type_traffic"]


def test_a_ticker_never_crosses_a_face():
    beats = [{"k": "b", "id": "b01", "w": "0-30", "job": "x", "cut": [300], "pic": ["main"],
              "do": [{"g": "ticker", "t": "STEM DAY", "at": "w0..w30.e", "y": 0.36}]}]
    r = compiled(beats, faces=FACES)
    t = next(i for i in r.l1["items"] if i["id"] == "b01.ticker")
    shot = next(i for i in r.l1["items"] if i["k"] == "shot")
    c = shot["crop"][0] if isinstance(shot["crop"][0], list) else shot["crop"]
    rect = S.type_rect(types.SimpleNamespace(voices=r.l1["voices"], W=1080, H=1920), t)
    for f in FACES[0]["boxes"]:
        face = [(f[0] - c[0]) / (c[2] - c[0]), (f[1] - c[1]) / (c[3] - c[1]), (f[2] - c[0]) / (c[2] - c[0]), (f[3] - c[1]) / (c[3] - c[1])]
        assert not S.rects_touch(rect, face), (rect, face)
    assert any(x["id"] == "b01.ticker" for x in r.meter["type_traffic"])


def test_one_gestures_own_layers_are_its_design():
    """A counter's steps sit on one seat, a chip's word on its label: nothing in the pass moves them."""
    beats = [{"k": "b", "id": "b01", "w": "0-30", "job": "x", "cut": [300], "pic": ["main"],
              "do": [{"g": "counter", "from": 1, "to": 9, "at": "w0..w20.e", "size": 0.08, "seat": [0.5, 0.75]},
                     {"g": "chip", "t": "CAPTAIN", "at": "w22", "d": 40, "seat": [0.5, 0.75]}]}]
    r = compiled(beats)
    assert not [t for t in r.meter["type_traffic"] if t["id"] in ("b01.counter",)]
    chip = [i for i in r.l1["items"] if i["id"].startswith("b01.chip")]
    assert {i["k"] for i in chip} == {"card", "word"}


def test_a_running_line_lowers_to_one_line_that_crosses_the_frame():
    r = compiled()
    rep = {}
    texts = L.lower_texts(r.l1, rep)
    tick = next(t for t in texts if t["id"] == "tx.b03.ticker")
    v = r.l1["voices"][next(i for i in r.l1["items"] if i["id"] == "b03.ticker")["v"]]
    assert L.text_w(tick["text"], v) <= L.RUN_FIT * r.l1["frame"][0]
    assert tick["x"] == 0.5 and tick["motion"]["x"][0]["v"] > tick["motion"]["x"][-1]["v"]
    assert rep["running_lines_fitted"] == 1


def test_a_landscape_call_fills_more_of_a_portrait_frame():
    """p5565's held crop: 69 % of the call's width, so the picture stood at 46 % of the frame's height."""
    ctx = types.SimpleNamespace()
    target = (1080 / 1920) * (1080 / 1920)
    w = 0.692
    held = [0.412 - w / 2, 0.5 - w / target / 2, 0.412 + w / 2, 0.5 + w / target / 2]
    c = S.fill_floor(ctx, held, target)
    assert abs(1.0 / (c[3] - c[1]) - S.FILL_MIN_H) < 1e-6          # the picture now fills 55 % of the height
    assert abs((c[2] - c[0]) / (c[3] - c[1]) - target) < 1e-6       # still frame-shaped, so the type math holds
    assert abs((c[0] + c[2]) / 2 - 0.412) < 1e-6 and ctx.filled == 1
    assert S.fill_floor(ctx, [0.3, 0.0, 0.62, 1.0], target) == [0.3, 0.0, 0.62, 1.0]       # already large: unchanged
    assert S.fill_floor(ctx, [0, -0.2, 1, 1.2], 0.84) == [0, -0.2, 1, 1.2]               # a 4:3 upload: not this rule
