"""The Score compiler (worker/score/compiler.py), promoted from the Director slice with its tests."""
import json
import os
import random
import sys
import time

import pytest

import score.compiler as S  # noqa: E402


@pytest.fixture(autouse=True)
def _mechanics_only(monkeypatch):
    """These tests pin how each line of a Score compiles. What a film may carry is the taste governor's
    (score/taste.py), which has its own tests in test_score_taste.py, so it is off here."""
    import score.compiler as _compiler
    monkeypatch.setattr(_compiler, "GOVERN", False)

# Arabic-script type needs the images' fonts-noto-core (or VALMERA_SCRIPT_FONTS pointing at a copy)
ARABIC = pytest.mark.skipif(S.font_path("NotoSansArabic-Bold.ttf") is None, reason="no Arabic-script font on this machine")

SAMPLE_2_8 = """\
{"k":"treat","read":"One flat phone take, soft, shop light. A man walks past the wardrobe he sells.","story":"A Black Friday offer told by the man standing next to it.","impossible":"none: the wardrobe is the only object","hooks":[{"pic":"main@k1 door crop","line":"w5-6"},{"pic":"%date card","line":"w47-52"},{"pic":"bin:2","line":"w16-18"}],"pick":0,"risks":"logo missing; white doors behind captions"}
{"k":"film","v":1,"fps":30,"frame":"9:16","frame_why":"upload is 9:16","spine":"main","band":"P2","seed":7}
{"k":"dna","mat":"price tag","plate":"#C4161C","ink":"#FFFFFF","pair":{"a":"grotesk-bold@.03","b":"serif-italic@.034"},"seat":[0.5,0.62],"quiet":"caption seat","curve":"settle","look":"warm lift, skin kept"}
{"k":"bind","ask":"a1","to":["cap:*"]}
{"k":"bind","ask":"a3","to":[],"say":"No logo was uploaded. The shop name is set in type until it is."}
{"k":"b","id":"b01","w":"0-4","job":"the offer arrives before the man","cut":"pulse 0.5","pic":[{"src":"main@k1","crop":"door","cam":"settle .84"},{"src":"bin:2"}],"type":"0 1|2*-4","do":[{"g":"neg_card","at":"each_cut","f":2}],"snd":[{"s":"whoosh_soft_1","at":"b01.s2.in"}]}
{"k":"b","id":"b02","w":"5-8","job":"name the event","cut":"lead 3f","pic":[{"plate":"plate"}],"type":"5*-6*","do":[{"g":"big","t":"NARLAR NOVO","at":"w7"}],"snd":[{"s":"impact_1","at":"w5","db":-10}]}
{"k":"b","id":"b03","w":"16-20","job":"the object itself","cut":"pulse 0.6","pic":[{"src":"main@k1","crop":"door.left"},{"src":"main@k1","crop":"door.right"},{"src":"bin:5"}],"type":"16 17|18*|19 20","rest":{"do":"the doors are the design here","snd":"his voice sells this"}}
"""


def talk_index(seconds=60.0, wps=3.2, seed=3, faces=True):
    """A talking clip: words on a human rhythm, sentences of 6-12 words, a pause between them."""
    r = random.Random(seed)
    words, sents, t = [], [], 0.4
    i = 0
    while t < seconds - 1:
        n = r.randint(6, 12)
        first = i
        for _ in range(n):
            d = r.uniform(0.6, 1.6) / wps
            words.append({"w": f"palavra{i}", "t0": round(t, 3), "t1": round(t + d, 3), "filler": False, "speaker": 0, "confidence": 1.0})
            t += d + r.uniform(0, 0.05)
            i += 1
        sents.append({"id": f"s{len(sents) + 1}", "t0": words[first]["t0"], "t1": words[-1]["t1"], "wi0": first, "wi1": i - 1, "text": "", "speaker": 0})
        t += r.uniform(0.1, 0.4)
    samples = [{"t": float(k), "faces": [[0.36, 0.12, 0.64, 0.3]] if faces else [], "mean_luma": 90.0} for k in range(int(seconds) + 1)]
    return {"video": {"fps": 30.0, "width": 1080, "height": 1920, "duration": seconds, "has_audio": True},
            "words": words, "sentences": sents, "shots": [{"id": 1, "start": 0.0, "end": seconds}],
            "spatial": {"samples": samples}}


def wordless_index(seconds=51.0):
    return {"video": {"fps": 30.0, "width": 720, "height": 1280, "duration": seconds, "has_audio": True},
            "words": [], "sentences": [], "shots": [{"id": 1, "start": 0.0, "end": 20.0}, {"id": 2, "start": 20.0, "end": seconds}]}


BIN = {str(i): {"kind": "image", "path": f"bin/{i}.jpg", "w": 1600, "h": 1067} for i in range(4)}
BIN["4"] = {"kind": "video", "path": "bin/4.mp4", "w": 1080, "h": 1920, "dur": 9.0, "fps": 25.0}


def film_60s(idx, band="S1", pulse=0.5):
    """A 60 s Score: every beat on a pulse, with no thought for where words fall."""
    head = [json.dumps({"k": "film", "v": 1, "fps": 30, "frame": "9:16", "band": band, "seed": 7, "gap": 0.2}),
            json.dumps({"k": "dna", "plate": "#101010", "ink": "#FFFFFF", "pair": {"a": "grotesk-bold@.03", "b": "serif-italic@.034"},
                        "seat": [0.5, 0.5], "look": {"contrast": 1.1, "sat": 0.9}, "grain": 0.05})]
    lines = []
    n = len(idx["words"])
    w, k = 0, 0
    r = random.Random(11)
    while w < n:
        z = min(n - 1, w + r.randint(6, 14))
        pics = [{"src": "main", "crop": [0.1, 0.1, 0.8, 0.8]}, {"src": f"bin:{k % 4}"}, {"src": "bin:4"}, {"src": f"bin:{(k + 1) % 4}"}]
        lines.append(json.dumps({"k": "b", "id": f"b{k:02d}", "w": f"{w}-{z}", "job": "a beat", "cut": f"pulse {pulse}", "pic": pics,
                                 "type": "|".join(f"{a}-{min(z, a + 2)}" for a in range(w, z + 1, 3)),
                                 "do": [{"g": "neg_card", "at": f"w{w}", "f": 2}], "snd": [{"s": "whoosh_soft_2", "at": f"b{k:02d}.in"}]}))
        w, k = z + 1, k + 1
    return "\n".join(head + lines) + "\n"


def check(r):
    probs = S.validate_l1(r.l1)
    assert not probs, probs[:5]


def compile_(text, idx, **kw):
    kw.setdefault("bin_items", BIN)
    kw.setdefault("faces", None)
    return S.compile_score(text, index=idx, sid="fixture", **kw)


# -- the tests the package names ----------------------------------------------

def test_design_sample_compiles():
    idx = talk_index(33.0)
    r = compile_(SAMPLE_2_8, idx)
    check(r)
    ids = {i["id"] for i in r.l1["items"]}
    assert {"b01.s0", "b02.s0", "b03.s0"} <= ids
    assert any(w["t"] == "NARLAR NOVO" for w in r.l1["items"] if w["k"] == "word")
    assert len(r.l1["sound"]) == 2
    assert any(s["s"] == "impact_1" and s["db"] == -10 for s in r.l1["sound"])


