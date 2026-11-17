"""Offline tests of the proof reel's editing devices (devices.py and proof.lay_devices): no model call, no stack, no render.

    $PY -m pytest -q test_devices.py
"""
import json
import os

from test_proof import P

D, F = P.D, P.F
FPS = 30
LIB = {n: dict(file=f"{n}.flac", peak_s=0.02, max_db=-3.0, id=n) for n in ("typing_2", "click_1", "tick_1", "whoosh_soft_1")}
UPLOAD = dict(path="/x/upload.mp4", w=1920, h=1080, fps=30.0, dur=27.6)
EXPORT = dict(path="/x/export_body.mp4", w=1080, h=1920, fps=30.0, dur=26.6)
EDL_A = dict(keep=[[0.0, 27.6]])
EDL_B = dict(keep=[[0.0, 19.75], [20.01, 27.55]], captions={"mode": "from_transcript"}, frame={"mode": "crop", "ratio": "9:16"},
             overlays=[dict(id="o1", kind="image", start=2.2, duration_s=2.6), dict(id="o2", kind="image", start=7.1, duration_s=2.8),
                       dict(id="o3", kind="image", start=15.6, duration_s=3.0)])
TYPED = "Turn this into a short and show what he describes."


def fake_stills(ff, path, times, out_dir, h=320):
    return [dict(t=round(float(t), 3), path=f"/x/still_{k}.jpg", w=int(h * 16 / 9), h=h) for k, t in enumerate(times)]


def reel(f_after=96, f_end=213):
    """A proof reel as it stands before a device: the export's pictures with the narration's captions over the typed line,
    the after cut in, one sound on a cut inside the line."""
    l1 = dict(l1=1, sid="t", fps=FPS, frame=[1080, 1920], dur_f=f_end, spine=[], items=[], sound=[],
              voices={"cap": {"font": "InterDisplay-Bold.ttf", "px": 72, "track": 0}},
              assets={"pre": dict(kind="video", path="/x/export_body.mp4", w=1080, h=1920, fps=30, dur=26.6),
                      "aft": dict(kind="video", path="/x/product/after.mp4", w=1080, h=1920, fps=30, dur=3.4)})
    l1["items"] += [dict(id="pre", k="shot", f=[0, f_after], pic="pre", src=[0.5, 0.5 + f_after / FPS], blur=[4.0] * f_after),
                    dict(id="aft", k="shot", f=[f_after, f_end], pic="aft", src=[0.0, (f_end - f_after) / FPS])]
    l1["items"] += [dict(id=f"cap{k}", k="word", f=[20 + 8 * k, 28 + 8 * k], t="word", v="cap", x=0.1, y=0.64, role="big", on=20 + 8 * k)
                    for k in range(8)]
    l1["sound"].append(dict(id="s0", s="whoosh_12", file="whoosh_12.flac", event_f=60, start_s=1.9, trim_s=0, db=-10, peak_s=0.05))
    l1["sound"].append(dict(id="s1", s="impact_7", file="impact_7.flac", event_f=200, start_s=6.6, trim_s=0, db=-10, peak_s=0.05))
    return l1


def words_of_line(t0=1.1):
    said = ["I", "typed:"] + TYPED.split()
    out, t = [], t0
    for w in said:
        out.append(dict(w=w, t0=round(t, 3), t1=round(t + 0.2, 3), seg=0))
        t += 0.22
    return out


def test_the_reel_gives_way_over_a_device_span_and_keeps_what_lies_outside():
    l1 = reel()
    D.clear_span(l1, 40, 120)
    pre = [it for it in l1["items"] if it["id"].startswith("pre")]
    assert [it["f"] for it in pre] == [[0, 40]]
    assert len(pre[0]["blur"]) == 40                               # per-frame values cut with the item
    aft = next(it for it in l1["items"] if it["id"].startswith("aft"))
    assert aft["f"] == [120, 213] and abs(aft["src"][0] - (120 - 96) / FPS) < 1e-3     # the after enters where the device leaves it
    assert all(it["f"][1] <= 40 or it["f"][0] >= 120 for it in l1["items"])
    l2 = reel()
    D.clear_span(l2, 0, 60, keep=D.is_caption)                     # the triptych keeps the narration's captions
    assert sum(1 for it in l2["items"] if it["k"] == "word") == 8
    assert D.drop_sounds(l2, 40, 120) == 1 and [s["id"] for s in l2["sound"]] == ["s1"]


