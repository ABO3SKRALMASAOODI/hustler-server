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
    assert [it["t"] for it in sorted(chars, key=lambda it: (it["f"][0], it["id"]))] == TYPED.split()
    assert min(it["f"][0] for it in chars) >= round(words_of_line()[2]["t0"] * FPS)
    assert [r["text"] for r in rec["receipt"]] == ["−0.3 s cut", "+3 pictures", "+ captions"] and all("f" in r for r in rec["receipt"])
    assert len(rec["pictures_landed"]) == 3 and rec["removed_s"] > 0.2
    # readable at phone size, inside the safe area, never in the mark's place or under the platform's rail
    assert rec["min_text_px"] >= 44 and rec["typed_px"] >= 52
    for t in rec["texts"]:
        assert t["y"] - t["cap_px"] / 1920 > D.MARK[3]
        assert t["y"] <= D.BOTTOM_Y
        if t["y"] > 0.55 and not t["v"].startswith("dv_l"):
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
        assert s["event_f"] in {rec["typed"][0], rec["send_f"], rec["send_f"] + 8} | lands


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
    sizes = {k: v["px"] for k, v in l1["voices"].items() if k.startswith("dv_s")}
    assert sizes == {"dv_s.wp": rec["typed_px"]}                                    # its own voice: no other device resizes its words
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
    assert "triptych" not in P.DEVICE_MENU and set(D.DEVICES) <= set(P.DEVICE_BRIEF)       # the triptych: by direction only (17 Nov)


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


def test_the_call_to_action_names_valmera_says_what_it_does_and_what_to_do():
    l1 = reel()
    rec = D.cta(l1, f0=110, f1=l1["dur_f"], export=EXPORT, lib=LIB, src0=20.0)
    lines = [it["t"] for it in sorted((it for it in l1["items"] if it["id"].startswith("ct.l")), key=lambda it: it["f"][0])]
    assert lines[-1] == "valmera.io" and "The AI video editor" in lines and "Type what you want." in lines
    url = next(t for t in rec["texts"] if t["t"] == "valmera.io")
    assert url["px"] >= 150 and url["cap_px"] >= 100                              # the website, large (the end card shows it small)
    text = " ".join(lines).lower()
    assert "free" not in text and "$" not in text and "plan" not in text            # only true claims: the trial is off
    assert rec["seconds"] >= 3.0 and rec["min_text_px"] >= 44 and rec["lands"][-1] < 110 + 30
    for t in rec["texts"]:
        assert D.MARK[3] < t["y"] - t["cap_px"] / 1920 and t["y"] < 0.8 and t["x"] + t["w"] <= 0.95
        assert t["y"] <= 0.55 or t["x"] + t["w"] <= D.RAIL_X + 1e-3
    assert not [it for it in l1["items"] if not it["id"].startswith("ct.") and it["f"][1] > 110]   # the reel gives way under it


UI = dict(dir="/x/ui", w=780, h=1688, typing=[f"type_00_{k:02d}.png" for k in range(len(TYPED.split()) + 1)], sent="type_00_sent.png",
          view="phone", player=[0.0588, 0.3187], site=dict(path="/x/ui/site.png", w=1170, h=2532))