def test_truncated_at_any_byte_compiles():
    idx = talk_index(33.0)
    data = SAMPLE_2_8.encode()
    rng = random.Random(5)
    for cut in [0, 1, 2, len(data) - 1, len(data)] + [rng.randrange(len(data)) for _ in range(120)]:
        r = compile_(data[:cut], idx)
        check(r)
        assert r.l1["dur_f"] > 0


def test_bad_line_drops_only_itself_and_its_words_get_hold_beats():
    idx = talk_index(33.0)
    good = compile_(SAMPLE_2_8, idx)
    lines = SAMPLE_2_8.splitlines()
    lines[6] = lines[6][:40] + "}}}{{ broken"
    r = compile_("\n".join(lines), idx)
    check(r)
    ids = {b["id"] for b in r.meter["by_beat"]}
    assert "b01" in ids and "b03" in ids and "b02" not in ids
    assert r.meter["rejects"]
    assert any(i.startswith("hold") for i in ids)
    heard = sorted(int(w["id"].split(".w")[1].split(".")[0]) for w in r.l1["items"] if w["k"] == "word" and w.get("role") == "cap")
    assert set(range(5, 9)) <= set(heard)          # the dropped beat's words still show
    assert abs(r.l1["dur_f"] - good.l1["dur_f"]) <= 2


def test_same_seed_is_byte_identical_and_seed_matters():
    idx = talk_index(60.0)
    text = film_60s(idx)
    a = compile_(text, idx).json()
    b = compile_(text, idx).json()
    assert a == b
    c = compile_(text.replace('"seed": 7', '"seed": 8'), idx).json()
    assert a != c


@pytest.mark.parametrize("seed,band,pulse", [(7, "S1", 0.5), (8, "S1", 0.45), (9, "S2", 0.6), (10, "P2", 0.74)])
def test_cuts_are_not_snapped_to_words(seed, band, pulse):
    idx = talk_index(60.0, seed=seed)
    r = compile_(film_60s(idx, band=band, pulse=pulse), idx)
    check(r)
    assert 54 <= r.meter["dur_s"] <= 62
    assert r.meter["word_lock"] < 0.55, r.meter["word_lock"]
    # and the cut stream has its own spread, not one repeated length
    cuts = S.distinct_cuts([i for i in r.l1["items"] if i["k"] == "shot"])
    lens = [b - a for a, b in zip(cuts, cuts[1:])]
    assert len(set(lens)) >= 8


def test_compile_under_300_ms():
    idx = talk_index(60.0)
    text = film_60s(idx)
    compile_(text, idx)                     # fonts warm, as in a running worker
    # CPU time: other agents load this Mac, and wall time then measures them
    runs = []
    for _ in range(3):
        t = time.process_time()
        compile_(text, idx)
        runs.append((time.process_time() - t) * 1000)
    assert sorted(runs)[1] < 300


# -- the clocks ---------------------------------------------------------------

def test_captions_run_on_the_voice():
    idx = talk_index(33.0)
    txt = '{"k":"film","fps":30,"band":"S1"}\n{"k":"b","id":"b1","w":"0-5","type":"0 1 2|3-5","cut":"pulse .5"}\n'
    r = compile_(txt, idx)
    caps = sorted((w for w in r.l1["items"] if w["k"] == "word" and w["id"].startswith("b1.")), key=lambda w: w["on"])
    assert len(caps) == 6
    t0 = idx["words"][0]["t0"] - S.PAD_IN
    for w in caps:
        n = int(w["id"].split(".w")[1])
        voice = (idx["words"][n]["t0"] - t0) * 30
        assert -1 <= w["on"] - voice <= 6