def test_receipt_rows_are_read_from_the_edit_lists_never_written():
    rows = D.receipt_rows(EDL_A, EDL_B, 27.6, 27.29)
    assert [r["text"] for r in rows] == ["−0.3 s cut", "+3 pictures", "+ captions"]
    assert rows[0]["value"] == 0.31 and rows[1]["value"] == 3
    assert D.receipt_rows(EDL_A, dict(keep=[[0.0, 27.6]], frame={"mode": "crop", "ratio": "9:16"}), 27.6, 27.6)[0]["text"] == "9:16 reframe"
    assert D.receipt_rows(EDL_A, EDL_A, 27.6, 27.6) == []


def test_the_typed_words_land_when_the_narrator_says_them():
    ws = words_of_line()
    wf = D.word_frames(ws, TYPED, F.norm_word)
    assert len(wf) == len(TYPED.split())
    assert wf[0][0] == round(ws[2]["t0"] * FPS)                   # "Turn", after "I typed:"
    assert all(a < b for a, b in wf) and all(wf[k][0] <= wf[k + 1][0] for k in range(len(wf) - 1))
    gappy = [w for w in ws if w["w"] != "short"]                   # a word the aligner did not hear takes its neighbours' gap
    wf2 = D.word_frames(gappy, TYPED, F.norm_word)
    k = TYPED.split().index("short")
    assert wf2[k - 1][1] <= wf2[k][0] <= wf2[k + 1][0]
    even = D.word_frames([dict(w="something", t0=1.0, t1=3.0, seg=0)], TYPED, F.norm_word)
    assert len(even) == len(TYPED.split()) and even[0][0] == 30


def lay_timeline(monkeypatch, l1=None):
    monkeypatch.setattr(D, "stills", fake_stills)
    l1 = l1 or reel()
    wf = D.word_frames(words_of_line(), TYPED, F.norm_word)
    rec, f_end = D.timeline(l1, f0=27, f_send=wf[-1][1], f_after=96, upload=UPLOAD, export=EXPORT, edl_a=EDL_A, edl_b=EDL_B, typed=TYPED,
                            word_f=wf, export_at=6.8, lib=LIB, ff="/x/ffmpeg", work="/x/work", focus=(0.62, 0.25),
                            caption_spans=[(0.2, 1.1), (1.2, 2.0)], versions=(1, 10))
    return l1, rec, f_end


def test_timeline_pulls_back_types_as_said_lands_the_edit_and_pushes_into_the_export(monkeypatch):
    l1, rec, f_end = lay_timeline(monkeypatch)
    items = {it["id"]: it for it in l1["items"]}
    assert rec["frames"] == [27, f_end] and f_end == rec["push"][1]
    pull = items["tl.view.pull"]
    assert pull["s"][0] > 3.0 and pull["s"][-1] < 1.05            # from the whole frame into its seat
    assert rec["viewer"][1] >= D.TOP_Y > D.MARK[3]                 # the viewer stands under the watermark's place
    push = items["tl.view.in"]
    assert push["f"][1] == f_end and abs(push["s"][-1] * (rec["export_in_viewer"][2] - rec["export_in_viewer"][0]) - 1.0) < 0.01
    assert items["tl.view.ex"]["f"][0] == 96 and abs(items["tl.view.ex"]["src"][0] - 6.8) < 1e-3   # the export with its own voice
    chars = [it for it in l1["items"] if it["id"].startswith("tl.say.")]
    assert "".join(it["t"] for it in sorted(chars, key=lambda it: (it["f"][0], it["id"]))) == TYPED.replace(" ", "")
    assert min(it["f"][0] for it in chars) >= round(words_of_line()[2]["t0"] * FPS)
    assert [r["text"] for r in rec["receipt"]] == ["−0.3 s cut", "+3 pictures", "+ captions"] and all("f" in r for r in rec["receipt"])
    assert len(rec["pictures_landed"]) == 3 and rec["removed_s"] > 0.2
    # readable at phone size, inside the safe area, never in the mark's place or under the platform's rail
    assert rec["min_text_px"] >= 44 and rec["typed_px"] >= 52
    for t in rec["texts"]:
        assert t["y"] - t["cap_px"] / 1920 > D.MARK[3]
        assert t["y"] <= D.BOTTOM_Y
        if t["y"] > 0.55 and t["v"] != "dv_l":
            assert t["x"] + t["w"] <= D.RAIL_X + 1e-3
    # the reel gave way: none of its pictures or captions inside the span, its after continues from the push
    assert not [it for it in l1["items"] if not it["id"].startswith("tl.") and it["f"][0] < f_end and it["f"][1] > 27]
    aft = next(it for it in l1["items"] if it["id"].startswith("aft"))
    assert aft["f"][0] == f_end and abs(aft["src"][0] - (f_end - 96) / FPS) < 1e-3
    # a few sounds, each on something that lands: the typing, the send, the blocks moving, two pictures
    snd = [s for s in l1["sound"] if s.get("device") == "tl"]
    assert len(snd) <= 5 and [s["id"] for s in l1["sound"] if not s.get("device")] == ["s1"]
    assert {s["s"] for s in snd} == {"typing_2", "click_1", "whoosh_soft_1", "tick_1"}
    lands = {p["f"] + 1 for p in rec["pictures_landed"]}
    for s in snd:
        assert s["event_f"] in {rec["typed"][0] + 1, rec["send_f"], rec["send_f"] + 8} | lands