def test_the_showcase_is_split_then_the_real_studio_then_the_edit_then_the_website():
    """Owner, 17 Nov: "How is this marketing the product? ... Where's the editing capability?" The references put both
    versions side by side from frame 0, show the tool's own screen being typed into, and close on the product itself."""
    hook, window = [13.65, 16.45], [0.0, 18.58]
    l1, beats = D.showcase(upload=UPLOAD, export=EXPORT, edl_b=EDL_B, typed=TYPED, window=window, hook=hook, ui=UI, page=UI["site"], lib=LIB,
                           lead_f=3, later="3 MIN")
    mk = beats["marks"]
    assert mk["split"] == [0, 84] and mk["studio"][0] == 84 and mk["site"][1] == l1["dur_f"]
    assert 24 <= l1["dur_f"] / FPS <= 35 - 5                                       # with the end card a 29-35 s film, like the references' 27-33 s
    # frame 0: both versions of the same instant, whole, under their labels
    up = [it for it in l1["items"] if it["id"].startswith("sp.up.")]
    ex = next(it for it in l1["items"] if it["id"] == "sp.ex")
    assert up[0]["f"][0] == 0 and ex["f"][0] == 0 and ex.get("op", 1) == 1 and ex["src"][0] == hook[0]
    assert abs(up[0]["src"][0] - D.src_at(D.program(EDL_B)[0], hook[0])) < 1e-3       # the raw at the export's instant
    assert ex["w"] == up[0]["w"] == 1 and ex["h"] == up[0]["h"]
    labels = {it["t"]: it for it in l1["items"] if it["id"] in ("sp.lab_up", "sp.lab_ex")}
    assert set(labels) == set(D.SPLIT_LABELS) and all(it["f"][0] == 0 for it in labels.values())
    assert "VALMERA" in D.SPLIT_LABELS[1]
    # the Studio: the recording's own photograph for every typed word, the empty composer first, then Send
    shots = sorted((it for it in l1["items"] if it["id"].startswith("st.ui") and it["id"] != "st.ui_sent"), key=lambda it: it["f"][0])
    assert [l1["assets"][it["pic"]]["path"] for it in shots] == [f"/x/ui/{n}" for n in UI["typing"]]
    assert shots[0]["f"][0] == mk["studio"][0] and shots[-1]["f"][1] == mk["send"]
    assert all(b["f"][0] - a["f"][0] == D.WORD_F for a, b in zip(shots[1:], shots[2:]))
    sent = next(it for it in l1["items"] if it["id"] == "st.ui_sent")
    assert sent["f"][0] == mk["send"]
    player = next(it for it in l1["items"] if it["id"] == "st.player")
    assert player["pic"] == "dv_up" and player["f"] == [mk["studio"][0], mk["push"][1]]          # the clip in the Studio is the clip of the split
    rec_u = beats["devices"][1]
    assert rec_u["typed_px"] >= 44                                                   # the sentence reads on a phone
    # the edit: grows out of the Studio on its own voice, then full frame, in step
    res = next(it for it in l1["items"] if it["id"] == "st.result")
    shot = next(it for it in l1["items"] if it["id"] == "export")
    assert res["f"][0] == mk["push"][0] and res["src"][0] == window[0] and shot["f"][0] == mk["push"][1]
    assert abs(shot["src"][0] - (window[0] + (shot["f"][0] - mk["push"][0]) / FPS)) < 1e-3
    assert shot["f"][1] == mk["site"][0] and abs(shot["src"][1] - (window[0] + (mk["site"][0] - mk["push"][0]) / FPS)) < 1e-3
    assert mk["export_voice"] == [mk["push"][0], mk["push"][0] + 557] and mk["site"][0] == mk["export_voice"][1]   # his whole line stays on his picture
    # The real front page fills the short dissolve into the brand card.
    page = next(it for it in l1["items"] if it["id"] == "ct.page")
    assert l1["assets"][page["pic"]]["path"] == "/x/ui/site.png"
    assert page["f"] == mk["site"] and beats["devices"][-1]["brand_join"]
    assert beats["devices"][-1]["texts"] == []
    text = " ".join(it.get("t", "") for it in l1["items"] if it["k"] == "word").lower()
    assert "free" not in text and "$" not in text and "plan" not in text
    for d in beats["devices"]:
        for t in d["texts"]:
            assert (t["x"] + t["w"] < D.MARK[0] or D.MARK[3] < t["y"] - t["cap_px"] / 1920) and t["y"] < 0.8
    for f in range(mk["site"][0]):
        assert sum(1 for it in l1["items"] if it["k"] == "shot" and it["f"][0] <= f < it["f"][1]) == 1, f
    events = {"studio": mk["studio"][0], "typing": mk["type_frames"][0], "send": mk["send"],
              "push": mk["push"][0] + D.SEAT_F}
    assert sorted(s["event_f"] for s in l1["sound"]) == sorted(events.values())


def test_hook_plays_the_selected_sentence_on_the_export_and_upload_clocks(monkeypatch):
    monkeypatch.setattr(P, "export_edl", lambda ex: {"keep": [[1., 6.], [20., 30.]]})
    proof = dict(sentence_ranges={"split":[1, 2]}, spans={"split":[6., 11.]},
                 sentences={"export":[{"text":"Introduction."}, {"text":"First thought.", "t0":6.2},
                                      {"text":"Its consequence."}]})
    hook = P.hook_beat({}, {}, proof)
    assert hook == dict(t=[6., 11.], says="First thought. Its consequence.", landing=6.2, upload_at=21.)