def test_display_word_lags_the_voice_within_the_measured_band():
    idx = talk_index(60.0)
    lags = []
    for w in range(0, 90, 3):
        txt = f'{{"k":"film","fps":30}}\n{{"k":"b","id":"b1","w":"0-100","do":[{{"g":"big","t":"X","at":"w{w}"}}]}}\n'
        r = compile_(txt, idx)
        it = next(i for i in r.l1["items"] if i["k"] == "word" and i.get("role") == "big")
        ctx_f = None
        for s in r.l1["spine"]:
            t = idx["words"][w]["t0"]
            if s["src"][0] <= t < s["src"][1]:
                ctx_f = s["f"][0] + (t - s["src"][0]) * 30
        lags.append(it["on"] - ctx_f)
    assert all(-1.5 <= x <= 8 or x >= 8 for x in lags)
    assert 1 <= sorted(lags)[len(lags) // 2] <= 6


def test_sound_peak_lands_on_the_event():
    idx = talk_index(33.0)
    txt = '{"k":"film","fps":30}\n{"k":"b","id":"b1","w":"0-40","snd":[{"s":"impact_1","at":"w20"},{"s":"riser_1","at":"w2"}]}\n'
    r = compile_(txt, idx)
    table, _ = S.sound_table()
    for s in r.l1["sound"]:
        peak = table[s["s"]]["peak_s"]
        assert abs(s["start_s"] + peak - s["trim_s"] - s["event_f"] / 30) < 1e-3
    riser = next(s for s in r.l1["sound"] if s["s"] == "riser_1")
    assert riser["start_s"] == 0 and riser["trim_s"] > 0     # head cut, peak still on the frame


# -- the spine ----------------------------------------------------------------

def test_cold_open_plays_later_words_first():
    idx = talk_index(33.0)
    txt = '{"k":"film","fps":30}\n{"k":"b","id":"hook","w":"20-24"}\n{"k":"b","id":"b1","w":"0-19"}\n{"k":"b","id":"b2","w":"25-60"}\n'
    r = compile_(txt, idx)
    check(r)
    sp = r.l1["spine"]
    assert sp[0]["src"][0] > sp[1]["src"][0]
    for s in sp:
        assert abs((s["src"][1] - s["src"][0]) - (s["f"][1] - s["f"][0]) / 30) < 1e-3


def test_gap_closes_pauses_and_keeps_sync():
    idx = talk_index(60.0)
    txt = '{"k":"film","fps":30,"gap":0.1}\n{"k":"b","id":"b1","w":"0-150"}\n'
    r = compile_(txt, idx)
    check(r)
    open_ = compile_(txt.replace(',"gap":0.1', ""), idx)
    assert r.l1["dur_f"] < open_.l1["dur_f"]
    assert len(r.l1["spine"]) > 3
    # every main shot sits on the spine: picture and voice stay locked across jumps
    for it in r.l1["items"]:
        if it["k"] == "shot" and it.get("pic") == "main":
            s = next(x for x in r.l1["spine"] if x["f"][0] <= it["f"][0] < x["f"][1])
            want = s["src"][0] + (it["f"][0] - s["f"][0]) / 30
            assert abs(want - it["src"][0]) < 1e-2


def test_explicit_drop_removes_words_without_a_hold():
    idx = talk_index(33.0)
    n = len(idx["words"]) - 1
    txt = '{"k":"film","fps":30,"drop":["10-14"]}\n{"k":"b","id":"b1","w":"0-%d"}\n' % n
    r = compile_(txt, idx)
    assert not [f for f in r.meter["findings"] if "in no beat" in f]
    assert compile_(txt.replace(',"drop":["10-14"]', ""), idx).l1["dur_f"] > r.l1["dur_f"]


def test_wordless_film_uses_k_anchors():
    idx = wordless_index()
    txt = ('{"k":"film","fps":30,"band":"M5","seed":3}\n'
           '{"k":"b","id":"b1","at":"k1+0.4s..k1+3.1s","cut":"pulse .4","pic":[{"src":"main@1.0-2.0","crop":[0.2,0.2,0.8,0.7]},{"src":"main"}]}\n'
           '{"k":"b","id":"b2","at":"k2+1s..k2+4s","do":[{"g":"big","t":"NIGHT","at":"b2.in"}]}\n'
           '{"k":"b","id":"p1","f":20,"pic":[{"plate":"black"}]}\n')
    r = compile_(txt, idx)
    check(r)
    assert abs(r.l1["dur_f"] - (81 + 90 + 20)) <= 2
    assert r.l1["spine"][0]["src"][0] == pytest.approx(0.4, abs=0.01)
    assert r.l1["spine"][1]["src"][0] == pytest.approx(21.0, abs=0.01)


def test_nothing_at_all_still_gives_a_film():
    r = compile_("", talk_index(20.0))
    check(r)
    assert r.l1["dur_f"] > 100
    r = compile_("garbage\n{\n", wordless_index(9.0))
    check(r)
    assert abs(r.l1["dur_f"] - 270) <= 1


# -- compile rules ------------------------------------------------------------

def test_nothing_enters_the_watermark_keepout():
    idx = talk_index(33.0)
    txt = ('{"k":"film","fps":30}\n{"k":"dna","seat":[0.72,0.15]}\n'
           '{"k":"b","id":"b1","w":"0-30","type":"0-2|3-5","do":[{"g":"big","t":"HERE","at":"w8","d":30,"seat":[0.74,0.15],"size":0.05},'
           '{"g":"card","pic":{"plate":"#FFFFFF"},"at":"w10..w20","w":0.3,"h":0.1,"x":0.74,"y":0.15}]}\n')
    r = compile_(txt, idx)
    check(r)
    for it in r.l1["items"]:
        if it["k"] == "card":
            y = S._last(it["y"])
            s = S._last(it["s"])
            rect = (it["x"] - it["w"] * s / 2, y - it["h"] * s / 2, it["x"] + it["w"] * s / 2, y + it["h"] * s / 2)
            assert not S.rects_touch(rect, S.KEEPOUT)
        if it["k"] == "word":
            v = r.l1["voices"][it["v"]]
            w = S.text_px(it["t"], v) / r.l1["frame"][0]
            h = v["px"] * 0.74 / r.l1["frame"][1]
            x, y = S._last(it["x"]), S._last(it["y"])
            assert not S.rects_touch((x, y - h, x + w, y), S.KEEPOUT), it["id"]


def test_moving_type_stays_out_of_the_keepout():
    idx = talk_index(33.0, faces=False)
    p = perc()
    for r in p["samples"]:
        r["nose"] = [0.7, 0.16]
    txt = ('{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-60","pic":[{"src":"main","crop":"full"}],'
           '"do":[{"g":"hud","of":"face","at":"w3..w30","label":"LIDER"},{"g":"pin","t":"AQUI","of":"nose","at":"w3..w30","size":0.03}]}\n')
    r = compile_(txt, idx, perception=p)
    check(r)
    for it in r.l1["items"]:
        if it["k"] == "word":
            v = r.l1["voices"][it["v"]]
            w = S.text_px(it["t"], v) / 1080
            h = v["px"] * 0.74 / 1920
            xs = it["x"] if isinstance(it["x"], list) else [it["x"]]
            ys = it["y"] if isinstance(it["y"], list) else [it["y"]]
            for x, y in zip(xs * len(ys), ys):
                assert not S.rects_touch((x, y - h, x + w, y), S.KEEPOUT), it["id"]


def test_captions_leave_the_face():
    idx = talk_index(33.0)
    txt = '{"k":"film","fps":30}\n{"k":"dna","seat":[0.5,0.2]}\n{"k":"b","id":"b1","w":"0-30","type":"0-2|3-5","pic":[{"src":"main","crop":"full"}]}\n'
    r = compile_(txt, idx, faces=[{"t": float(t), "boxes": [[0.3, 0.1, 0.7, 0.32]]} for t in range(34)])
    seen = [it for it in r.l1["items"] if it["k"] == "word" and it["id"].startswith("b1.")]
    assert seen and all(it["y"] > 0.32 for it in seen)


def test_filler_never_shows_and_fix_spells():
    idx = talk_index(33.0)
    idx["words"][3]["filler"] = True
    idx["words"][4]["w"] = "Flyder,"
    txt = '{"k":"film","fps":30}\n{"k":"fix","w":"4","t":"Friday"}\n{"k":"b","id":"b1","w":"0-8","type":"0-8"}\n'
    r = compile_(txt, idx)
    t = [i["t"] for i in r.l1["items"] if i["k"] == "word"]
    assert "palavra3" not in t and "Friday" in t and "Flyder," not in t


def test_only_approved_sounds_and_no_music():
    idx = talk_index(33.0)
    txt = '{"k":"film","fps":30}\n{"k":"b","id":"b1","w":"0-30","snd":[{"s":"my_synth","at":"w2"},{"s":"impact","at":"w3"}]}\n'
    r = compile_(txt, idx)
    assert [s["s"] for s in r.l1["sound"]] == ["impact_1"]     # a role picks an approved file
    assert any("my_synth" in f for f in r.meter["findings"])


def test_customer_score_refuses_def_and_shader():
    idx = talk_index(33.0)
    txt = ('{"k":"def","name":"x"}\n{"k":"film","fps":30}\n{"k":"b","id":"b1","w":"0-30"}\n'
           '{"k":"item","id":"x1","kind":"shader","at":"w2","d":3}\n'
           '{"k":"item","id":"x2","kind":"flash","mode":"neg","at":"w2","d":3}\n')
    r = compile_(txt, idx)
    assert any("def" in f for f in r.meter["findings"]) and any("shader" in f for f in r.meter["findings"])
    assert any(i["id"] == "x2" for i in r.l1["items"])


def test_black_and_white_stays_a_device():
    idx = talk_index(33.0)
    txt = '{"k":"film","fps":30}\n{"k":"dna","look":{"sat":0}}\n{"k":"b","id":"b1","w":"0-60"}\n'
    r = compile_(txt, idx)
    assert r.l1["film"]["grade"]["sat"] >= 0.5
    txt = '{"k":"film","fps":30}\n{"k":"b","id":"b1","w":"0-60","look":{"sat":0}}\n{"k":"b","id":"b2","w":"61-70"}\n'
    r = compile_(txt, idx)
    sat0 = sum(i["f"][1] - i["f"][0] for i in r.l1["items"] if i["k"] == "shot" and (i.get("grade") or {}).get("sat", 1) <= S.MONO_SAT)
    assert sat0 <= S.MONO_SHARE * r.l1["dur_f"] + 1


def test_prose_look_is_reported_not_guessed():
    idx = talk_index(33.0)
    r = compile_('{"k":"film"}\n{"k":"dna","look":"warm lift, skin kept"}\n{"k":"b","id":"b1","w":"0-30"}\n', idx)
    assert r.l1["film"]["grade"] == S.IDENTITY or r.l1["film"]["grade"] == {k: float(v) for k, v in S.IDENTITY.items()}
    assert any("prose" in f for f in r.meter["findings"])


# -- gestures -----------------------------------------------------------------

@pytest.mark.parametrize("g", [
    {"g": "hold", "pic": "bin:1", "cam": "settle .84"},
    {"g": "cuts", "pic": ["bin:1", "bin:2", "bin:3"], "pulse": 0.4},
    {"g": "salvo", "pic": ["bin:1", "bin:2"], "f": [2, 3], "treat": "neg:cyan", "every": 3},
    {"g": "salvo", "pic": "main", "f": 2, "n": 8},
    {"g": "page", "plate": "#C4161C", "t": "WAS JUST", "size": 0.1, "in": "flicker"},
    {"g": "page", "plate": "paper", "w": "2-4"},
    {"g": "big", "t": "BLACK|FRIDAY", "at": "w3", "in": "letters", "font": "condensed"},
    {"g": "big", "w": "3-4", "in": "bounce", "hl": "#FFE600", "size": 0.09},
    {"g": "big", "t": "SETTLE", "at": "w4", "d": 20, "in": "settle", "blend": "difference"},
    {"g": "card", "pic": "bin:2", "bg": "dim", "rot": -2},
    {"g": "card", "pic": "main", "bg": "plate", "in": "cut"},
    {"g": "band", "pic": "bin:4"},
    {"g": "neg_card", "at": "each_cut", "f": 2},
    {"g": "flash", "at": "w4", "mode": "white", "f": 1},
    {"g": "flash", "at": "each_cut", "mode": "threshold", "f": 2, "rect": [0.1, 0.1, 0.9, 0.6]},
    {"g": "burn", "at": "w4"},
    {"g": "tiles", "at": "w2", "f": 2},
    {"g": "tiles", "at": "w2", "mode": "fill", "n": 6},
    {"g": "punch", "at": "w5", "z": 1.3},
    {"g": "freeze", "at": "w5", "d": 15, "treat": "neg", "cam": "push 1.06"},
    {"g": "counter", "from": 20, "to": 29, "fmt": "DIA {n}", "size": 0.14},
    {"g": "pile", "pic": ["bin:0", "bin:1", "bin:2", "bin:3"], "every": 5},
    {"g": "split", "pic": ["main", "bin:1"], "bg": "plate"},
    {"g": "glitch", "at": "every 10f"},
    {"g": "boxes", "at": "w4", "hold": 12},
    {"g": "slat", "at": "w4"},
    {"g": "whip", "at": "each_cut"},
    {"g": "slide", "at": "each_cut", "dir": "left"},
    {"g": "shake", "at": "w5"},
    {"g": "chip", "t": "20-29 NOV", "at": "w5"},
    {"g": "shred", "at": "each_cut", "n": 6},
    {"g": "shred", "at": "each_cut", "dir": "left", "order": "seed"},
    {"g": "shatter", "at": "each_cut", "grid": "4x6"},
    {"g": "echo", "at": "w2..w6"},
    {"g": "stutter", "at": "w5"},
    {"g": "type_on", "t": "promocao espetacular", "at": "w2..w12"},
    {"g": "wall", "pic": ["bin:0", "bin:1", "bin:2"], "at": "w2..w9", "every": 2},
    {"g": "ticker", "t": "BLACK FRIDAY", "at": "w2..w12", "bg": "#C4161C"},
    {"g": "big", "t": [{"t": "get", "size": 0.06}, {"t": "that you", "size": 0.015}], "at": "w5", "seat": "free"},
])
def test_every_gesture_draws_inside_the_contract(g):
    idx = talk_index(33.0)
    txt = ('{"k":"film","fps":30,"band":"S1","seed":4}\n{"k":"dna","plate":"#C4161C","look":"warm_low"}\n'
           + json.dumps({"k": "b", "id": "a0", "w": "0-20", "job": "x", "cut": "pulse .6", "pic": ["main", "bin:0"], "type": "0-2|3-5|6-8", "do": [g]}) + "\n"
           '{"k":"b","id":"a1","w":"21-30"}\n')
    r = compile_(txt, idx)
    check(r)
    assert not [f for f in r.meter["findings"] if "failed" in f or "unknown" in f], r.meter["findings"]
    assert any(i["id"].startswith(f"a0.{g['g']}") for i in r.l1["items"]), g


def perc(seconds=33.0):
    """A man who walks across the frame, sampled as perceive.py samples him."""
    rows = []
    for k in range(int(seconds * 5)):
        t = k * 0.2
        x = 0.3 + 0.4 * (t / seconds)
        rows.append({"t": t, "face": [x - 0.05, 0.2, x + 0.05, 0.25], "body": [x - 0.15, 0.2, x + 0.15, 0.8], "luma": 0.6})
    return {"samples": rows, "shots": [{"id": 1, "t": [0, seconds], "free": [[0.0, 0.3, 0.6, 0.5], [0.72, 0.03, 1.0, 0.12]]}]}


def test_subject_crop_follows_him_and_stays_in_the_source():
    idx = talk_index(33.0, faces=False)
    txt = '{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-90","pic":[{"src":"main","crop":"body","cam":"push 1.05"}]}\n'
    r = compile_(txt, idx, perception=perc())
    check(r)
    c = next(i for i in r.l1["items"] if i["id"] == "a0.s0")["crop"]
    assert isinstance(c[0], list)
    cx = [(q[0] + q[2]) / 2 for q in c]
    assert cx[-1] > cx[0] + 0.1                        # he walked right, the frame went with him
    assert max(abs(x - y) for x, y in zip(cx, cx[1:])) < 0.01    # with no twitch


def test_hud_and_spot_ride_the_subject():
    idx = talk_index(33.0, faces=False)
    txt = ('{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-60","pic":[{"src":"main","crop":"full"}],'
           '"do":[{"g":"hud","of":"body","at":"w3..w30","label":"LIDER"},{"g":"spot","of":"body","at":"w31..w50"}]}\n')
    r = compile_(txt, idx, perception=perc())
    check(r)
    c = next(i for i in r.l1["items"] if i["id"] == "a0.hud.tl_h")
    assert c["rect"][-1][0] > c["rect"][0][0]
    assert any(i["id"] == "a0.spot.3" for i in r.l1["items"])


def test_pin_rides_a_tracked_point():
    idx = talk_index(33.0, faces=False)
    p = perc()
    for r in p["samples"]:
        r["rw"] = [r["body"][0], 0.5]
    txt = ('{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-60","pic":[{"src":"main","crop":"full"}],'
           '"do":[{"g":"pin","t":"6 PORTAS","of":"rw","at":"w3..w30","size":0.03}]}\n')
    r = compile_(txt, idx, perception=p)
    check(r)
    it = next(i for i in r.l1["items"] if i["id"] == "a0.pin")
    assert it["x"][-1] > it["x"][0]


def test_cutouts_follow_the_voice_and_flip_the_ink(tmp_path):
    idx = talk_index(33.0, faces=False)
    d = tmp_path / "own"
    d.mkdir()
    (d / "cut_paper.mp4").write_bytes(b"x")
    (d / "plates.json").write_text(json.dumps({"paper": ["#F4F1EC", "#D8D3CA"]}))
    (tmp_path / "index.json").write_text(json.dumps(idx))
    (tmp_path / "matte.mp4").write_bytes(b"x")
    txt = ('{"k":"film","fps":30,"gap":0.1}\n{"k":"b","id":"a0","w":"0-30","pic":"cut:paper","type":"0-2|3-5",'
           '"do":[{"g":"big","t":"BEHIND","at":"w8","behind":true,"seat":[0.5,0.4]}]}\n')
    r = S.compile_score(txt, work_dir=str(tmp_path))
    check(r)
    assert "own:cut_paper" in r.l1["assets"] and "plate" not in r.l1["assets"]["own:cut_paper"]
    assert "matte" in r.l1["assets"]
    words = [i for i in r.l1["items"] if i["k"] == "word" and i["id"].startswith("a0.c")]
    assert words and all(w["ink"] == "#111111" for w in words)
    assert any(i.get("behind") for i in r.l1["items"])
    shots = [i for i in r.l1["items"] if i["k"] == "shot" and i.get("pic") == "own:cut_paper"]
    assert len(shots) >= 2      # it jumps with the spine, like the take itself


def test_real_bin_shape_loads(tmp_path):
    (tmp_path / "bin.json").write_text(json.dumps({"sid": "x", "items": [
        {"id": 0, "kind": "video", "path": "bin/0.mp4", "w": 1080, "h": 1920, "dur": 5.95, "fps": 29.97},
        {"id": 11, "kind": "image", "path": "bin/11.jpg", "w": 867, "h": 1300}]}))
    b = S.load_bin(str(tmp_path))
    assert b["0"]["kind"] == "video" and b["11"]["path"] == "bin/11.jpg"


def test_caption_steps_off_display_type():
    idx = talk_index(33.0, faces=False)
    txt = ('{"k":"film","fps":30}\n{"k":"dna","seat":[0.5,0.42]}\n{"k":"b","id":"a0","w":"0-20","type":"0-5",'
           '"do":[{"g":"page","plate":"#000000","t":"BLACK|FRIDAY","at":"w0..w5","size":0.2,"seat":[0.5,0.42]}]}\n')
    r = compile_(txt, idx)
    big = [i for i in r.l1["items"] if i.get("role") == "big"]
    caps = [i for i in r.l1["items"] if i.get("role") == "cap" and i["id"].startswith("a0.c0")]
    low = max(i["y"] for i in big)
    assert caps and all(i["y"] > low for i in caps)


@pytest.mark.parametrize("layout", ["prelaid", "reflow", "chunk", "stack", "ghost", "trail", "redact"])
def test_caption_layouts(layout):
    idx = talk_index(33.0)
    txt = ('{"k":"film","fps":30}\n{"k":"dna","cap":{"layout":"%s"}}\n{"k":"b","id":"a0","w":"0-20","type":"0-3|4-6|7-9"}\n' % layout)
    r = compile_(txt, idx)
    check(r)
    assert len([i for i in r.l1["items"] if i.get("role") == "cap" and i["id"].startswith("a0.")]) == 10


def test_soft_crop_is_held_at_the_limit():
    idx = talk_index(33.0)
    idx["video"].update(width=478, height=850)
    txt = '{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-60","pic":[{"src":"main","crop":[0.45,0.4,0.5,0.43]}]}\n'
    r = compile_(txt, idx)
    c = next(i for i in r.l1["items"] if i["id"] == "a0.s0")["crop"]
    assert 1080 / ((c[2] - c[0]) * 478) <= 6.01
    assert any("held at" in f for f in r.meter["findings"])


def test_two_frame_card_lands_on_its_planned_frames():
    idx = talk_index(33.0)
    txt = '{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-20","cut":"30 30 30","pic":["main"],"do":[{"g":"neg_card","at":"each_cut","f":2}]}\n'
    r = compile_(txt, idx)
    cuts = [i["f"][0] for i in r.l1["items"] if i["k"] == "shot"][1:]
    fl = sorted(i["f"] for i in r.l1["items"] if i["k"] == "flash")
    assert [f[0] for f in fl] == cuts[:len(fl)]
    assert all(f[1] - f[0] == 2 for f in fl)


def test_raw_item_compiles_with_anchors():
    idx = talk_index(33.0)
    txt = ('{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-20"}\n'
           '{"k":"item","id":"mark1","kind":"flash","mode":"fill","c":"#FFE600","at":"w4..w6","rect":[0.1,0.5,0.9,0.52]}\n')
    r = compile_(txt, idx)
    it = next(i for i in r.l1["items"] if i["id"] == "mark1")
    assert it["f"][1] > it["f"][0]


# -- meter --------------------------------------------------------------------

def test_meter_reads_blanks_and_the_ceiling():
    idx = talk_index(33.0)
    r = compile_('{"k":"film","fps":30}\n{"k":"b","id":"b1","w":"0-60","job":"all of it"}\n', idx)
    txt = r.meter_text
    assert "b1:" in txt and "snd" in r.meter["blanks"]["b1"]
    assert r.meter["ceiling"] <= 4.5        # nothing added: rubric arithmetic
    assert "CEILING" in txt
    full = compile_(film_60s(idx), idx)
    assert full.meter["ceiling"] == 10.0
    assert full.meter["sounds"] > 5 and full.meter["cuts10"] > 5


def test_flash_gate_thins_past_three_a_second():
    idx = talk_index(33.0)
    txt = ('{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-60","do":[{"g":"flash","at":"every 4f","f":2,"mode":"white"},'
           '{"g":"tiles","at":"w3","f":2}]}\n')
    r = compile_(txt, idx)
    check(r)
    frames = sorted(i["f"][0] for i in r.l1["items"] if i["k"] == "flash" and i.get("c") == "#FFFFFF")
    assert frames
    assert all(sum(1 for x in frames if f <= x < f + 30) <= 3 for f in frames)
    assert any("flash gate" in f for f in r.meter["findings"])
    assert any("tiles" in i["id"] for i in r.l1["items"])          # a small cell is not a flash
    ok = compile_(txt.replace('"fps":30}', '"fps":30,"flash_ok":true}'), idx)
    assert len([i for i in ok.l1["items"] if i["k"] == "flash"]) > len([i for i in r.l1["items"] if i["k"] == "flash"])


def test_compile_facts_read_the_plan():
    idx = talk_index(33.0)
    plain = compile_('{"k":"film","fps":30}\n{"k":"b","id":"a0","w":"0-100","pic":[{"src":"main","crop":"full"}]}\n', idx)
    assert plain.meter["facts"]["class"] in ("same_upload", "light_touch")
    assert plain.meter["facts"]["opening_changed"] is False
    full = compile_(film_60s(talk_index(60.0)), talk_index(60.0))
    fx = full.meter["facts"]
    assert fx["class"] == "edited" and fx["new_sources"] >= 2 and fx["grade_applied"] and fx["sfx_added"] > 0
    assert "FACTS" in full.meter_text


def test_rest_counts_as_filled():
    idx = talk_index(33.0)
    n = len(idx["words"]) - 1
    txt = ('{"k":"film","fps":30,"band":"S1"}\n{"k":"dna","look":"print"}\n'
           '{"k":"b","id":"b1","w":"0-%d","job":"x","pic":"main","type":"0-5","do":[{"g":"flash","at":"w3"}],'
           '"rest":{"snd":"his voice carries it"}}\n' % n)
    r = compile_(txt, idx)
    assert not r.meter["blanks"]


def test_real_sessions_compile_when_packs_are_on_disk():
    here = os.environ.get("DIRECTOR_SLICE_WORK") or "/nonexistent"
    for sid in ("p5302", "p5374", "p5297"):
        wd = os.path.join(here, sid)
        if not os.path.exists(os.path.join(wd, "index.json")):
            pytest.skip("no session pack")
        r = S.compile_score("", work_dir=wd)
        check(r)
        assert r.ms < 300


def _counter_film(seat):
    idx = dict(video=dict(width=478, height=850, duration=8.0, fps=30), language="pt", speakers=1, shots=[dict(id=1, start=0, end=8.0)],
               words=[dict(w=f"w{i}", t0=i * 0.5 + 0.1, t1=i * 0.5 + 0.5, confidence=1.0, filler=False) for i in range(14)],
               sentences=[dict(id="s1", wi0=0, wi1=13, t0=0.1, t1=7.0)])
    perc = dict(W=478, H=850, dur=8.0, shots=[dict(id=1, t=[0, 8.0], free=[[0.0, 0.06, 0.72, 0.25]])], samples=[])
    t = '{"k":"film","v":1,"fps":30,"frame":"9:16","spine":"main","band":"P2","seed":3}\n{"k":"b","id":"b01","w":"0-13","job":"x","cut":[40,40,40],"pic":["main","main@k1","main"],"do":[{"g":"counter","from":20,"to":29,"at":"w0..w12.e","step":2,"size":0.1,"seat":%s}]}\n' % seat
    r = S.compile_score(t, index=idx, perception=perc, sid="t", bin_items={}, faces=[])
    return [i for i in r.l1["items"] if ".counter." in i["id"]]


def test_counter_keeps_one_seat_across_cuts():
    ticks = _counter_film('"free"')
    assert len(ticks) > 10
    ys = {round(i["y"] if not isinstance(i["y"], list) else i["y"][0], 3) for i in ticks}
    assert len(ys) == 1, ys


# ---- S6: what the first real film showed. The Score stays, the compiler solves these.

def _chunk_film(extra_do="", cap_in="settle"):
    idx = talk_index(12.0)
    head = ('{"k":"film","fps":30,"band":"S1","seed":3}\n'
            '{"k":"dna","pair":{"a":"grotesk-bold@.03","b":"serif-italic@.034"},"seat":[0.5,0.4],"cap":{"layout":"chunk","in":"%s","gap":0.28,"pool":0}}\n' % cap_in)
    return head + '{"k":"b","id":"b1","w":"0-9","job":"x","pic":"main","type":"0 1 2|3 4 5","do":[%s],"rest":{"snd":"voice"}}\n' % extra_do, idx


def test_a_settling_line_keeps_its_gaps():
    """Each word used to scale about its own centre, so PRODUTO ran into SENSACIONAL on landing."""
    txt, idx = _chunk_film()
    r = compile_(txt, idx)
    ws = [i for i in r.l1["items"] if i["k"] == "word" and i.get("chunk") == "b1.c0"]
    assert len(ws) == 3
    v = r.l1["voices"]["a"]
    ws.sort(key=lambda w: w["x"][-1])
    for a, b in zip(ws, ws[1:]):
        for k in (0, 3):
            s = a["s"][k]
            wa, wb = S.text_px(a["t"], v) / 1080, S.text_px(b["t"], v) / 1080
            end_a = a["x"][k] + wa / 2 + wa * s / 2
            start_b = b["x"][k] + wb / 2 - wb * s / 2
            assert start_b - end_a > 0


def test_a_written_rect_is_a_limit():
    """A crop written to leave something out stays inside itself: p5302's calendar said MARÇO."""
    r = S.fit_inside([0.05, 0.4, 0.62, 0.95], 867, 1300, 1080, 1920)
    assert r[0] >= 0.05 - 1e-6 and r[1] >= 0.4 - 1e-6 and r[2] <= 0.62 + 1e-6 and r[3] <= 0.95 + 1e-6
    assert abs(((r[2] - r[0]) * 867) / ((r[3] - r[1]) * 1300) - 9 / 16) < 1e-3
    grown = S.fit_crop([0.05, 0.4, 0.62, 0.95], 867, 1300, 1080, 1920)
    assert grown[1] < 0.4


def test_a_caption_yields_to_display_type_saying_the_same():
    idx = talk_index(12.0)
    t = " ".join(idx["words"][i]["w"].upper() for i in range(3))
    txt, _ = _chunk_film('{"g":"big","t":"%s","at":"w0","d":40,"seat":[0.5,0.6]}' % t)
    r = compile_(txt, idx)
    assert not [i for i in r.l1["items"] if i.get("chunk") == "b1.c0"]
    assert [i for i in r.l1["items"] if i.get("chunk") == "b1.c1"]


def test_a_tall_face_keeps_the_caption_off_it():
    """Anton stands far above 0.74 em. A block measured at 0.74 let a caption sit on MARAVILHOSO."""
    idx = talk_index(12.0)
    txt, _ = _chunk_film('{"g":"big","t":"MARAVILHOSO","at":"w0","d":60,"size":0.1,"font":"condensed","seat":[0.5,0.45]}')
    r = compile_(txt, idx)
    big = [i for i in r.l1["items"] if i["k"] == "word" and i.get("role") == "big"][0]
    v = r.l1["voices"][big["v"]]
    y = big["y"][-1] if isinstance(big["y"], list) else big["y"]
    top = y - S.cap_height(v["font"]) * v["px"] / 1920
    for c in (i for i in r.l1["items"] if i.get("chunk") == "b1.c0"):
        cy = c["y"][-1] if isinstance(c["y"], list) else c["y"]
        px = r.l1["voices"][c["v"]]["px"]
        assert cy < top or cy - 0.74 * px / 1920 > y


def test_ink_reads_the_picture_under_it(monkeypatch):
    import numpy as np
    monkeypatch.setattr(S, "thumbs", lambda ctx, aid: np.full((1, S.THUMB_H, S.THUMB_W), 0.93, np.float32))
    txt, idx = _chunk_film()
    r = compile_(txt, idx)
    assert {i["ink"] for i in r.l1["items"] if i["k"] == "word"} == {S.DARK_INK}
    monkeypatch.setattr(S, "thumbs", lambda ctx, aid: np.full((1, S.THUMB_H, S.THUMB_W), 0.1, np.float32))
    r = compile_(txt, idx)
    assert {i["ink"] for i in r.l1["items"] if i["k"] == "word"} == {"#FFFFFF"}


def test_a_film_with_no_media_keeps_the_plate_rule():
    txt, idx = _chunk_film()
    r = compile_(txt, idx)
    assert {i["ink"] for i in r.l1["items"] if i["k"] == "word"} == {"#FFFFFF"}


def test_a_word_reads_the_picture_the_film_really_shows(monkeypatch):
    """A salvo replaces the base after the display word's line was reached. Ink read the base, and came out dark on dark."""
    import numpy as np
    monkeypatch.setattr(S, "thumbs", lambda ctx, aid: np.full((1, S.THUMB_H, S.THUMB_W), 0.93 if str(aid).startswith("bin:") else 0.1, np.float32))
    idx = talk_index(12.0)
    txt = ('{"k":"film","fps":30,"band":"S1","seed":3}\n{"k":"dna","pair":{"a":"grotesk-bold@.03","b":"serif-italic@.034"}}\n'
           '{"k":"b","id":"b1","w":"0-9","job":"x","pic":"main","do":[{"g":"big","t":"BLACK FRIDAY","at":"w0","d":40,"seat":[0.5,0.5]},'
           '{"g":"salvo","pic":["bin:0","bin:1"],"at":"beat","f":[3,3]}],"rest":{"snd":"voice","type":"the display is the caption"}}\n')
    r = compile_(txt, idx)
    ws = [i for i in r.l1["items"] if i["k"] == "word" and i["id"].startswith("b1.big")]
    assert ws and {w["ink"] for w in ws} == {S.DARK_INK}


def test_an_anchor_on_a_waiting_word_still_resolves():
    idx = talk_index(12.0)
    txt = ('{"k":"film","fps":30,"band":"S1","seed":3}\n{"k":"dna","pair":{"a":"grotesk-bold@.03","b":"serif-italic@.034"}}\n'
           '{"k":"b","id":"b1","w":"0-9","job":"x","pic":"main","do":[{"g":"big","t":"UM","at":"w1","d":30,"seat":[0.5,0.5]}],'
           '"snd":[{"s":"impact_1","at":"b1.big.0.in"}],"rest":{"type":"the display is the caption"}}\n')
    r = compile_(txt, idx)
    big = [i for i in r.l1["items"] if i["id"] == "b1.big.0"][0]
    assert r.l1["sound"] and r.l1["sound"][0]["event_f"] == big["f"][0]


# ---------------------------------------------------------------- what the rating of p5302 named


def _film(beats, dna=None):
    head = ['{"k":"film","v":1,"fps":30,"frame":"9:16","spine":"main","band":"own","own":{"med":1,"p10":0.5,"p90":2},"seed":3}',
            json.dumps({"k": "dna", "plate": "#101014", "ink": "#FFFFFF", "pair": {"a": "grotesk-bold@.03", "b": "serif-italic@.034"}, "big": "condensed", "seat": [0.5, 0.62], **(dna or {})})]
    return "\n".join(head + [json.dumps(b, ensure_ascii=False) for b in beats]) + "\n"


REST = {"type": "x", "snd": "x", "look": "x"}


def printed_bin(boxes):
    return {"0": {"kind": "image", "path": "bin/0.jpg", "w": 1080, "h": 1920, "seen": [{"t": 0, "text": [{"s": "BLACK FRIDAY", "b": b} for b in boxes], "faces": []}]}}


def test_type_leaves_words_printed_in_the_picture():
    """p5302 at 3.5 s: BLACK FRIDAY stamped over BLACK FRIDAY on the lightbox."""
    box = [0.2, 0.44, 0.8, 0.56]
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "bin:0", "do": [{"g": "big", "t": "BLACK FRIDAY", "at": "@0..@3", "size": 0.1, "seat": [0.5, 0.5]}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10), bin_items=printed_bin([box]))
    w = [i for i in c.l1["items"] if i["k"] == "word"][0]
    y = w["y"][-1] if isinstance(w["y"], list) else w["y"]
    h = 0.1 * 0.74
    assert y - h >= box[3] or y <= box[1], (y, box)