def test_timeline_keeps_the_prompt_and_the_tracks_apart(monkeypatch):
    _, rec, _ = lay_timeline(monkeypatch)
    v0, v1 = rec["viewer"][1], rec["viewer"][3]
    p0, p1 = rec["prompt"][1], rec["prompt"][3]
    t0 = min(a for a, _ in rec["tracks"].values())
    assert v1 < p0 < p1 < t0 and max(b for _, b in rec["tracks"].values()) <= D.BOTTOM_Y + 1e-6
    assert set(rec["tracks"]) == {"v", "p", "c"}


def test_wipe_shows_the_sentence_as_said_then_reveals_the_export_at_the_same_instant(monkeypatch):
    l1 = reel()
    wf = D.word_frames(words_of_line(), TYPED, F.norm_word)
    rec, f_end = D.wipe(l1, f0=27, f_send=wf[-1][1], f_after=96, upload=UPLOAD, export=EXPORT, edl_b=EDL_B, typed=TYPED, word_f=wf,
                        export_at=6.8, lib=LIB, focus=(0.62, 0.25))
    seam = next(it for it in l1["items"] if it["id"] == "wp.seam")
    assert seam["k"] == "join" and seam["mode"] == "seam" and seam["stage"] == "top"
    assert seam["x"][0] < 0 and seam["x"][-1] > 1 and seam["f"] == rec["sweep"] and f_end == rec["sweep"][1]
    assert abs(seam["src"][0] - (6.8 - (96 - rec["sweep"][0]) / FPS)) < 1e-3          # the export at the same instant as the after
    assert rec["typed_px"] >= 56 and rec["min_text_px"] >= 44
    words = [it for it in l1["items"] if it["id"].startswith("wp.w")]
    assert len(words) == len(TYPED.split()) and words[0]["f"][0] == wf[0][0]
    assert [s["s"] for s in l1["sound"] if s.get("device") == "wp"] == ["whoosh_soft_1"]
    desk = next(it for it in l1["items"] if it["id"] == "wp.desk")
    assert desk["blur"] >= 20 and desk["pic"] == "dv_up"                                # the clip's own picture, never a black page


def test_grid_counts_up_to_the_real_number_of_films(monkeypatch):
    l1 = reel()
    tiles = [dict(path=f"/x/t{k}.jpg", w=270, h=480, sid=f"s{k}") for k in range(8)]
    rec = D.grid(l1, f0=150, f1=210, tiles=tiles, lib=LIB, upload=UPLOAD)
    counter = sorted((it for it in l1["items"] if ".counter" in it["id"]), key=lambda it: it["f"][0])
    assert [it["t"] for it in counter] == [str(k) for k in range(1, 9)] and counter[-1]["f"][1] == 210
    cards = [it for it in l1["items"] if it["id"].startswith("gr.t")]
    assert len(cards) == 8 and rec["count"] == 8
    assert all(c["y"] - c["h"] / 2 >= D.TOP_Y - 1e-6 for c in cards)
    assert len([s for s in l1["sound"] if s.get("device") == "gr"]) <= 4