def test_the_studio_photographs_are_read_from_the_recording(tmp_path):
    from PIL import Image
    import numpy as np
    d = tmp_path / "ui"
    d.mkdir()
    a = np.full((1688, 780, 3), 11, np.uint8)
    a[100:539] = (np.arange(780) % 200)[None, :, None]                               # the player's picture, then flat ground
    for n in ["type_00_00.png", "type_00_01.png", "type_00_sent.png", "site.png", "01_uploaded_2.png"]:
        Image.fromarray(a).save(d / n)
    rec = dict(ui=dict(dir=str(d), files=sorted(os.listdir(d)), view="phone"), upload=dict(file=str(tmp_path / "upload.mp4"), w=1920, h=1080))
    ui = P.ui_shots(rec)
    assert ui["typing"] == ["type_00_00.png", "type_00_01.png"] and ui["sent"] == "type_00_sent.png" and ui["site"]["w"] == 780
    assert abs(ui["player"][1] - 539 / 1688) < 2e-3 and abs(ui["player"][0] - (539 - 780 * 1080 / 1920) / 1688) < 2e-3
    assert P.ui_shots(dict(ui=None, upload=dict(file=str(tmp_path / "none" / "upload.mp4"), w=1920, h=1080))) is None
    assert P.later_label(dict(first_result_s=198.2)) is None
    assert P.later_label(dict(first_result_s=198.2, processing_s=31.2)) == "31 SEC"


def test_the_tutorial_closes_on_the_website_too_when_the_recording_has_it():
    l1 = reel()
    rec = D.site(l1, f0=110, f1=l1["dur_f"], page=UI["site"], export=EXPORT, lib=LIB, src0=20.0)
    assert rec["device"] == "cta" and rec["lines"] == ["valmera.io"] and rec["seconds"] >= 3.0
    assert not [it for it in l1["items"] if not it["id"].startswith("ct.") and it["f"][1] > 110]


def test_the_film_chooses_its_format_and_the_tutorial_stays_the_default():
    assert P.format_of({}) == "tutorial"
    assert P.format_of(dict(idea_full=dict(format="showcase"))) == "showcase"
    assert P.format_of(dict(idea_full=dict(format="reel"))) == "tutorial"
    assert P.format_of({}, {"format": "showcase"}) == "showcase"
    assert "showcase" in P.FORMAT_MENU and "triptych" not in P.DEVICE_MENU
    assert set(D.DEVICES) <= set(P.DEVICE_BRIEF)


def test_long_pause_inside_the_selected_thought_does_not_cut_the_sentence():
    rows = [dict(t0=.2, t1=3.), dict(t0=3.4, t1=15.8), dict(t0=16.2, t1=20.)]
    gaps = [(3., 3.4), (6., 9.), (15.8, 16.2)]
    assert P.snap_window(rows, (1, 1), "recording", 20., silence=lambda *a, **kw: gaps) == [3.2, 16.]


def test_the_scale_film_opens_on_every_short_counted_then_the_studio_board_then_one_short_then_the_site():
    """Coordinator, 17 Nov: scale reads as "how did they do that" at a glance: one sentence, a board of finished shorts."""
    tiles = [dict(path=f"/x/short_{k}.mp4", w=1080, h=1920, fps=30.0, dur=40.0, t0=5.0) for k in range(10)]
    ui = dict(UI, board=dict(path="/x/ui/board_01.png", w=780, h=1688))
    typed = "Find the 10 best moments in this podcast and make each one a short."
    l1, beats = D.scale_showcase(upload=UPLOAD, hero=dict(tiles[0]), tiles=tiles, typed=typed, window=[4.0, 13.5], ui=ui, page=UI["site"], lib=LIB,
                                 lead_f=3, later="9 MIN", sub="from a 15-min podcast")
    mk = beats["marks"]
    cards = sorted((it for it in l1["items"] if it["id"].startswith("gl.t") and it["k"] == "card"), key=lambda it: it["f"][0])
    assert len(cards) == 10 and cards[0]["f"][0] == 0 and cards[-1]["f"][0] < 30       # all ten land inside two seconds
    assert len({l1["assets"][it["pic"]]["path"] for it in cards}) == 10                    # ten different films, each playing
    counters = sorted((it for it in l1["items"] if ".counter" in it["id"]), key=lambda it: it["f"][0])
    assert [it["t"] for it in counters] == [str(k) for k in range(1, 11)] and counters[-1]["f"][1] == mk["split"][1]
    assert mk["board"] and mk["board"][1] == mk["push"][0] and next(it for it in l1["items"] if it["id"] == "st.board")
    shot = next(it for it in l1["items"] if it["id"] == "export")
    assert l1["assets"][shot["pic"]]["path"] == "/x/short_0.mp4" and shot["f"] == [mk["push"][1], mk["site"][0]]
    assert 15 <= l1["dur_f"] / FPS <= 30                                             # with the end card a 20-35 s film
    for f in range(mk["site"][0]):
        assert sum(1 for it in l1["items"] if it["k"] == "shot" and it["f"][0] <= f < it["f"][1]) == 1, f
    texts = [t for d in beats["devices"] for t in d["texts"]]
    assert min(t["px"] for t in texts) >= 44
    page = next(it for it in l1["items"] if it["id"] == "ct.page")
    assert page["f"] == mk["site"] and l1["assets"][page["pic"]]["path"] == UI["site"]["path"]