def test_no_clear_seat_is_said_and_pooled():
    boxes = [[0.0, y, 1.0, y + 0.13] for y in (0.05, 0.2, 0.35, 0.5, 0.65, 0.8)]
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "bin:0", "do": [{"g": "big", "t": "DIA 22", "at": "@0..@3", "size": 0.1, "seat": [0.5, 0.5]}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10), bin_items=printed_bin(boxes))
    assert any("printed in the picture" in f for f in c.meter["findings"])
    assert any(i.get("mode") == "pool" for i in c.l1["items"] if i["k"] == "flash")


def test_a_settling_title_never_reaches_the_edges():
    """p5302 at 19 s: LAR NOVO MOVEIS filled the frame edge to edge on its landing frame."""
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": {"plate": "#E11F79"},
                "do": [{"g": "big", "t": "LAR NOVO MOVEIS", "at": "@0..@3", "size": 0.2, "seat": [0.5, 0.5], "in": "settle"}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    w = [i for i in c.l1["items"] if i["k"] == "word"][0]
    v = c.l1["voices"][w["v"]]
    peak = max(w["s"])
    assert S.text_px(w["t"], v) / 1080 * peak <= 0.91


@ARABIC
def test_a_persian_line_gets_a_face_that_holds_it():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main", "do": [{"g": "big", "t": "صدام کن", "at": "@0..@3", "size": 0.09, "font": "grotesk-black:-4", "in": "letters"}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    ws = [i for i in c.l1["items"] if i["k"] == "word"]
    assert len(ws) == 1 and ws[0]["t"] == "صدام کن"            # never split into letters
    v = c.l1["voices"][ws[0]["v"]]
    assert v["font"].startswith("NotoSansArabic") and v["track"] == 0


@ARABIC
def test_a_nastaliq_ladder_does_not_stack_its_lines_on_each_other():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main", "do": [{"g": "big", "t": "به جون چشات|دیگه جون ندارم", "at": "@0..@3", "size": 0.05, "font": "nastaliq"}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    a, b = [i for i in c.l1["items"] if i["k"] == "word"][:2]
    # By the shaped ink of the face that draws it (Naskh, from the images' fonts-noto-core), not a ratio
    # that held only for the Mac's Nastaliq: line two's ink starts below line one's.
    va, vb = c.l1["voices"][a["v"]], c.l1["voices"][b["v"]]
    qa, qb = S.shaped(va["font"], a["t"]), S.shaped(vb["font"], b["t"])
    assert b["y"] - qb[1] * vb["px"] / 1920 > a["y"] + qa[2] * va["px"] / 1920


def test_the_meter_says_a_page_is_flat_and_what_opens_the_film():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "bin:0", "rest": {**REST, "do": "x"}},
               {"k": "b", "id": "b02", "at": "@3..@6", "job": "x", "cut": "pulse 9", "pic": {"plate": "#E11F79"}, "do": [{"g": "big", "t": "LOJA", "at": "@3..@6", "size": 0.1}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10), bin_items={"0": {"kind": "image", "path": "bin/0.jpg", "w": 1080, "h": 1920}})
    ly = c.meter["layers"]
    assert ly["first"] == "bin:0"
    assert ly["flat_s"] >= 2.5 and ly["found_held_s"] >= 2.5
    assert "THIN first second shows bin:0" in c.meter_text


