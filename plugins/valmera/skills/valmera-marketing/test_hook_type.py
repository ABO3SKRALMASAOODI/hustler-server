"""The tutorial's first second and its display type (20 Nov, round 1 causes hook-solution-under-pain and
display-items-unanchored). Offline: no model call, no stack, no render.

    $PY -m pytest -q test_hook_type.py
"""
import json
import os

from test_devices import EDL_B, EXPORT, LIB, UPLOAD, reel
from test_proof import P

D, F = P.D, P.F
FPS = 30


class Ctx:
    def __init__(self, supply="/x/supply", args=None):
        self.supply, self.args, self.logs = supply, args or {}, []

    def log(self, m):
        self.logs.append(m)


# ----------------------------------------------------------------- display items reach the screen on their words

def test_plain_display_strings_are_anchored_to_their_lines_own_stressed_word():
    sc = dict(segments=[
        dict(say="Trash can. Black hole. Both on screen.", emphasis=["Black"], show=["TRASH CAN", "BLACK HOLE"]),
        dict(say="Try it at valmera dot io", show=[dict(text="VALMERA.IO", word_i=4, role="key")]),
        dict(say="Five pictures landed", show=[dict(text="+5 PICTURES", word_i=99, role="number", big="+5")]),
        dict(say="Nothing here", show=[]),
    ])
    assert P.anchor_show(sc) == 3
    s0, s1, s2 = (s["show"] for s in sc["segments"][:3])
    assert [x["word_i"] for x in s0] == [2, 2] and all(x["role"] == "key" for x in s0)    # "Black" is word 2 of say
    assert s1 == [dict(text="VALMERA.IO", word_i=4, role="key")]                          # a valid item is kept as written
    assert s2[0]["word_i"] == 0 and s2[0]["role"] == "key" and s2[0]["big"] == "+5"       # out of range: the line's first word
    assert sc["segments"][3]["show"] == []
    assert P.anchor_show(sc) == 0                                                          # anchored items stay anchored


def test_an_anchored_marketing_item_lands_where_the_cutters_anchored_only_rule_dropped_it():
    """The cutter of a film cut by verdicts places an item on its word_i or not at all: the writer's plain strings
    were all dropped ('not found in the words'); anchored, each lands on its word."""
    say = "Trash can. Black hole. Both on screen."
    script = dict(segments=[dict(say=say, emphasis=["Trash"], show=["TRASH CAN"])])
    words, t = [], 0.2
    for w in say.split():
        words.append(dict(w=w, start=round(t, 3), end=round(t + 0.25, 3), seg=0))
        t += 0.3
    clock = F.Clock(dict(words=words, duration=round(t + 0.5, 2)), script)
    voices = {"cap": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0}, "dsp": {"font": "InterDisplay-Black.ttf", "px": 150, "track": -2}}
    dropped = []
    assert F.display_words(F.Plan(voices, {}), clock, script, "dsp", 0.627, F.Clock.f(clock.dur), anchored_only=True, dropped=dropped) == []
    assert dropped[0]["why"] == "not found in the words"
    P.anchor_show(script)
    shown = F.display_words(F.Plan(voices, {}), clock, script, "dsp", 0.627, F.Clock.f(clock.dur), anchored_only=True)
    assert len(shown) == 1 and shown[0][0] == max(F.Clock.f(clock.seg_words(0)[0]["t0"]) - 1, 1)


def test_the_script_checks_and_the_audit_read_display_items_by_their_text():
    rc = {"typed": "Make it a vertical short with bold captions.", "first_result_s": 95}
    segs = [dict(say=f"line {k} words here", show=[], fallback_text="a b") for k in range(8)]
    segs[5]["say"] = "So I typed make it a vertical short with bold captions"
    segs[-1]["show"] = [dict(text="VALMERA.IO", word_i=0, role="key")]
    segs[1]["show"] = [dict(text="3x faster", word_i=0, role="figure")]
    probs = P.script_problems(dict(segments=segs, proof=dict(before_after=2, after_after=5)), rc["typed"], rc)
    assert any("'3x'" in p for p in probs) and not any("call to action" in p for p in probs)


def test_the_writer_is_asked_for_anchored_display_items_and_given_the_opening_beat(monkeypatch):
    seen = {}

    def ask(system, content, **kw):
        seen["system"], seen["content"] = system, content
        return {"segments": []}, {}
    monkeypatch.setattr(P, "ask", ask)
    monkeypatch.setattr(P, "ref_pace", lambda: dict(shot_med_s=0.6, wps=3.0))
    hook = dict(t=[15.49, 18.29], landing=15.79, says="we have a black hole", upload_at=40.2)
    P.write(Ctx(), dict(title="t"), dict(id="reel"), dict(typed="Show what he describes."), hook=hook)
    text = seen["system"] + seen["content"]
    assert "built before-and-after" in text and "we have a black hole" in text and "15.79" in text
    assert "on the hook when it shows the result" not in text and "or its solved result" not in text
    assert '"word_i"' in seen["content"] and "VALMERA.IO" in seen["content"]
    P.write(Ctx(), dict(title="t"), dict(id="reel"), dict(typed="Show what he describes."))
    assert "built before-and-after" not in seen["system"] and "never the finished export under words about a broken before" in seen["system"]