def test_recorded_tutorial_completes_the_upload_on_the_site_beat():
    l1 = reel(f_end=983)
    uploaded = dict(path="/record/type_00_00.png", w=780, h=1688)
    rec = D.site(l1, f0=846, f1=983, page=UI["site"], export=EXPORT, lib=LIB, action_media=uploaded)
    page, action = [next(it for it in l1["items"] if it["id"] == k) for k in ("ct.page", "ct.action")]
    assert page["f"][1] == action["f"][0] == rec["lands"][-1]
    assert page["crop"][15] != page["crop"][-15]
    assert action["s"][15] != action["s"][-15] and action["f"][1] == 983
    assert [(s["s"],s["f"]) for s in rec["sounds"]] == [("click_1", action["f"][0])]


def test_recorded_composer_pins_growing_field_at_its_first_baseline(tmp_path):
    from PIL import Image
    field = dict(text="separate titles", words=[])
    ui = dict(field=field, typing=["empty"], dir=str(tmp_path), w=300)
    for i, top in enumerate((.84, .76)):
        name = f"type_{i}.png"
        im = Image.new("RGB", (300, 500), "#111111")
        im.putpixel((40, round(top*500)+4), (255,255,255))
        im.save(tmp_path/name)
        ui["typing"].append(name)
        field["words"].append(dict(box=[.1,top,.9,.94], scroll=0, font=.04))
    captured = D.recorded_field(ui, "separate titles", str(tmp_path), .76)
    assert captured["height"] == 90
    for path in captured["pictures"]:
        with Image.open(path) as pic:
            assert pic.size == (240,90) and pic.getpixel((10,4)) == (255,255,255)


def test_checked_keyboard_and_rendered_sound_follow_the_final_picture_clock():
    keyboard = "/checked/195963594.jpg"
    l1 = dict(assets={"photo": {"path": keyboard}},
              items=[{"id": "you", "k": "word", "f": [241, 248], "role": "cap"}],
              sound_design={"placed": [dict(t=t, kind="card") for t in (3.30, 5.37, 23.93, 31.63)]},
              sound=[dict(id="ct.snd0", s="click_2", file="click_2.flac", device="ct",
                          event_f=914, start_s=30.318, db=-18)])
    P.keep_checked_layout(l1, {"pictures": [dict(f=[241, 258], path=keyboard)]}, [{"fit": "yes"}])
    assert l1["documentary_cuts"][0]["f"] == [241, 258]
    assert l1["items"][0]["contrast_shadow"] and l1["items"][0]["stroke"]["w"] > 0
    report = P.W3.rendered_sounds(l1, 30)
    assert [(row["t"], row["kind"]) for row in report["placed"]] == [(30.467, "ct")]
    assert report["placed"][0]["rendered"][0]["file"] == "click_2.flac"


# 26 Nov 2026, Marketing film 01 (checklist): the opening never showed the completed checklist, a whoosh played on f2 over
# a split already whole on f0, the sent prompt passed under the watermark at f178-180 and the result inset covered it at
# f197. The checklist's three titles below are the real export's landings (records/01.json, export_check.visible) and the
# bubble's place on the sent photograph is measured from the delivered frames (y 0.29-0.40 after the old 0.30 rise).
CHECKLIST = [(7.933, 19.0), (8.633, 18.3), (10.766, 16.2)]