def test_a_card_behind_reaches_the_engine_with_the_matte():
    class W:
        pass
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main", "do": [{"g": "card", "pic": "main", "at": "@0..@3", "w": 0.4, "h": 0.3, "behind": True}], "rest": REST}])
    import tempfile
    d = tempfile.mkdtemp()
    json.dump(wordless_index(10), open(f"{d}/index.json", "w"))
    open(f"{d}/matte.mp4", "w").close()
    c = S.compile_score(t, work_dir=d)
    assert any(i.get("behind") for i in c.l1["items"] if i["k"] == "card")
    assert "matte" in c.l1["assets"]


@ARABIC
def test_a_sung_line_inks_word_by_word_right_to_left():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@4", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "line", "t": "صدام کن دیگه", "at": "@0..@4", "times": [0.5, 1.5, 2.5], "font": "nastaliq", "size": 0.05, "ghost": 0.25}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    ws = sorted((i for i in c.l1["items"] if i["k"] == "word"), key=lambda i: i["id"])
    assert [w["t"] for w in ws] == ["صدام", "کن", "دیگه"]
    xs = [w["x"][0] if isinstance(w["x"], list) else w["x"] for w in ws]
    assert xs[0] > xs[1] > xs[2]                      # the first word stands rightmost
    # the Nastaliq cut was macOS's copy and does not ship: the class reads as the Naskh the images carry
    assert all("Arabic" in c.l1["voices"][w["v"]]["font"] for w in ws)
    assert ws[1]["op"][0] == 0.25 and ws[1]["op"][44] == 0.25 and ws[1]["op"][46] > 0.5