def test_the_style_cards_open_on_the_built_before_and_after():
    cards = json.load(open(os.path.join(os.path.dirname(P.__file__), "reels.json")))["styles"]
    assert cards and all("before-and-after" in c["hook"] and "pain" not in c["hook"] for c in cards)


def test_the_export_check_chooses_the_after_that_holds_the_landings_the_sentence_names():
    import inspect
    src = inspect.getsource(P.export_check)
    assert P.CHECK_V >= 6 and "holds the most of the asked change landing" in src


# ----------------------------------------------------------------- the opening beat

def _record(tmp_path):
    os.makedirs(tmp_path / "edl")
    (tmp_path / "edl" / "v003.json").write_text(json.dumps(EDL_B))
    ex = tmp_path / "export.mp4"
    ex.write_bytes(b"")
    rec = dict(upload=dict(file=UPLOAD["path"], dur=27.6, w=1920, h=1080, sha256="u"), source=dict(file="pod.mp4", t0=100.0, t1=127.6),
               exports=[dict(tag="final", file=str(ex), dur=32.0, edl_version=3, sha256="e", w=1080, h=1920)])
    return rec, str(tmp_path / "record.json")


def test_the_opening_beat_is_the_exports_longest_landing_with_the_speakers_words(tmp_path, monkeypatch):
    rec, path = _record(tmp_path)
    monkeypatch.setattr(P, "endcard_s", lambda: 5.6)
    monkeypatch.setattr(P, "quiet_snap", lambda path, t, span=0.45: round(t, 3))
    monkeypatch.setattr(P, "upload_words", lambda rec: [(14.0, "and"), (15.35, "we"), (15.4, "have"), (15.7, "a"), (16.0, "black"), (16.3, "hole"),
                                                        (19.0, "later")])
    hook = P.hook_beat(rec, path)
    assert hook["t"] == [15.3, 18.1] and hook["landing"] == 15.6         # the 3 s picture at 15.6, entered 0.3 s before it
    assert hook["says"] == "we have a black hole"
    assert hook["upload_at"] == 15.3
    assert P.hook_beat(dict(rec, exports=[dict(rec["exports"][0], edl_version=9)]), path) is None   # no edit list: no beat