def test_completed_checklist_and_original_remain_side_by_side_on_its_spoken_span():
    hook = [10.5, 13.]
    scene = reel()
    split = D.split(scene, f0=0, f1=75, upload=UPLOAD, export=EXPORT, edl_b=EDL_B,
                    export_at=hook[0], lib=LIB)
    assert split["upload_box"][3] <= split["export_box"][1]
    edited = next(it for it in scene["items"] if it["id"] == "sp.ex")
    assert edited["src"] == hook and edited["f"] == [0, 75]
    assert split["upload_at"] == D.src_at(D.program(EDL_B)[0], hook[0])


def test_a_checked_title_is_reviewed_at_its_entering_and_outgoing_frames():
    export = dict(dur=27., fps=30)
    edl = dict(keep=[[0., 27.]], texts=[dict(start=11.6, end=12.7, text="The checklist")])
    frames = {round(t * 30) for t in P.review_times(export, edl, [])}
    assert {347, 348, 380, 381} <= frames


def test_the_sent_prompt_stays_under_the_mark_and_clear_of_the_growing_result():
    ui = dict(UI, bubble=[0.406, 0.485])
    l1, beats = D.showcase(upload=UPLOAD, export=EXPORT, edl_b=EDL_B, typed=TYPED, window=[0.0, 18.58], hook=[13.65, 16.45], ui=ui,
                           page=UI["site"], lib=LIB, lead_f=3, later="8 MIN")
    mk = beats["marks"]
    sent = next(it for it in l1["items"] if it["id"] == "st.ui_sent")
    res = next(it for it in l1["items"] if it["id"] == "st.result")
    ch = 1080 / 780 * 1688 / 1920
    for f in range(sent["f"][0], sent["f"][1]):
        y, s = sent["y"][f - sent["f"][0]], sent["s"][f - sent["f"][0]]
        top, bot = y + (0.406 - 0.5) * ch * s, y + (0.485 - 0.5) * ch * s
        assert top > D.MARK[3] + 0.02, (f, top)                         # never under the watermark
        if res["f"][0] <= f:  # every frame where the prompt and result are both visible
            i = f - res["f"][0]
            sb = beats["devices"][1]["result_seat"]
            h = (sb[3] - sb[1]) * res["s"][i]
            assert res["y"][i] - h / 2 > bot, (f, res["y"][i] - h / 2, bot)   # the result grows below the sentence
    pill = next(it for it in l1["items"] if it["id"] == "st.later.pill")
    assert pill["y"] - pill["h"] / 2 > bot and pill["y"] + pill["h"] / 2 < beats["devices"][1]["result_seat"][1]


def test_the_sent_bubble_is_measured_on_the_photograph(tmp_path):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (780, 1688), (10, 10, 12))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 99, 779, 538], fill=(60, 60, 60))                  # the player
    d.rounded_rectangle([90, 685, 760, 818], 18, fill=(46, 20, 22))     # the sentence's bubble
    d.rectangle([0, 1540, 779, 1660], fill=(30, 30, 34))                # the composer
    im.save(tmp_path / "sent.png")
    b = P.sent_bubble(str(tmp_path / "sent.png"), [0.0588, 0.3187])
    assert b and abs(b[0] - 685 / 1688) < 0.01 and abs(b[1] - 819 / 1688) < 0.01


def test_cassette_and_pc_stretches_keep_the_source_programme_time():
    hook, before, after, stretch = [1., 4.], [5., 9.], [13., 16.], [33., 37.]
    export = dict(EXPORT, dur=41.2)
    scene, beats = D.showcase(upload=UPLOAD, export=export, edl_b={"keep":[[0.,41.2]]}, typed=TYPED,
                              window=stretch, hook=hook, before=before, after=after, ui=UI, page=UI["site"], lib=LIB)
    shots = {it["id"]: it for it in scene["items"] if it["id"] in ("before", "after", "export")}
    assert shots["before"]["src"] == before
    assert shots["after"]["src"][0] <= 14.5 < shots["after"]["src"][1] == after[1]
    assert shots["export"]["src"] == stretch
    assert shots["export"]["f"][1] == beats["cta"][0]