def test_a_chip_takes_a_span_and_a_track_beat_finds_the_nearest_frame():
    idx = wordless_index(10)
    idx["perception"] = {"beats": [0.5, 1.0, 1.5, 2.0, 2.5]}
    t = _film([{"k": "b", "id": "b01", "at": "@0..@4", "job": "x", "cut": "pulse 9", "pic": "main", "do": [{"g": "chip", "t": "#72000A", "at": "b01.in..d2", "bg": "#111113", "size": 0.02, "seat": [0.3, 0.88], "in": "cut"}], "rest": REST}])
    c = S.compile_score(t, index=idx)
    assert not c.meter["findings"], c.meter["findings"]
    card = [i for i in c.l1["items"] if i["k"] == "card"][0]
    assert card["f"] == [0, 45]


def test_recolour_is_not_a_flash_for_the_health_gate():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@1", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "recolour", "key": "#C81E28", "c": "#1FA8A0", "at": f"@{q / 10}..@{(q + 1) / 10}"} for q in range(8)], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    assert len([i for i in c.l1["items"] if i.get("mode") == "recolour"]) == 8


def test_match_pulls_a_bright_found_picture_into_a_dark_take(tmp_path):
    import numpy as np
    d = str(tmp_path)
    open(f"{d}/source.mp4", "w").close()
    os.makedirs(f"{d}/bin")
    open(f"{d}/bin/0.jpg", "w").close()
    os.utime(f"{d}/source.mp4", (1, 1))
    os.utime(f"{d}/bin/0.jpg", (1, 1))
    os.makedirs(f"{d}/thumbs")
    np.save(f"{d}/thumbs/main.npy", np.full((3, S.THUMB_H, S.THUMB_W), 15, np.uint8))
    np.save(f"{d}/thumbs/bin_0.npy", np.full((1, S.THUMB_H, S.THUMB_W), 200, np.uint8))
    json.dump(wordless_index(10), open(f"{d}/index.json", "w"))
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "bin:0", "rest": {**REST, "do": "x"}}], dna={"match": 0.9})
    c = S.compile_score(t, work_dir=d, bin_items={"0": {"kind": "image", "path": "bin/0.jpg", "w": 1080, "h": 1920}})
    s = [i for i in c.l1["items"] if i["k"] == "shot"][0]
    assert s["grade"]["gain"] < 0.8