def test_the_opening_clip_is_cut_with_its_sound_from_the_frame_the_voice_starts(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    record.write_text(json.dumps(dict(upload=dict(file="raw.mp4", dur=30, sha256="raw"),
                                      exports=[dict(tag="final", file="export.mp4", dur=35, sha256="export")])))
    cuts = []
    monkeypatch.setattr(P, "endcard_s", lambda: 5)
    monkeypatch.setattr(P, "quiet_snap", lambda path, t: t)
    monkeypatch.setattr(P, "cut", lambda src, a, b, out, **kw: cuts.append((src, a, b, os.path.basename(out), kw.get("audio", True))))
    script = dict(record=str(record), export_check=dict(before=[1, 4], after=[12.4, 16.4]),
                  hook=dict(t=[15.3, 18.1], landing=15.6, says="we have a black hole"))
    pc = P.product_clips(script, str(tmp_path / "product"))
    hook = [c for c in cuts if c[3] == "hook.mp4"]
    assert hook == [("export.mp4", round(15.3 + F.LEAD_F / F.FPS, 3), 18.1, "hook.mp4", True)]
    assert pc["hook"]["at"] == 15.3 and pc["hook"]["landing"] == 15.6 and "tease" not in pc
    assert not any(c[3] == "tease.mp4" for c in cuts)


def test_the_film_opens_on_the_split_over_the_speakers_voice_with_a_hit_on_the_landing(tmp_path, monkeypatch):
    rec, path = _record(tmp_path)
    json.dump(rec, open(path, "w"))
    monkeypatch.setattr(D, "media", lambda ff, p: dict(EXPORT, path=p))
    monkeypatch.setattr(P.PX, "sound_facts", lambda ids=(): {n: dict(file=f"{n}.flac", peak_s=0.02, max_db=-3.0, id=n) for n in ids})
    monkeypatch.setattr(P, "face_focus", lambda ff, p, t, work, fb: (0.6, 0.3))
    l1 = reel()
    l1["assets"]["hk"] = dict(kind="video", path="/x/product/hook.mp4", w=1080, h=1920, fps=30, dur=2.7)
    l1["items"].insert(0, dict(id="hk", k="shot", f=[3, 84], pic="hk", src=[0.0, 2.7]))
    pack = dict(ffmpeg="/x/ffmpeg", sounds=dict(LIB), product=dict(paths=["/x/product/after.mp4"], hook=dict(t=[15.4, 18.1], at=15.3, landing=15.6)),
                voices=[dict(person="hook", start=0.0, end=2.7), dict(person="after", start=9.0, end=12.0)])
    rec_d = P.lay_hook(Ctx(str(tmp_path)), l1, pack, dict(record=path))
    assert rec_d["device"] == "split" and rec_d["frames"] == [0, F.Clock.f(2.7 + F.LEAD_F / F.FPS)]
    assert rec_d["export_at"] == 15.3 and rec_d["land_f"] == 9
    hits = {s["s"]: s["event_f"] for s in l1["sound"] if s.get("device") == "sp"}
    assert hits.get("impact_1") == 9 and "whoosh_soft_1" in hits
    f1 = rec_d["frames"][1]
    assert not any(it.get("pic") == "hk" and it["f"][0] < f1 for it in l1["items"])     # the split, not the bare export, opens
    assert P.lay_hook(Ctx(), reel(), dict(pack, voices=[]), dict(record=path)) is None    # no opening voice: no split


def test_a_split_that_cannot_be_laid_leaves_the_export_on_its_voice():
    l1 = reel()
    before = json.dumps(l1, sort_keys=True)
    pack = dict(ffmpeg="/x/ffmpeg", product=dict(paths=[], hook=dict(at=1.0)), voices=[dict(person="hook", start=0.0, end=2.0)])
    out = P.lay_hook(Ctx(), l1, pack, dict(record="/x/missing.json"))
    assert out["fault"] and json.dumps(l1, sort_keys=True) == before


# ----------------------------------------------------------------- the payoff gets the landing it names

def test_each_landing_of_the_export_is_offered_as_a_frame_of_its_own():
    times = P.candidate_times(20.0, [(15.79, 1.02), (2.2, 2.6)])
    assert 16.29 in times and 16.6 not in times and 15.6 in times                 # the landing replaces the nearest second
    assert 2.7 in times and 2.6 not in times
    assert times == sorted(set(times)) and len(times) == len(P.candidate_times(20.0))


def test_a_line_naming_the_landing_can_have_it(tmp_path, monkeypatch):
    from types import SimpleNamespace
    offered = []
    monkeypatch.setattr(P, "still", lambda path, t, w: "aW1hZ2U=")

    def ask(system, content, **kw):
        offered.extend(x["text"] for x in content if x.get("type") == "text" and x["text"].startswith("Candidate"))
        k = next(int(t.split()[1].rstrip(":")) for t in offered if "16.29" in t)
        return {"windows": [dict(line=0, candidate=k)]}, {}
    monkeypatch.setattr(P, "ask", ask)
    sc = dict(receipts={}, script=dict(segments=[dict(say="Black hole too.", pictures=["THE EXPORT"], duration_s=1.0)]))
    rows, _ = P.product_windows(SimpleNamespace(supply=str(tmp_path)), sc, {"THE EXPORT": ("/fixture.mp4", 20.0, [(12.0, 15.0)])},
                                landings={"THE EXPORT": [(15.79, 1.02)]})
    assert rows[0]["center"] == 16.29 and rows[0]["start"] == 15.79


def test_display_words_stay_on_their_words_over_the_recording_above_its_own_captions():
    l1 = dict(assets={"v1": dict(path="/x/upload_3.mp4"), "v2": dict(path="/x/clip.mp4")},
              items=[dict(id="s1", k="shot", f=[0, 100], pic="v2"),
                     dict(id="s2", k="shot", f=[100, 300], pic="v1"),
                     dict(id="cap", k="word", f=[120, 140], v="cap", t="trash", y=0.62),
                     dict(id="big", k="word", f=[130, 165], v="dsp130", t="TRASH CAN", y=[0.71, 0.70, 0.687], role="big"),
                     dict(id="sml", k="word", f=[130, 165], v="dsps130", t="in one sentence", y=[0.76, 0.75, 0.737], role="big"),
                     dict(id="out", k="word", f=[20, 50], v="dsp20", t="EDITORS", y=0.687, role="big")])
    P.as_delivered(l1, {"/x/upload_3.mp4"})
    ids = {it["id"]: it for it in l1["items"]}
    assert "cap" not in ids                                                    # never a caption over the recording
    assert ids["big"]["y"][-1] == P.DISPLAY_OVER_RECORDING_Y
    assert abs(ids["big"]["y"][0] - ids["big"]["y"][-1] - 0.023) < 1e-6        # its landing's rise kept
    assert abs(ids["sml"]["y"][-1] - ids["big"]["y"][-1] - 0.05) < 1e-6        # its small line moves with it
    assert ids["out"]["y"] == 0.687                                            # off the recording: on its seat
