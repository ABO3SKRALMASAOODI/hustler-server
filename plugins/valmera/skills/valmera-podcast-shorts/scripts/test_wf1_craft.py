"""Round1 (19 Nov 2026) default-design and thin-sound, at their source in the podcast workflow.

Type: display words by story role, set at least 1.6x the caption on a seat of their own, a long phrase as a two-size
stack, ink chosen by measured contrast, captions that leave out the display's own spoken words (by position), one
shade a caption page sized to its words. Colour: each cut-in matched part of the way to the take; section finishes
from the story (spoken time), on the take too. Sound and music: the story's turn apart from the payoff, handed to
music_film as --turn. No model, no network."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import wf1_film as film  # noqa: E402
import wf1_short as wf  # noqa: E402
from wf1_layout import seat_display  # noqa: E402

F = film.F


def test_a_display_phrase_is_typeset_by_role_at_least_1_6x_the_caption():
    for sid, st in film.STYLES.items():
        R = F.Ruler({"dsp": dict(st["dsp"])})
        for text, key, role in (("INSECURE OVERACHIEVERS", "OVERACHIEVERS", "hook"), ("FINANCIAL TERMS", None, "name"),
                                ("49%", "49%", "figure"), ("rebuilt the whole team", "team", "turn")):
            rows = film.display_rows(R, st["dsp"], text if st["upper"] else text.lower(), key, role)
            big = max(px for _, px in rows)
            line = max(rows, key=lambda r: r[1])[0]
            R.voices["m"] = dict(st["dsp"], px=big)
            # at least 1.6x the caption, or the key word alone already fills the width the rail leaves
            assert big >= film.DISPLAY_MIN * film.CAP_PX or R.width("m", line) >= 0.97 * film.DISPLAY_W * film.W, (sid, text, rows)
            for line, px in rows:                                   # every line stands inside the display width
                R.voices["m"] = dict(st["dsp"], px=px)
                assert R.width("m", line) <= film.DISPLAY_W * film.W + 1
            assert " ".join(t for t, _ in rows) == (text if st["upper"] else text.lower())
        assert "_fit" not in R.voices
    # the figure takes the role's larger size than a name of the same length would
    st = film.STYLES["footage-cut"]
    R = F.Ruler({"dsp": dict(st["dsp"])})
    assert film.display_rows(R, st["dsp"], "49%", None, "figure")[0][1] > film.display_rows(R, st["dsp"], "49%", None, "name")[0][1]


def test_a_phrase_too_long_for_one_line_stacks_round_its_key_word():
    st = film.STYLES["footage-cut"]
    R = F.Ruler({"dsp": dict(st["dsp"])})
    rows = film.display_rows(R, st["dsp"], "INSECURE OVERACHIEVERS", "overachievers", "hook")
    assert [t for t, _ in rows] == ["INSECURE", "OVERACHIEVERS"] and rows[1][1] > rows[0][1] >= film.CAP_PX


def test_the_display_ink_is_chosen_by_contrast_with_the_ground_under_it():
    assert film.contrast_ink(0.05, "#F6F1E7", ["#FF7A3D"]) == ("#F6F1E7", film.DARK_INK)    # cream on a dark shirt stays
    ink, edge = film.contrast_ink(0.92, "#F6F1E7", ["#FF7A3D"])                                # cream on a white page does not
    assert ink == film.DARK_INK and edge != film.DARK_INK
    assert film.contrast(ink, 0.92) >= film.INK_MIN


def test_the_caption_leaves_out_the_words_the_display_printed_by_their_position():
    script = {"segments": [{"say": "a team of insecure overachievers who love it"}]}
    toks = script["segments"][0]["say"].split()
    words = {"duration": 5, "words": [dict(w=t, start=0.3 * i, end=0.3 * i + 0.28, seg=0) for i, t in enumerate(toks)]}
    clock = F.Clock(words, script)
    span = F.said_span(clock, 0, "insecure overachievers")
    assert [w["w"] for w in span] == ["insecure", "overachievers"]
    plan = F.Plan({"cap": dict(font="InterDisplay-Bold.ttf", px=58, track=0)}, {})
    a = F.Clock.f(span[0]["t0"]) - 1
    F.caption_build(plan, clock, "cap", .65, hold=[(a - 1, a + 34)], omit=span)
    shown = [i["t"] for i in plan.items if i["k"] == "word"]
    assert "overachievers" not in shown and "insecure" not in shown          # never said twice on screen
    assert shown[:3] == ["a", "team", "of"] and shown[-2:] == ["love", "it"]
    # the same word said elsewhere is still captioned: the position decides, never the text
    script2 = {"segments": [{"say": "insecure people and insecure overachievers"}]}
    w2 = {"duration": 3, "words": [dict(w=t, start=0.3 * i, end=0.3 * i + 0.28, seg=0) for i, t in enumerate(script2["segments"][0]["say"].split())]}
    c2 = F.Clock(w2, script2)
    p2 = F.Plan({"cap": dict(font="InterDisplay-Bold.ttf", px=58, track=0)}, {})
    F.caption_build(p2, c2, "cap", .65, omit=F.said_span(c2, 0, "insecure overachievers"))
    assert [i["t"] for i in p2.items if i["k"] == "word"].count("insecure") == 1
    assert F.said_span(c2, 0, "financial terms") == []


def _bar_scene(face, bright=None):
    voices = {"d0": dict(font="InterDisplay-Black.ttf", px=70, track=0), "d1": dict(font="InterDisplay-Black.ttf", px=140, track=0),
              "cap": dict(font="InterDisplay-Black.ttf", px=58, track=0)}
    plan = F.Plan(voices, {})
    plan.word([0, 30], "INSECURE", "d0", .5, .40, align="c", on=0)
    plan.word([2, 30], "OVERACHIEVERS", "d1", .5, .47, align="c", on=0)
    plan.block([0, 30], [.1, .41, .9, .48], "#F2C14E", z=7, bar_on=0)          # the bar: centre .445, its line's baseline .47
    items = film.ink_by_ground(plan.items, voices, bright) if bright else plan.items
    l1 = dict(frame=[1080, 1920], voices=voices, items=items, fps=30)
    l1["items"].insert(0, dict(id="s", k="shot", pic="main", f=[0, 30], crop=[0, 0, 1, 1]))
    seat_display(l1, {"main": dict(faces=[[face[0], face[1], face[2] - face[0], face[3] - face[1]]])}, plan.R)
    words = [i for i in l1["items"] if i["k"] == "word"]
    ys = [i["y"][-1] if isinstance(i["y"], list) else i["y"] for i in words]
    bar = next(i for i in l1["items"] if i.get("pic") == "plate")
    return words, ys, bar


def test_a_contrast_bar_moves_with_its_line_and_a_stack_is_seated_as_one_block():
    words, ys, bar = _bar_scene([.3, .38, .7, .5])
    assert ys[1] != .47 and abs((ys[1] - ys[0]) - .07) < 1e-6                     # the stack moved as one
    assert "bar_on" not in bar and abs(bar["y"] - (ys[1] - .025)) < 1e-4 and abs(bar["w"] - .8) < 1e-6
    # on a bright picture the display takes dark ink and its voice a new name (ink_by_ground): the bar still follows
    words, ys, bar = _bar_scene([.3, .38, .7, .5], bright=[(0, 30)])
    assert words[1]["v"] == "d1d" and ys[1] != .47 and abs(bar["y"] - (ys[1] - .025)) < 1e-4
    # a face no seat clears: the block shrinks about its centre (voices renamed _seatN) and the bar shrinks with it
    words, ys, bar = _bar_scene([.02, .14, .98, .86])
    n = words[1]["v"].count("_seat")
    assert n >= 1 and abs(bar["w"] - .8 * .9 ** n) < 1e-4 and abs(bar["h"] - .07 * .9 ** n) < 1e-4
    assert abs(bar["y"] - (ys[1] - .025 * .9 ** n)) < 1e-4 and "bar_on" not in bar


def test_section_spans_and_cutaway_balance(tmp_path):
    seg_f = [0, 60, 130, 200, 260]
    assert film.section_spans([0, 1, 3], seg_f, 330) == [(0, 129), (199, 259)]
    assert film.section_spans([2], seg_f, 330, min_f=80) == []
    from PIL import Image
    Image.new("RGB", (64, 64), (150, 80, 40)).save(tmp_path / "take.jpg")          # a warm, dim room
    Image.new("RGB", (64, 64), (90, 140, 200)).save(tmp_path / "clip.jpg")         # a cool, bright clip
    g = film.clip_balance(str(tmp_path / "take.jpg"), str(tmp_path / "clip.jpg"))
    assert g["temp"] > 0 and g["gain"] < 1 and set(g) == {"lift", "gamma", "gain", "sat", "contrast", "temp", "tint"}
    assert film.clip_balance(None, str(tmp_path / "clip.jpg")) is None


class _LLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def ask(self, system, content, **kw):
        self.calls.append(kw)
        return json.dumps(self.answer), dict(provider="t", model="t", usd=0.0)

    @staticmethod
    def json_block(text):
        return json.loads(text)


def test_a_directed_beat_and_palette_ask_reach_the_film_together():
    plan = {"contract": wf.DIRECTION_CONTRACT, "beats": [{"w": 55, "role": "turn", "sound": "impact"}],
            "asks": [{"dev": "counter", "at": 85}], "sections": []}
    for contract in (plan["contract"], plan["contract"] - 1):
        forwarded = wf.film_direction(dict(plan, contract=contract))["director"]
        assert forwarded["beats"] == plan["beats"]
        assert forwarded["asks"] == plan["asks"]


def test_the_direction_names_roles_the_turn_and_the_payoff_apart(tmp_path, monkeypatch):
    llm = _LLM(dict(display=[dict(seg=1, text="insecure overachievers", role="hook", key="overachievers"),
                             dict(seg=3, text="49%", role="figure", key="49%"), dict(seg=4, text="x", role="filler")] +
                            [dict(seg=k, text=f"alt {k}", role="name") for k in range(5)],
                    past_segs=[0, 1], payoff_seg=5, turn_seg=3, arc=[]))
    monkeypatch.setattr(wf, "_llm", lambda: llm)
    ctx = SimpleNamespace(supply=str(tmp_path))
    pack = dict(lines=[dict(i=i, text=f"line {i}") for i in range(6)])
    d = wf.direction_of(ctx, dict(title="t"), pack)
    assert llm.calls[0]["effort"] == "high"
    assert d["turn_seg"] == 5 and d["story_turn_seg"] == 3 and d["contract"] == wf.DIRECTION_CONTRACT
    # ranked best first with alternates: up to six are kept, the film lands the first that fit its timing
    assert len(d["display"]) == 6 and [r["role"] for r in d["display"][:3]] == ["hook", "figure", "hook"] and d["display"][1]["accent"] is True
    assert wf.direction_of(ctx, {}, pack)["display"] == json.load(open(tmp_path / "director.json"))["display"] and len(llm.calls) == 1
    # an answer that lays no device still gives the film devices: the signal director's asks
    assert d["asks"] and "the answer laid no device" in " ".join(d["repairs"])
    # an older cache is read again under the new contract; a turn after the payoff is no turn
    json.dump(dict(display=[], turn_seg=2, contract=3), open(tmp_path / "director.json", "w"))
    llm.answer = dict(display=[], past_segs=[], payoff_seg=4, turn_seg=5, arc=[])
    d2 = wf.direction_of(ctx, {}, pack)
    assert len(llm.calls) == 2 and d2["turn_seg"] == 4 and d2["story_turn_seg"] is None


def test_the_music_stage_hands_the_story_turn_with_the_payoff(tmp_path, monkeypatch):
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path), state_root=str(tmp_path), run_id="t", workflow="wf1",
                          log=lambda _: None, args={"music": True})
    seen = []

    def run(ctx, args, **kw):
        seen.append(args[-1])
        raise RuntimeError("test dispatch boundary")
    monkeypatch.setattr(wf.chain, "sh", run)
    monkeypatch.delenv("SHELL_MUSIC_CMD", raising=False)
    wf.chain.music_version(ctx, dict(film="f.mp4"), dict(l1="s.json", turn_s=31.2, story_turn_s=19.6, turn_drop=True))
    assert "--payoff 31.200" in seen[0] and "--turn 19.600" in seen[0] and "--turn-drop" in seen[0]
    wf.chain.music_version(ctx, dict(film="f.mp4"), dict(l1="s.json", turn_s=31.2, story_turn_s=30.9, turn_drop=True))
    assert "--turn " not in seen[1] and "--turn-drop" in seen[1]             # a turn on the payoff is the payoff; music_film reads one
    wf.chain.music_version(ctx, dict(film="f.mp4"), dict(l1="s.json", turn_s=31.2))
    assert "--turn" not in seen[2]                                            # another workflow: no opt-in, today's arrangement
    # the shared-ledger route hands the same opt-in in its args file
    ledger = tmp_path / "used.json"
    json.dump(["t-old"], open(ledger, "w"))
    ctx.args = {"music": True, "music_ledger": str(ledger)}
    wf.chain.music_version(ctx, dict(film="f.mp4"), dict(l1="s.json", turn_s=31.2, turn_drop=True))
    a = json.load(open(tmp_path / "music_args.json"))
    assert a["turn_drop"] is True and a["payoff"] == 31.2 and a["turn"] is None and "turn_drop=bool(a.get(\"turn_drop\"))" in wf.chain.MUSIC_USED


def test_a_display_word_is_found_where_the_transcript_writes_digits_or_an_apostrophe():
    """m12 (round1): '44 minutes' was heard as '44' and "don't" as one word; spoken_forms spelled the figure out only
    and split the apostrophe, so said_at found neither and both display words were lost to timing."""
    assert ["44", "minutes"] in F.spoken_forms("44 minutes") and ["fortyfour", "minutes"] in F.spoken_forms("44 minutes")
    assert F.spoken_forms("don\u2019t need a lot of people") == [["dont", "need", "a", "lot", "of", "people"]]
    assert ["5000"] in F.spoken_forms("$5,000") and ["49"] in F.spoken_forms("49%")
    words = [dict(w=w, start=0.3 * k, end=0.3 * k + 0.25, seg=0 if k < 7 else 1)
             for k, w in enumerate(["we", "talked", "for", "44", "minutes", "or", "whatever.", "you", "don't", "need", "a", "lot", "of", "people,"])]
    clock = F.Clock(dict(duration=5.0, words=words), dict(segments=[dict(say="a"), dict(say="b")]))
    assert F.said_at(clock, 0, "44 minutes")[1] == 2 and F.said_at(clock, 1, "don't need a lot of people")[1] == 6
    assert [w["w"] for w in F.said_span(clock, 1, "don't need a lot of people")][0] == "don't"