def test_a_beat_in_seconds_plays_the_words_inside_it():
    """p5297: a film written in seconds over doubtful words grew a 7 s hold beat of them after its end."""
    idx = talk_index(10)
    end = idx["words"][-1]["t1"] + 0.2
    idx["video"]["duration"] = end
    t = _film([{"k": "b", "id": "b01", "at": f"@0..@{end}", "job": "x", "cut": "pulse 9", "pic": "main", "rest": {**REST, "do": "x"}}])
    c = S.compile_score(t, index=idx)
    assert not any(b["id"].startswith("hold") for b in c.meter["by_beat"])
    assert abs(c.meter["dur_s"] - end) < 0.1


def test_a_bare_shot_id_is_its_span():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@4", "job": "x", "cut": [60, 60], "pic": ["main", "main"],
                "do": [{"g": "recolour", "key": "#C81E28", "c": "#1FA8A0", "at": "b01.s1"}, {"g": "chip", "t": "X", "at": "b01.s1", "seat": [0.3, 0.8], "in": "cut"}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    assert not c.meter["findings"], c.meter["findings"]
    r = [i for i in c.l1["items"] if i.get("mode") == "recolour"][0]
    assert r["f"] == [60, 120]


def test_an_anchor_on_a_shot_a_gesture_replaced_still_lands():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@6", "job": "x", "cut": [60, 60, 60], "pic": ["main", "main", "main"],
                "do": [{"g": "split", "pic": ["main", "main"], "at": "b01.s1.in..b01.out", "dir": "v"}, {"g": "flash", "at": "b01.s2.in", "f": 2, "mode": "white"}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    assert not [f for f in c.meter["findings"] if "resolves to nothing" in f], c.meter["findings"]
    assert any(i["k"] == "flash" and i["f"][0] == 120 for i in c.l1["items"])


def test_a_shot_counted_from_one_means_the_last():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@4", "job": "x", "cut": [60, 60], "pic": ["main", "main"], "do": [{"g": "flash", "at": "b01.s2.in", "f": 2, "mode": "white"}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    assert any(i["k"] == "flash" and i["f"][0] == 60 for i in c.l1["items"])


def test_a_buried_sound_is_lifted_and_a_voice_layer_has_no_file():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@4", "job": "x", "cut": "pulse 9", "pic": "main",
                "snd": [{"s": "whoosh_soft_1", "at": "@1", "db": -22}, {"s": "echo", "at": "@2", "d": 0.5, "n": 2, "db": -9}, {"s": "swell", "at": "@3", "d": 0.6}], "rest": {**REST, "do": "x"}}])
    idx = wordless_index(10)
    c = S.compile_score(t, index=idx, perception={"audio": {"step": 0.5, "rms_db": [-14.0] * 40}})
    by = {s["s"]: s for s in c.l1["sound"]}
    assert by["whoosh_soft_1"]["db"] > -10         # -22 would sit 20 dB under the track
    assert by["echo"]["d_s"] == 0.5 and by["echo"]["n"] == 2 and "file" not in by["echo"]
    assert by["swell"]["start_s"] < 3
    assert not S.validate_l1(c.l1)