def test_triptych_opens_on_three_exports_of_one_instant():
    l1 = reel()
    edl2 = dict(keep=[[0.24, 26.67]])
    exs = [dict(media=dict(EXPORT, path=f"/x/e{k}.mp4"), edl=e) for k, e in enumerate((EDL_B, edl2, EDL_A))]
    s = D.common_second([e["edl"] for e in exs], 2.6, prefer=6.0, dur=27.6)
    assert s is not None and abs(s - 6.0) < 0.3
    rec = D.triptych(l1, f0=0, f1=78, exports=exs, upload=UPLOAD, lib=LIB, src_s=s)
    assert rec["starts"] == [round(s, 3), round(s - 0.24, 3), round(s, 3)]           # each export's own edit list maps the second
    c0 = next(it for it in l1["items"] if it["id"] == "tr.c0")
    assert c0["f"][0] == 0 and c0["op"][0] == 1.0                                      # frame 0 is a real export
    assert sum(1 for it in l1["items"] if it["k"] == "word" and it.get("v") == "cap") == 8   # the hook keeps its captions
    assert all(b[1] > D.MARK[3] for b in rec["boxes"])


def test_common_second_needs_one_uncut_stretch_in_every_cut():
    a = dict(keep=[[0.0, 5.0], [6.0, 10.0]])
    b = dict(keep=[[0.0, 3.0], [3.5, 10.0]])
    s = D.common_second([a, b], 2.0, prefer=0.0, dur=10.0)
    assert s is not None and (s + 2.0 <= 3.0 or (3.5 <= s and s + 2.0 <= 5.0) or s >= 6.0)
    assert D.common_second([dict(keep=[[0.0, 1.0], [1.2, 2.2]])], 1.5, dur=2.0) is None


def test_the_device_is_the_films_choice_and_the_timeline_by_default():
    assert P.device_of({}) == "timeline"
    assert P.device_of(dict(idea_full=dict(device="wipe"))) == "wipe"
    assert P.device_of(dict(idea_full=dict(device="spin"))) == "timeline"
    assert P.device_of(dict(idea_full=dict(device="wipe")), {"device": "triptych"}) == "triptych"
    assert "triptych" in P.DEVICE_MENU and set(P.DEVICE_BRIEF) == set(D.DEVICES)


def test_a_device_that_cannot_be_laid_leaves_the_film_as_built(tmp_path):
    class Ctx:
        supply = str(tmp_path)
        args = {}

        def log(self, m):
            pass
    l1 = reel()
    before = json.dumps(l1, sort_keys=True)
    out = P.lay_devices(Ctx(), l1, dict(proof=dict(after_after=0), segments=[{}, {}]), dict(ffmpeg="/x/ffmpeg", product=dict(paths=[])),
                        None, dict(record=str(tmp_path / "missing.json"), receipts=dict(typed=TYPED)), {})
    assert out["faults"] and not out.get("typed_line") and json.dumps(l1, sort_keys=True) == before


def test_same_upload_finds_the_other_films_of_this_clip(tmp_path):
    def rec(sid, sha, w=1080, h=1920, state="done"):
        d = tmp_path / sid
        (d / "edl").mkdir(parents=True)
        (d / "exports").mkdir()
        (d / "exports" / "final.mp4").write_bytes(b"x")
        (d / "edl" / "v003.json").write_text("{}")
        r = dict(sid=sid, state=state, failures=[], upload=dict(sha256=sha), asks=[dict(text=f"ask {sid}")],
                 exports=[dict(tag="final", file=str(d / "exports" / "final.mp4"), sha256=sid, w=w, h=h, edl_version=3, dur=20.0)])
        (d / "record.json").write_text(json.dumps(r))
        return r
    me = rec("me", "A")
    rec("b", "A")
    rec("c", "A")
    rec("wide", "A", w=1920, h=1080)
    rec("other", "B")
    rec("failed", "A", state="failed")
    got = P.same_upload(me, [str(tmp_path), None, str(tmp_path)])
    assert [g["sid"] for g in got] == ["b", "c"] and os.path.basename(got[0]["edl"]) == "v003.json"