def test_site_waits_until_the_comparison_and_its_diagram_have_finished():
    stretch = [11., 25.76]
    scene, beats = D.showcase(upload=UPLOAD, export=EXPORT, edl_b=EDL_B, typed=TYPED, window=stretch,
                              hook=[0., 3.], before=[3., 6.], after=[6., 10.], ui=UI, page=UI["site"], lib=LIB)
    final = next(it for it in scene["items"] if it["id"] == "export")
    assert final["src"][0] <= 15.5 < 22.266 <= final["src"][1]
    assert abs(final["src"][1] - stretch[1]) <= .5/FPS
    assert final["f"][1] == beats["marks"]["export_voice"][1] == beats["cta"][0]


def measured_read(tmp_path, monkeypatch):
    def narrate(ctx, script, out):
        text = script["segments"][0]["say"]
        words = [dict(w=word, start=i*.2, end=(i+1)*.2, seg=0) for i,word in enumerate(text.split())]
        path = tmp_path / "studio-words.json"
        path.write_text(json.dumps(dict(words=words, duration=words[-1]["end"])))
        return dict(words=str(path), wav="typed.wav", voice="narrator", duration=words[-1]["end"])
    monkeypatch.setattr(P.W3, "narrate_script", narrate)


def test_showcase_supply_plays_the_checked_caption_on_both_recorded_clocks(tmp_path, monkeypatch):
    from types import SimpleNamespace
    measured_read(tmp_path, monkeypatch)

    # Film 04, recorded export-check frames and source ASR on the upload clock.
    words = [(7.05, 7.68, "And"), (8.08, 8.33, "the"), (8.33, 9.05, "NVIDIA"),
             (9.05, 10.08, "processors..."), (10.49, 10.64, "Or"), (10.64, 11.2, "GPUs"),
             (11.2, 11.85, "rather,"), (12.16, 12.41, "you")]
    visible = [dict(seconds=[4.92, 11.633, 25.766, 37.9], how="The 1080×1920 export frames Cuban."),
               dict(seconds=[11.633, 25.766, 28.8], how='The on-screen captions show “processors or GPUs rather,” “ASICs right,” and “CPUs that were”.')]
    edl = dict(keep=[[0, 39.4]], captions=dict(mode="from_transcript", emphasis_words=["GPUs", "ASICs", "CPUs", "AI"]))
    rec = dict(upload=dict(file="u"), words=[dict(t0=a, t1=b, w=w) for a, b, w in words],
               exports=[dict(file="e", dur=39.4, tag="final", edl_version=3)])
    P.jsave(str(tmp_path / "record.json"), rec)
    (tmp_path / "edl").mkdir()
    P.jsave(str(tmp_path / "edl/v003.json"), edl)
    spans = dict(split=[9.,12.], before=[0.,7.68], after=[12.,15.], stretch=[7.8,29.])
    script = dict(record=str(tmp_path / "record.json"), receipts=dict(visible=visible, typed=TYPED),
                  script=dict(proof=dict(spans=spans)))
    monkeypatch.setattr(P, "cut", lambda *args: None)
    monkeypatch.setattr(P.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(P.PX, "sound_facts", lambda *args: {})
    out = P.showcase_supply(SimpleNamespace(sid="caption"), script, str(tmp_path), str(tmp_path / "pack.json"))
    prod = out["product"]
    assert 8.90 <= prod["hook"][0] <= 9.05 and 11.85 <= prod["hook"][1] <= 12.16
    assert prod["upload_at"] == prod["hook"][0]
    assert 7.68 <= prod["window"][0] <= 8.08 and prod["window"][1] >= 28.8
    assert prod["caption_proof"]["seen_s"] == 11.633
    l1 = reel()
    split = D.split(l1, f0=0, f1=120, upload=UPLOAD, export=EXPORT, edl_b=edl,
                    export_at=prod["hook"][0], lib=LIB, caption_proof=prod["caption_proof"])
    assert split["export_box"][2] - split["export_box"][0] >= .95
    assert split["upload_box"][3] <= split["export_box"][1]
    assert split["export_at"] == split["upload_at"]
    assert all(it["k"] != "word" or it["t"] in split["labels"] for it in l1["items"] if it["id"].startswith("sp."))


def test_region_grade_opens_at_equal_scale_and_returns_on_a_registered_wipe():
    # Film 05, Studio v6. The fitted picture has bars, and the second keep starts after a cut.
    edl = dict(keep=[[0, 29.27], [34.07, 61.4]],
               frame=dict(mode="crop", ratio="9:16", focus_x=.67, picture=[0, .035, 1, .965]),
               fx=[dict(type="region", device="region_tone", effect="exposure", start=0, end=56.6,
                        region=dict(shape="ellipse", box=[.31, .07, .92, .54]))])
    l1, beats = D.showcase(upload=dict(UPLOAD, dur=61.4), export=dict(EXPORT, dur=56.6),
                           edl_b=edl, typed=TYPED, window=[1, 11], hook=[28, 30.8], ui=UI, page=UI["site"], lib=LIB)
    opening = beats["devices"][0]
    a, b = opening["upload_box"], opening["export_box"]
    assert a[2]-a[0] == b[2]-b[0] >= .9
    assert a[3] <= b[1] and abs((a[3]-a[1]) - (b[3]-b[1])) < .0001
    raw = [i for i in l1["items"] if i["id"].startswith("sp.up.")]
    assert [i["f"] for i in raw] == [[0, 38], [38, 84]]
    assert abs(raw[1]["src"][0] - 34.07) < .02
    # The same physical feature must land at the same pane coordinate on both sides.
    crop = raw[0]["crop"]
    ex = next(i for i in l1["items"] if i["id"] == "sp.ex")["crop"]
    import renderer
    src, dst = renderer.picture_mapping(1920, 1080, 1080, 1920, "crop", (.67, None), edl["frame"]["picture"])
    for axis in (0, 1):
        for corner in (axis, axis+2):
            actual = (crop[corner]-src[axis]) / (src[axis+2]-src[axis])
            expected = (ex[corner]-dst[axis]) / (dst[axis+2]-dst[axis])
            assert abs(actual-expected) < .001
    wipe = next(i for i in l1["items"] if i["k"] == "join" and i.get("mode") == "seam")
    assert beats["reveal"][1] < wipe["f"][0] < wipe["f"][1] < beats["cta"][0]
    assert wipe["x"][0] <= 0 and wipe["x"][-1] >= 1
    assert abs(wipe["src"][0] - (1 + (wipe["f"][0]-beats["reveal"][0])/30)) < .001


def test_grade_showcase_continues_the_same_thought_after_send(tmp_path, monkeypatch):
    from types import SimpleNamespace
    measured_read(tmp_path, monkeypatch)

    edl = dict(keep=[[0, 29.27], [34.07, 61.4]],
               fx=[dict(type="region", device="region_tone", start=0, end=56.6,
                        region=dict(box=[.31, .07, .92, .54]))])
    rec = dict(upload=dict(file="u"), exports=[dict(file="e", dur=56.6, tag="final", edl_version=6)])
    P.jsave(str(tmp_path / "record.json"), rec)
    (tmp_path / "edl").mkdir()
    P.jsave(str(tmp_path / "edl/v006.json"), edl)
    spans = dict(split=[1.,128/30], before=[0.,1.], after=[128/30,6.], stretch=[6.,11.1])
    script = dict(record=str(tmp_path / "record.json"), receipts=dict(typed=TYPED),
                  script=dict(proof=dict(spans=spans)))
    monkeypatch.setattr(P, "cut", lambda *args: None)
    monkeypatch.setattr(P, "hear_clock", lambda *args: [dict(w="Ends.", t0=10.9, t1=11.)])
    monkeypatch.setattr(P.subprocess, "run", lambda *args, **kw: None)
    monkeypatch.setattr(P.PX, "sound_facts", lambda *args: {})
    result = P.showcase_supply(SimpleNamespace(sid="grade"), script, str(tmp_path), str(tmp_path / "pack.json"))
    product = result["product"]
    assert product["hook"] == [1., 128/30]
    assert product["after"] == [128/30, 6.] and product["window"] == [6., 11.1]
    assert product["marks"]["site"][0] == product["marks"]["export_voice"][1]

def test_recorded_site_occupies_the_brand_join_after_the_last_spoken_frame():
    marks = D.showcase_marks([0, 4], [8, 18], 3, before=[1, 4], after=[4, 7])
    begin, end = marks["site"]
    assert begin == marks["export_voice"][1] == marks["stretch"][1]
    assert end - begin == D.card_join_frames()
    scene = reel(f_end=end)
    photo = dict(path="/x/page.png", w=390, h=844)
    receipt = D.site(scene, f0=begin, f1=end, page=photo, export=EXPORT, lib=LIB)
    assert receipt["brand_join"] and receipt["lines"] == []
    assert [(i["k"], i["f"]) for i in scene["items"] if i["id"].startswith("ct.")] == [("card",[begin,end])]