def test_a_line_the_person_would_hide_reads_in_front_of_him():
    import tempfile
    d = tempfile.mkdtemp()
    json.dump(wordless_index(10), open(f"{d}/index.json", "w"))
    open(f"{d}/matte.mp4", "w").close()
    per = {"samples": [{"t": k / 5, "body": [0.36, 0.3, 0.64, 0.9], "face": [0.45, 0.3, 0.55, 0.36]} for k in range(50)]}
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "big", "t": [{"t": "LAR NOVO GRANDE", "size": 0.12}, {"t": "MOVEIS", "size": 0.04}], "at": "@0..@3", "seat": [0.5, 0.45], "behind": True}], "rest": REST}])
    c = S.compile_score(t, work_dir=d, perception=per)
    ws = {w["t"]: w for w in c.l1["items"] if w["k"] == "word"}
    assert ws["LAR NOVO GRANDE"].get("behind") and not ws["MOVEIS"].get("behind")


def test_type_on_the_display_clock_fills_the_type_stave():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main", "do": [{"g": "line", "t": "a b", "at": "@0..@3"}], "rest": {"snd": "x", "look": "x"}}])
    c = S.compile_score(t, index=wordless_index(10))
    assert not c.meter["blanks"], c.meter["blanks"]


def test_named_ink_that_vanishes_on_its_plate_turns_readable():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": {"plate": "#E11F79"},
                "do": [{"g": "big", "t": [{"t": "OI", "size": 0.05, "ink": "#FFFFFF"}, {"t": "ESPETACULARES", "size": 0.07, "ink": "#E11F79"}], "at": "@0..@3", "seat": [0.5, 0.3]}], "rest": REST}])
    c = S.compile_score(t, index=wordless_index(10))
    w = next(i for i in c.l1["items"] if i["k"] == "word" and i["t"] == "ESPETACULARES")
    assert w["ink"] == "#FFFFFF"
