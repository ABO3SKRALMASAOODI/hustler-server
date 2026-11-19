"""Offline tests of the one-command proof reel (proof.py): no model call, no stack, no render.

    $PY -m pytest -q test_proof.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    """proof.py on the stories' film.py; this folder's tests (test_film.py) import this folder's film.py, so the path and
    the module table are put back as they were once it is loaded."""
    path, mods = list(sys.path), dict(sys.modules)
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
    spec = importlib.util.spec_from_file_location("wf_proof_test", os.path.join(HERE, "proof.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.path[:] = path
    for k in [k for k in sys.modules if k not in mods]:
        del sys.modules[k]
    return m


P = load()


def test_inventory_images_survive_the_real_writer_adapter():
    inventory = {"pictures": {"lamp": [dict(path="lamp.jpg", inventory_id=0, shows="A lamp")]}}
    content = P.VI.writer_content(inventory, lambda *args, **kwargs: "jpeg-bytes")
    parts = P.writer._oa_parts(content)
    images = [part for part in parts if part["type"] == "input_image"]
    assert images == [{"type": "input_image", "image_url": "data:image/jpeg;base64,jpeg-bytes", "detail": "high"}]


def test_uses_the_stories_film_not_this_folders():
    assert os.path.dirname(P.F.__file__).endswith("valmera-stories")
    assert set(P.cards()) <= set(P.R.STYLES)


def test_boundaries_on_full_stops_and_on_pauses():
    w = [(0.0, "one"), (0.3, "two."), (0.6, "three"), (0.9, "four"), (2.4, "five")]
    assert P.boundaries(w) == {1, 3}


def test_snap_window_starts_and_ends_on_sentences():
    w = [(float(t), f"w{t}" + ("." if t % 8 == 7 else "")) for t in range(60)]
    a, b = P.snap_window(w, 9.2, 31.0)
    assert a == 7.75 and 30.0 <= b <= 41.0


def test_script_problems_catch_untrue_or_missing_parts():
    rc = {"typed": "Make it a vertical short with bold captions.", "first_result_s": 95}
    segs = [dict(say=f"line {k} words here", show=[]) for k in range(8)]
    segs[5]["say"] = "So I typed make it a vertical short with bold captions"
    segs[-1]["show"] = ["VALMERA.IO"]
    sc = dict(segments=segs, proof=dict(before_after=2, after_after=5))
    probs = P.script_problems(sc, rc["typed"], rc)
    assert all("typed sentence" not in p for p in probs)
    sc["proof"] = dict(before_after=5, after_after=7)
    segs[1]["show"] = ["3x faster"]
    segs[2]["say"] = "in 3 minutes"
    probs = P.script_problems(sc, rc["typed"], rc)
    assert any("before_after" in p for p in probs)
    assert any("'3x'" in p for p in probs)
    assert any("digits in say" in p for p in probs)


def test_recording_is_shown_as_delivered():
    l1 = dict(assets={"v1": dict(path="/x/after.mp4"), "v2": dict(path="/x/clip.mp4")},
              items=[dict(id="s1", k="shot", f=[0, 100], pic="v2", look="mono_silver"),
                     dict(id="s2", k="shot", f=[100, 300], pic="v1", look="mono_silver"),
                     dict(id="fl", k="flash", f=[0, 400], mode="pool", a=0.4),
                     dict(id="fx", k="flash", f=[150, 153], mode="fill"),
                     dict(id="gt", k="card", f=[50, 350], z=40, pic="gate", op=[0.9] * 300)])
    spans = P.as_delivered(l1, {"/x/after.mp4"})
    assert spans == [(100, 300)]
    ids = {it["id"]: it for it in l1["items"]}
    assert "look" not in ids["s2"] and ids["s1"]["look"] == "mono_silver"
    assert "fx" not in ids
    pools = sorted(it["f"] for it in l1["items"] if it.get("mode") == "pool")
    assert pools == [[0, 100], [300, 400]]
    gates = sorted((it["f"], len(it["op"])) for it in l1["items"] if it.get("pic") == "gate")
    assert gates == [([50, 100], 50), ([300, 350], 50)]


def test_the_products_crop_frame_stays_on_the_upload_it_is_drawn_on():
    """The before's dim mask and crop edges are drawn over the real upload on purpose; the as-delivered pass keeps them
    while it still clears finishes and captions off the recording."""
    l1 = dict(assets={"v1": dict(path="/x/before.mp4")},
              items=[dict(id="s1", k="shot", f=[0, 75], pic="v1", blur=26),
                     dict(id="c1", k="card", f=[0, 75], z=1, pic="v1"),
                     dict(id="m1", k="card", f=[0, 75], z=2, pic="plate", keep_over_proof=True),
                     dict(id="gt", k="card", f=[0, 75], z=40, pic="gate")])
    P.as_delivered(l1, {"/x/before.mp4"})
    ids = {it["id"] for it in l1["items"]}
    assert {"s1", "c1", "m1"} <= ids and "gt" not in ids


def test_recording_does_not_acquire_new_captions_even_on_short_cuts():
    scene = dict(assets={"raw": dict(path="/product/upload.mp4")}, items=[
        dict(id="upload", k="shot", f=[20, 35], pic="raw", z=0),
        dict(id="fake_caption", k="word", f=[15, 30], text="ADDED"),
        dict(id="outside", k="word", f=[40, 60], text="VALMERA.IO"),
    ])
    assert P.as_delivered(scene, {"/product/upload.mp4"}) == [(20, 35)]
    words = [it for it in scene["items"] if it["k"] == "word"]
    assert not any(w["f"][0] < 35 and w["f"][1] > 20 for w in words)          # nothing captions the recording
    assert sorted(w["f"] for w in words) == [[15, 20], [40, 60]]              # trimmed at its edge, never deleted whole


def test_unreadable_product_picture_cannot_pass(monkeypatch):
    monkeypatch.setattr(P, "words_under", lambda *args: "the finished edit")
    monkeypatch.setattr(P, "still", lambda *args, **kwargs: None)
    monkeypatch.setattr(P, "ask", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no frames")))
    picture = dict(path="/x/after.mp4", name="after", f=[0, 90], src=[0, 3])
    bad, spend = P.picture_check(None, {"pictures": [picture]}, None, {picture["path"]}, {})
    assert len(bad) == 1 and "could not be inspected" in bad[0][2]
    assert not spend


def test_missing_and_loose_verdicts_fail_including_product(monkeypatch):
    monkeypatch.setattr(P, "words_under", lambda *args: "captions on the words")
    monkeypatch.setattr(P, "still", lambda *args, **kwargs: "jpeg")
    monkeypatch.setattr(P, "ask", lambda *args, **kwargs: (
        {"pictures": [{"n": 1, "fit": "loose", "why": "only the same speaker"}]}, {}))
    pictures = [dict(path=f"/x/{i}.mp4", name="after", f=[i * 30, (i + 1) * 30], src=[0, 1]) for i in range(2)]
    from types import SimpleNamespace
    bad, _ = P.picture_check(None, {"pictures": pictures}, SimpleNamespace(words=[]), {p["path"] for p in pictures}, {})
    assert len(bad) == 2
    assert bad[1][2] == "missing picture verdict"


def test_picture_verdict_is_bound_to_played_window():
    a = dict(path="/x/a.mp4", src=[0, 1])
    b = dict(a, src=[8, 9])
    assert P.picture_key(a, "the edit") != P.picture_key(b, "the edit")


def test_picture_check_supplies_phrase_context_without_widening_exact_words(monkeypatch):
    from types import SimpleNamespace
    clock = SimpleNamespace(words=[dict(w="a", t0=0, t1=.2, seg=0),
                                   dict(w="lamp", t0=.2, t1=.8, seg=0),
                                   dict(w="chairs", t0=1, t1=2, seg=1)])
    seen = []
    monkeypatch.setattr(P, "still", lambda *args, **kw: "jpeg")
    def ask(system, content, **kw):
        seen.extend(x.get("text", "") for x in content)
        return {"pictures": [{"n": 1, "fit": "yes"}]}, {}
    monkeypatch.setattr(P, "ask", ask)
    picture = dict(path="lamp.jpg", f=[0, 6], src=[0, 0])
    bad, _ = P.picture_check(None, {"pictures": [picture]}, clock, set(), {})
    assert not bad
    text = " ".join(seen)
    assert "words: 'a'" in text and "fragments only: 'a lamp'" in text
    assert "chairs" not in text


def test_proof_holds_stay_bounded_after_audio_snap(tmp_path, monkeypatch):
    import json
    record = tmp_path / "record.json"
    record.write_text(json.dumps(dict(upload=dict(file="raw.mp4", dur=30, sha256="raw"),
                                      exports=[dict(tag="final", file="export.mp4", dur=35, sha256="export")])))
    cuts = []
    monkeypatch.setattr(P, "endcard_s", lambda: 5)
    monkeypatch.setattr(P, "quiet_snap", lambda path, t: t + (0.4 if t > 4 else -0.1))
    monkeypatch.setattr(P, "cut", lambda src, a, b, out, **kw: cuts.append((src, a, b, out)))
    script = dict(record=str(record), export_check=dict(before=[1, 9], after=[4, 13], tease_s=15))
    product = P.product_clips(script, str(tmp_path / "product"))
    assert product["before"]["t"][1] - product["before"]["t"][0] <= 3
    assert product["after"]["t"][1] - product["after"]["t"][0] <= 4
    assert len(cuts) == 3 and product["hook"] is None             # no opening beat on the script: no opening clip
    assert cuts[-1][1:] == (0.0, 30, str(tmp_path / "product" / "export_body.mp4"))   # the whole export, for lines short of pictures
    assert product["body"]["t"] == [0.0, 30]


def test_the_before_knows_the_exports_own_crop_of_the_upload(tmp_path):
    """The export's crop of a 16:9 upload, from the recording's own edit list: the ratio's largest window around the
    focus, the focus track overriding it in its seconds (kids-bold-v, measured by template match: x 0.478-0.794)."""
    import json
    os.makedirs(tmp_path / "edl")
    frame = dict(mode="crop", ratio="9:16", focus_x=0.636, focus_y=0.2, focus_track=[dict(x=0.9, y=0.2, t0=4.5, t1=6.5, mode="crop")])
    (tmp_path / "edl" / "v005.json").write_text(json.dumps(dict(frame=frame)))
    rec = dict(upload=dict(w=1920, h=1080), exports=[dict(tag="final", edl_version=5)])
    path = str(tmp_path / "record.json")
    x0, y0, x1, y1 = P.product_crop(path, rec, 2.0)
    assert abs(x0 - 0.4778) < 1e-3 and abs(x1 - 0.7942) < 1e-3 and (y0, y1) == (0.0, 1.0)
    assert P.product_crop(path, rec, 5.0)[2] == 1.0                # the track's seconds: pushed to the right edge
    (tmp_path / "edl" / "v005.json").write_text(json.dumps(dict(frame=dict(mode="fit", ratio="9:16"))))
    assert P.product_crop(path, rec, 2.0) is None                  # a fitted export has no crop to push into


def test_a_line_that_names_the_export_always_shows_it(tmp_path, monkeypatch):
    """16 Nov: the frame choice gave the payoff and call-to-action lines no export frame, and they played as type
    pages. Every line naming the recording still gets one of its shots, even one the model ranked nothing for."""
    from types import SimpleNamespace
    monkeypatch.setattr(P, 'still', lambda path, t, w: 'aW1hZ2U=')
    monkeypatch.setattr(P, 'ask', lambda system, content, **kw: ({'lines': [dict(line=0, shots=[0])]}, {'usd': 0}))
    sc = dict(receipts={}, script=dict(segments=[dict(say='The result', pictures=['THE EXPORT']),
                                                dict(say='Bold captions below the face', pictures=['THE EXPORT']),
                                                dict(say='Try Valmera', pictures=['THE EXPORT'])]))
    src = {'THE EXPORT': dict(path='/fixture.mp4', dur=12, busy=[(4, 7)], hard=[2.0, 9.5], soft=[])}
    rows, ranked, shots, _ = P.product_windows(SimpleNamespace(supply=str(tmp_path)), sc, src, {0: 1, 1: 2, 2: 1})
    per = {i: [r for r in rows if r['line'] == i] for i in range(3)}
    assert all(per[i] for i in range(3)) and ranked == {0: [0]}
    assert len({r['id'] for r in rows}) == len(rows)
    assert all(not (r['end'] > 4 and r['start'] < 7) for r in rows)        # never the after's seconds


def test_instruction_panel_preserves_submitted_sentence():
    class Clock:
        def seg_words(self, i):
            return [dict(t0=4.0, t1=8.0)]
    text = "Make a vertical short cropped to the speaker with white captions on black."
    scene = dict(voices={}, items=[])
    panel = P.instruction_panel(scene, Clock(), dict(proof=dict(after_after=2)), text)
    assert " ".join(panel["lines"]) == text
    assert all(v["px"] >= 38 for v in scene["voices"].values())
    assert panel["frames"] == [120, 240]


def test_reused_recording_is_hash_verified_and_keeps_provenance(tmp_path):
    import hashlib
    import json
    import pytest
    original = tmp_path / "original"
    original.mkdir()
    source = original / "upload.mp4"
    source.write_bytes(b"original fixture")
    media = dict(file=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    record = original / "record.json"
    record.write_text(json.dumps(dict(sid="synthetic", upload=media, exports=[media],
                                      recorded_at="2026-11-13", be_sha="historical-revision")))
    destination = tmp_path / "copy"
    copied = P.reuse_record(str(record), str(destination))
    assert copied["upload"]["file"] == str(destination / "upload.mp4")
    assert copied["reused_from"]["be_sha"] == "historical-revision"
    assert source.read_bytes() == b"original fixture"
    source.write_bytes(b"modified")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        P.reuse_record(str(record), str(tmp_path / "tampered"))


def test_claim_audit_receives_viewer_copy_not_production_notes(monkeypatch):
    seen = []
    def ask(system, content, **kwargs):
        seen.append(content)
        return {"claims": [{"text": "Bold captions", "supported": True}]}, {}
    monkeypatch.setattr(P, "ask", ask)
    sc = dict(segments=[dict(say="Bold captions", show=["READABLE"],
                             see=[dict(must_show="production-only-placement")], direction="actor-only-note")])
    assert P.audit(sc, {})[0] == []
    assert "Bold captions" in seen[0] and "READABLE" in seen[0]
    assert "production-only-placement" not in seen[0] and "actor-only-note" not in seen[0]


def test_photo_plan_preserves_visual_requirement_and_resumes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    ctx = SimpleNamespace(supply=str(tmp_path))
    subject = dict(name="Hands typing", searches=["typing laptop tutorial 4K"],
                   must_show="Both hands type on the visible laptop keys.")
    calls = []
    def response(*args, **kwargs):
        calls.append(1)
        return {"subjects": [{"name": "Hands typing", "searches": ["typing keyboard", "laptop hands"]}]}, {"usd": 0}
    monkeypatch.setattr(P, "ask", response)
    briefs, _ = P.photo_briefs(ctx, [subject])
    assert briefs[0]["must_show"] == subject["must_show"]
    assert briefs[0]["searches"] == ["typing keyboard", "laptop hands"]
    assert subject["searches"] == ["typing laptop tutorial 4K"]
    assert P.photo_briefs(ctx, [subject]) == (briefs, [])
    assert len(calls) == 1


def test_product_windows_rank_the_recordings_own_shots_for_every_line(tmp_path, monkeypatch):
    """20 Nov: the export was offered as 1 s candidates on a 1 s grid, at most three a line, and each recording line got
    one slice. Now it is offered as its own shots (cut at its edit list's events), the model ranks them for every line's
    words, and a line takes its ranked shots up to its demand, each shot to one line; bad ids are ignored."""
    from types import SimpleNamespace
    seen = []
    monkeypatch.setattr(P, 'still', lambda path, t, w: 'aW1hZ2U=')

    def response(system, content, **kwargs):
        seen.extend(x.get('text', '') for x in content)
        return {'lines': [dict(line=0, shots=[1, 0, 1, 999]), dict(line=1, shots=[1, 2]), dict(line=2, shots=[3])]}, {'usd': 0}
    monkeypatch.setattr(P, 'ask', response)
    sc = dict(receipts={}, script=dict(segments=[dict(say='Trash can lands on his words', pictures=['THE EXPORT']),
                                                dict(say='Black hole too', pictures=['THE EXPORT']),
                                                dict(say='Editors lose hours', pictures=['A timeline'])]))
    src = {'THE EXPORT': dict(path='/fixture.mp4', dur=10, busy=[(1, 3)], hard=[4.0, 6.0, 8.3], soft=[3.4, 6.2, 9.0])}
    rows, ranked, shots, _ = P.product_windows(SimpleNamespace(supply=str(tmp_path)), sc, src, {0: 3, 1: 3, 2: 2})
    # cut at the hard events; a soft one (a caption group, a picture change) only 0.45 s or more from every edge
    assert [(c['start'], c['end']) for c in shots] == [(0.0, 1.0), (3.0, 4.0), (4.0, 6.0), (6.0, 8.3), (8.3, 9.0), (9.0, 10.0)]
    assert ranked == {0: [1, 0], 1: [1, 2], 2: [3]}
    assert len([r['id'] for r in rows]) == len({r['id'] for r in rows})              # each shot to one line
    assert {r['id'] for r in rows if r['line'] == 0} == {0, 1} and {r['id'] for r in rows if r['line'] == 1} == {2}
    assert not [r for r in rows if r['line'] == 2]                                 # a line that does not name the recording takes none
    assert 'Trash can lands on his words' in seen[0] and any('Shot 4: THE EXPORT 8.30-9.00 s' in x for x in seen)
    assert P.product_windows(SimpleNamespace(supply=str(tmp_path)), sc, src, {})[1] == ranked     # cached


def test_picture_words_do_not_include_an_adjacent_unplayed_claim():
    from types import SimpleNamespace
    clock = SimpleNamespace(words=[dict(w='monitor', t0=0, t1=1), dict(w='phone', t0=1, t1=2)])
    assert P.words_under(clock, 0, 30) == 'monitor'
    assert P.words_under(clock, 30, 60) == 'phone'


def test_a_recording_the_product_failed_is_made_again_not_refused(tmp_path, monkeypatch):
    """20 Nov: every index of the recording stack failed (a Whisper model missing offline); the three ideas were cached
    as 'no export' and the resumed run refused them for good."""
    import json, os
    import proof as P
    run = tmp_path / "run"; (run / "tmp" / "studio" / "records" / "s0").mkdir(parents=True)
    rec_p = run / "tmp" / "studio" / "records" / "s0" / "record.json"
    rec_p.write_text(json.dumps(dict(sid="s0", exports=[], failures=["error_reply", "job_failed"])))
    (run / "script_state.json").write_text(json.dumps(dict(i0=dict(ok=False, record=str(rec_p),
        check=dict(ok=False, v=P.CHECK_V, why="no export", stack_v=P.STACK_V)))))

    class Ctx:
        run_dir = str(run); tmp = str(run / "tmp"); run_id = "x-000000"
        def log(self, m): pass
    made = []

    def fake_record(ctx, idea, k, ask_text):
        made.append(k)
        raise RuntimeError("stop after the re-record decision")
    monkeypatch.setattr(P, "record", fake_record)
    monkeypatch.setattr(P, "able_cards", lambda ctx: [dict(id="showcase")])
    try:
        P.script(Ctx(), dict(ideas=[dict(asks=["Make it vertical"], must_see=[[]], style="showcase")]))
    except RuntimeError:
        pass
    assert made == ["i0"]
    assert os.path.isdir(str(run / "tmp" / "studio" / "records" / "s0.failed1"))
    assert P.product_failed(dict(exports=[], failures=["job_failed"]))
    assert not P.product_failed(dict(exports=["e.mp4"], failures=[]))
    assert not P.product_failed(dict(exports=[], failures=[]))


# ----------------------------------------------------------------- 20 Nov round 1: the proof carries the film

def test_shot_list_cuts_at_the_recordings_events_outside_its_busy_seconds():
    shots = P.shot_list(12.5, hard=[2.0, 2.2, 9.0], soft=[0.3, 5.0, 8.8], busy=[(6.0, 7.0)], min_s=0.45, max_s=3.0)
    # 2.0-2.2 is too short and joins the shot before it; 0.3 and 8.8 lie too near an edge; 9.0-12.5 is split in two
    assert shots == [(0.0, 2.2), (2.2, 5.0), (5.0, 6.0), (7.0, 9.0), (9.0, 10.75), (10.75, 12.5)]
    assert all(b - a >= 0.45 and b - a <= 3.0 for a, b in shots)
    assert not any(a < 7.0 and b > 6.0 for a, b in shots)                    # never the seconds shown whole elsewhere


def test_owed_windows_share_ranked_shots_round_by_round_and_owe_a_shot_to_a_line_ranked_nothing():
    cands = [dict(id=k, source="THE EXPORT", center=k + 0.5, start=k, end=k + 1.0) for k in range(6)]
    lines = [dict(i=0, sources=["THE EXPORT"]), dict(i=1, sources=["THE EXPORT"]), dict(i=2, sources=["THE EXPORT"])]
    out = P.owed_windows(lines, cands, {0: [0, 1, 2, 3], 1: [1, 4]}, set(), {0: 3, 1: 3, 2: 1})
    by = {i: [o["id"] for o in out if o["line"] == i] for i in range(3)}
    assert by[1] == [1, 4] and by[0] == [0, 2, 3]                            # the scarcer line first, each shot once
    assert len(by[2]) == 1 and by[2][0] == 5                                 # ranked nothing: the farthest free shot


def test_product_windows_recover_from_an_unanswered_ranking(tmp_path, monkeypatch):
    """An outage after the retries is recovered: every recording line still gets its own shot, and nothing is cached,
    so a resumed run asks again."""
    from types import SimpleNamespace
    monkeypatch.setattr(P, 'still', lambda path, t, w: 'aW1hZ2U=')
    monkeypatch.setattr(P, 'ask', lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503 from the proxy")))
    sc = dict(receipts={}, script=dict(segments=[dict(say='a', pictures=['THE EXPORT']), dict(say='b', pictures=['THE EXPORT'])]))
    logs = []
    rows, ranked, shots, spend = P.product_windows(SimpleNamespace(supply=str(tmp_path), log=logs.append), sc,
                                                   {'THE EXPORT': dict(path='/f.mp4', dur=8, busy=[], hard=[3.0], soft=[])}, {0: 2, 1: 2})
    assert {r['line'] for r in rows} == {0, 1} and ranked is None and not spend and logs     # not ranked, not 'ranked nothing'
    assert not os.path.exists(tmp_path / 'product_windows.json')


def test_board_prints_keep_the_narrations_captions_and_nothing_touches_the_print():
    """20 Nov: as_delivered deleted all 83 caption words of the narration (every recording span sealed). A recording laid
    as a print on the board keeps the captions (they sit below it) and gets no flash or finish; a full-frame recording
    still gets none of the three, its caption words trimmed at its edges."""
    l1 = dict(assets={"v1": dict(path="/x/export_3.mp4"), "v2": dict(path="/x/after.mp4")},
              items=[dict(id="pg", k="shot", f=[0, 60], pic="plate", proof_board=True),
                     dict(id="pr", k="card", f=[0, 60], z=1, pic="v1", proof_board=True, look="story_low"),
                     dict(id="af", k="shot", f=[60, 120], pic="v2"),
                     dict(id="w1", k="word", f=[10, 40], role="cap", v="cap", t="Valmera", x=0.3, y=0.67),
                     dict(id="w2", k="word", f=[50, 80], role="cap", v="cap", t="edits", x=0.5, y=0.67),
                     dict(id="pool", k="flash", f=[10, 40], mode="pool", a=0.5, rect=[0.1, 0.57, 0.9, 0.75]),
                     dict(id="neg", k="flash", f=[30, 32], mode="neg"),
                     dict(id="gt", k="card", f=[0, 150], z=40, pic="finish_gate", op=[0.9] * 150)])
    spans = P.as_delivered(l1, {"/x/export_3.mp4", "/x/after.mp4"})
    ids = {it["id"]: it for it in l1["items"]}
    assert spans == [(60, 120)]                                              # only the full-frame after is sealed
    assert "w1" in ids and "pool" in ids                                     # the caption on the board stays, with its pool
    assert "neg" not in ids and "look" not in ids["pr"]                      # no flash or look on the print
    assert [it["f"] for it in l1["items"] if it["id"].startswith("w2")] == [[50, 60]]
    assert sorted(it["f"] for it in l1["items"] if it.get("pic") == "finish_gate") == [[120, 150]]


def test_product_layout_keeps_full_frame_for_the_voices_and_the_hook_only():
    paths = [f"/p/{n}" for n in ("after.mp4", "before.mp4", "export_0.mp4", "export_body.mp4", "hook.mp4", "upload_1.mp4", "upload_10.mp4",
                                 "upload_2.mp4")]
    whole, board = P.product_layout(paths)
    assert {os.path.basename(p) for p in whole} == {"after.mp4", "before.mp4", "hook.mp4", "upload_1.mp4"}   # one reveal a film
    assert {os.path.basename(p) for p in board} == {"export_0.mp4", "export_body.mp4", "upload_10.mp4", "upload_2.mp4"}
    footage = [dict(i=1, clips=[dict(path="/p/upload_2.mp4"), dict(path="/p/upload_1.mp4")]), dict(i=3, clips=[dict(path="/p/upload_10.mp4")])]
    whole, board = P.product_layout(paths, footage)                          # one reveal a line: its best-ranked upload shot
    assert {os.path.basename(p) for p in whole} == {"after.mp4", "before.mp4", "hook.mp4", "upload_2.mp4", "upload_10.mp4"}
    assert P.upload_reveals(paths, footage) == ["/p/upload_2.mp4", "/p/upload_10.mp4"]


def _line_words(segs):
    words, t = [], 0.0
    for i, s_ in enumerate(segs):
        for w in s_["say"].split():
            words.append(dict(w=w, start=round(t, 3), end=round(t + 0.25, 3), seg=i))
            t += 1 / 3.2
        t += 0.22
    return dict(words=words, duration=round(t, 2))


def test_an_unanswered_ranking_still_fills_short_lines_with_unshown_export_shots(tmp_path, monkeypatch):
    """20 Nov review: on an outage product_windows returned ranked={} and the fill read it as 'every line ranked
    nothing', so no line got a proof fill and each recording line looped its one owed shot. Now an unanswered call
    leaves the ranking out and the cut's proof tier plays export shots no file has shown, each second once."""
    from types import SimpleNamespace
    monkeypatch.setattr(P, 'still', lambda path, t, w: 'aW1hZ2U=')
    monkeypatch.setattr(P, 'ask', lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503 from the proxy")))
    segs = [dict(say=" ".join(f"w{i}x{k}" for k in range(16)), pictures=["THE EXPORT"], beat="setup",
                 see=[dict(thing="THE EXPORT", as_of="x", must_show="x")]) for i in range(2)]
    sc = dict(title="t", segments=segs)
    src = {'THE EXPORT': dict(path='/f.mp4', dur=20.0, busy=[(16.0, 19.0)], hard=[2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0], soft=[])}
    rows, ranked, shots, _ = P.product_windows(SimpleNamespace(supply=str(tmp_path), log=lambda m: None), dict(receipts={}, script=sc),
                                               src, {0: 1, 1: 1})
    assert ranked is None and len(rows) == 2
    body = tmp_path / "export_body.mp4"
    body.write_bytes(b"x")
    fill = P.proof_fill_clip(dict(path=str(body), file="product/export_body.mp4", subject="THE EXPORT", seconds=20.0, w=1080, h=1920,
                                  motion=dict(p90=2.0, curve=[2.0] * 30)), (16.0, 19.0), "exp-sha", shots, ranked, [r["id"] for r in rows])
    assert "ranked" not in fill and len(fill["shots"]) == len(shots)
    footage = {r["line"]: [dict(path=str(tmp_path / f"export_{k}.mp4"), seconds=r["end"] - r["start"], subject="THE EXPORT", w=1080, h=1920,
                                motion=dict(p90=2.0, curve=[2.0] * 30), origin=dict(key="exp-sha", t0=r["start"]))]
               for k, r in enumerate(rows)}
    sp = P.R.Supply({}, footage, fit={c["path"]: {0: [i]} for i, v in footage.items() for c in v})
    clock = P.F.Clock(_line_words(segs), sc)
    tl = P.R.cut_fit(sc, clock, sp, P.F.Clock.f(clock.dur) + 15, stress=[], proof_fill=[fill])
    proof = [r[4] for r in tl if r[4].get("tier") == "proof"]
    assert proof                                                             # the fill is on, not off for the whole film
    own = [(r["start"], r["end"]) for r in rows]
    played = sorted(tuple(p["src"]) for p in proof)
    for a, b in played:
        assert any(x <= a and b <= y for x, y, _ in fill["shots"])          # whole export shots, never a 1 s slice
        assert not (a < 19.0 and b > 16.0)                                  # never the after's seconds
        assert not any(a < y and b > x for x, y in own)                      # never a second a line's own file plays
    assert all(p_[1] <= q[0] for p_, q in zip(played, played[1:]))           # each export second once
    assert len({p["take_i"] for p in proof}) == len(proof)                   # each shot one picture


def _frames(kind, n=6, w=640, h=480):
    import numpy as np
    rng = np.random.default_rng(3)
    out = []
    for k in range(n):
        if kind == "sharp":                                                            # crisp detail everywhere
            g = rng.integers(0, 255, (h, w)).astype(np.float32)
        elif kind == "soft":                                                           # a blurred picture that changes
            g = rng.integers(0, 255, (h, w)).astype(np.float32)
            for axis in (0, 1):
                g = np.apply_along_axis(lambda v: np.convolve(v, np.ones(4) / 4, mode="same"), axis, g)
            g = (g - g.mean()) * 4 + 110
        else:                                                                          # a dark stage, a speaker moving
            g = np.full((h, w), 20.0, np.float32)
            yy, xx = np.mgrid[0:h, 0:w]
            g[(xx - 100 - 80 * k) ** 2 + (yy - 200) ** 2 < 70 ** 2] = 180.0
            g[int(0.84 * h):int(0.95 * h), int(0.3 * w):int(0.7 * w)] = 30.0
            for x in range(int(0.3 * w), int(0.7 * w), 12):                           # bright digits that stay put
                g[int(0.86 * h):int(0.93 * h), x:x + 5] = 235.0
        out.append(np.clip(g, 0, 255))
    return out


def test_look_measures_see_an_upscaled_soft_clip_and_a_burned_in_timecode():
    """20 Nov: the ideas call chose 640x480 VHS with a burned-in timecode over the 1920x1080 source, knowing only words.
    The look is measured: the crop's upscale, edge acuity at phone size, and a static text band (on the owner's real
    sources: 1920x1080 acuity 1.04, no band; the 640x480 VHS 0.61 with its timecode found at y 0.875-0.958)."""
    sd = P.look_measures(_frames("soft"), 640, 480)
    assert sd["crop_px"] == 270 and sd["upscale"] == 4.0 and sd["overlay"] is None
    tc = P.look_measures(_frames("timecode"), 640, 480)
    assert tc["overlay"] and tc["overlay"][1] >= 0.75 and tc["overlay"][0] <= 0.35 and tc["overlay"][2] >= 0.65
    hd = P.look_measures(_frames("sharp", w=1920, h=1080), 1920, 1080)
    assert hd["upscale"] == round(1080 / 607.5, 2) and hd["acuity"] > sd["acuity"] * 1.5
    assert "burned-in band" in P.look_line(tc) and "no burned-in band" in P.look_line(sd)


def test_the_ideas_call_sees_each_clips_picture(monkeypatch):
    seen = {}
    monkeypatch.setattr(P, "able_cards", lambda ctx: [dict(id="reel", look="l", hook="h")])
    monkeypatch.setattr(P, "sources", lambda: [dict(id="hd", file="/x/hd.mp4"), dict(id="vhs", file="/x/vhs.mp4")])
    monkeypatch.setattr(P, "ledger_rows", lambda ctx: [])
    monkeypatch.setattr(P, "transcript_windows", lambda s, used: [dict(t0=10.0 * k, t1=10.0 * k + 9, text="w") for k in range(9)])
    monkeypatch.setattr(P, "references", lambda: [])
    monkeypatch.setattr(P, "ref_pace", lambda: {})
    monkeypatch.setattr(P, "source_look", lambda path, *a, **k: dict(w=640, h=480, crop_px=270, upscale=4.0, acuity=0.61, overlay=[0.3, 0.8, 0.7, 0.95])
                        if "vhs" in path else dict(w=1920, h=1080, crop_px=608, upscale=1.78, acuity=1.04, overlay=None))
    asked = []
    monkeypatch.setattr(P, "frames", lambda path, times, w=288: asked.append((path, w)) or ["aW1n"] * len(times))

    def ask(system, content, **kw):
        seen.update(system=system, content=content)
        return dict(ideas=[dict(title="t", source="hd", asks=["a"], style="reel")]), dict(usd=0)
    monkeypatch.setattr(P, "ask", ask)

    class Ctx:
        workflow = "wf2"
        args = {}
    P.ideas(Ctx())
    text = seen["content"][0]["text"]
    assert "burned-in band" in text and "shown 4.0x at 1080" in text and "edge acuity at phone size 1.04" in text
    images = [x for x in seen["content"] if x.get("type") == "image"]
    assert len(images) == 2 * P.IDEA_FRAMES and {p for p, _ in asked} == {"/x/hd.mp4", "/x/vhs.mp4"}
    assert "look premium" in seen["system"] and "raw footage an editor would get" not in text


def test_the_export_check_reads_its_look_at_full_size_as_a_record_not_a_gate(tmp_path, monkeypatch):
    """The check called a soft 4x VHS upscale 'a clean vertical cut' from 288 px frames. It now reads four 720 px frames
    and the measured look; the look is recorded and never refuses the recording."""
    sizes = []
    monkeypatch.setattr(P, "frames", lambda path, times, w=288: sizes.append(w) or ["aW1n"] * len(times))
    monkeypatch.setattr(P, "spoken", lambda rec, ex: (None, None))
    monkeypatch.setattr(P, "endcard_s", lambda: 5.6)
    monkeypatch.setattr(P, "source_look", lambda path, *a, **k: dict(w=640, h=480, crop_px=270, upscale=4.0, acuity=0.6, overlay=[0.3, 0.8, 0.7, 0.95]))
    texts = []

    def ask(system, content, **kw):
        texts.extend(x.get("text", "") for x in content)
        return dict(elements=[dict(asked="captions", visible=True)], all_visible=True, defects=[], looks="soft, timecode in frame"), {}
    monkeypatch.setattr(P, "ask", ask)
    rec = dict(state="done", upload=dict(file="/u.mp4", w=640, h=480, dur=30.0),
               exports=[dict(tag="final", file="/e.mp4", w=1080, h=1920, dur=30.0)])
    chk = P.export_check(None, rec, "Add captions", [])
    assert chk["ok"] and chk["v"] == P.CHECK_V and chk["look_measured"]["export"]["upscale"] == 4.0
    assert sizes.count(720) == 1 and any("full size" in t for t in texts) and any("burned-in band" in t for t in texts)


def test_recording_lines_get_footage_subjects_from_their_own_words(tmp_path, monkeypatch):
    """20 Nov: the footage search stripped THE EXPORT and searched one line of nine. A line whose only picture is the
    recording gets subjects a model chose for its words (a named one by name, or a new one with its searches)."""
    from types import SimpleNamespace
    sc = dict(segments=[dict(say="Editors lose hours to this", pictures=[]), dict(say="Trash can. Black hole.", pictures=[])],
              named=[dict(name="Editor at a timeline", **{"is": "thing"}, must_show="An editor at a timeline")])
    monkeypatch.setattr(P, "ask", lambda *a, **k: (dict(lines=[
        dict(line=0, subjects=["Editor at a timeline", "Unknown name"]),
        dict(line=1, subjects=[dict(name="Black hole image", video_searches=["black hole first image"], must_show="the black hole")]),
        dict(line=7, subjects=["Editor at a timeline"])]), {"usd": 0}))
    ctx = SimpleNamespace(supply=str(tmp_path), log=lambda m: None)
    subs, spend = P.footage_subjects(ctx, sc, [0, 1])
    assert [n["name"] for n in subs[0]] == ["Editor at a timeline"] and subs[1][0]["video_searches"] == ["black hole first image"]
    P.lend_subjects(sc, subs)
    assert sc["segments"][0]["pictures"] == ["Editor at a timeline"] and sc["segments"][1]["pictures"] == ["Black hole image"]
    assert [n["name"] for n in sc["named"]] == ["Editor at a timeline", "Black hole image"]
    monkeypatch.setattr(P, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached")))
    assert P.footage_subjects(ctx, sc, [0, 1])[0] == subs
    monkeypatch.setattr(P, "ask", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503")))
    assert P.footage_subjects(SimpleNamespace(supply=str(tmp_path / "x"), log=lambda m: None), sc, [0]) == ({}, [])


def test_the_narrations_captions_survive_on_the_board_and_stay_off_the_full_frame_recording(tmp_path):
    """The reel lays the export's shots as prints on the board and the after full frame; as_delivered then keeps the
    narration's captions over the prints and none over the after (20 Nov: captions_sampled 0)."""
    from PIL import Image
    segs = []
    for i in range(3):
        segs.append(dict(say=" ".join(f"w{i}x{k}" for k in range(12)), pictures=["THE EXPORT"], beat=["hook", "setup", "payoff"][i],
                         see=[dict(thing="THE EXPORT", as_of="x", must_show="x")], phrases=[[0, 4], [4, 8], [8, 12]]))
    sc = dict(title="t", segments=segs)
    words, t = [], 0.0
    for i, s_ in enumerate(segs):
        for w in s_["say"].split():
            words.append(dict(w=w, start=round(t, 3), end=round(t + 0.25, 3), seg=i))
            t += 1 / 3.2
        t += 0.22
    footage = {}
    for i in range(3):
        footage[i] = []
        for k in range(3):
            p = str(tmp_path / (("after" if (i, k) == (2, 0) else f"export_{i}{k}") + ".jpg"))
            Image.new("RGB", (1080, 1920), (90, 100, 110)).save(p)
            footage[i].append(dict(path=p, seconds=1.6, subject="THE EXPORT", w=1080, h=1920, motion=dict(p90=2.0, curve=[2.0] * 24),
                                   origin=dict(key="sha", t0=2.0 * (3 * i + k))))
    paths = [c["path"] for v in footage.values() for c in v]
    whole = [p for p in paths if os.path.basename(p) == "after.jpg"]          # the stand-in files are stills named by role
    board = [p for p in paths if p not in whole]
    sup = dict(sid="m", words=dict(words=words, duration=round(t, 2)), narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg", pictures={},
               sounds={}, footage=[dict(i=i, clips=v) for i, v in footage.items()], fit={c["path"]: [i] for i, v in footage.items() for c in v},
               fit_verdicts={}, work=str(tmp_path / "fin"))
    l1, beats = P.R.build(sc, sup, "pinboard", dict(require_fit=True, full_frame_paths=whole, board_paths=board, cap_px=72))
    caps_before = [it for it in l1["items"] if it["k"] == "word" and it.get("role") == "cap"]
    spans = P.as_delivered(l1, set(paths))
    caps = [it for it in l1["items"] if it["k"] == "word" and it.get("role") == "cap"]
    keys = {k for k, a in l1["assets"].items() if a.get("path") in board}
    on_board = [it["f"] for it in l1["items"] if it.get("pic") in keys and it["k"] == "card"]
    assert on_board and caps and len(caps) >= len(caps_before) // 2
    assert any(any(c["f"][0] < b and c["f"][1] > a for c in caps) for a, b in on_board)       # captions under the prints
    assert all(not (c["f"][0] < s1 and c["f"][1] > s0) for c in caps for s0, s1 in spans)    # none over the full-frame after


def test_a_line_with_many_upload_shots_reveals_the_upload_once_and_keeps_its_captions(tmp_path):
    """20 Nov review: every upload_* file was full frame and went through reveal_before, so a line that took five upload
    shots would play the same wide-then-push reveal five times running, all its captions trimmed away. Now the line's
    best-ranked upload shot is the one reveal and its other upload shots lie on the board under its captions."""
    from PIL import Image
    segs = [dict(say=" ".join(f"u{k}" for k in range(22)), pictures=["THE UPLOAD"], beat="setup", phrases=[[0, 6], [6, 12], [12, 18], [18, 22]],
                 see=[dict(thing="THE UPLOAD", as_of="x", must_show="x")])]
    sc = dict(title="t", segments=segs)
    clips = []
    for k in range(4):
        p = str(tmp_path / f"upload_{k}.jpg")
        Image.new("RGB", (1920, 1080), (60 + 30 * k, 90, 110)).save(p)
        clips.append(dict(path=p, seconds=1.6, subject="THE UPLOAD", w=1920, h=1080, motion=dict(p90=2.0, curve=[2.0] * 24),
                          origin=dict(key="up-sha", t0=2.0 * k)))
    paths = [c["path"] for c in clips]
    footage = [dict(i=0, clips=clips)]
    whole, board = P.product_layout(paths, footage)
    reveals = P.upload_reveals(paths, footage)
    assert whole == reveals == [paths[0]] and board == paths[1:]
    sup = dict(sid="m", words=_line_words(segs), narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg", pictures={}, sounds={},
               footage=footage, fit={p: [0] for p in paths}, fit_verdicts={}, work=str(tmp_path / "fin"))
    l1, beats = P.R.build(sc, sup, "pinboard", dict(require_fit=True, full_frame_paths=whole, board_paths=board, whole_frame_paths=reveals,
                                                    whole_frame_crop={paths[0]: [0.3, 0.0, 0.62, 1.0]}, cap_px=72))
    shown = [p for p in beats["pictures"] if p["path"] in paths]
    assert [p["path"] for p in shown if p["mode"] == "product"] == [paths[0]]          # exactly one reveal, its cut-ins on the board
    assert {p["path"] for p in shown if p["mode"] == "proof_print"} and \
        all(p["mode"] == "proof_print" for p in shown if p["path"] != paths[0])       # the others on the board
    spans = P.as_delivered(l1, set(paths))
    caps = [it for it in l1["items"] if it["k"] == "word" and it.get("role") == "cap"]
    keys = {k for k, a in l1["assets"].items() if a.get("path") in board}
    prints = [it["f"] for it in l1["items"] if it.get("pic") in keys and it["k"] == "card"]
    assert len(spans) == 1 and prints                                        # one sealed span, the reveal
    assert any(any(c["f"][0] < b and c["f"][1] > a for c in caps) for a, b in prints)     # captions over the line's board prints
    assert all(not (c["f"][0] < s1 and c["f"][1] > s0) for c in caps for s0, s1 in spans)


def test_a_slow_or_broken_source_frame_is_a_look_not_measured_never_a_stopped_run(tmp_path, monkeypatch):
    """20 Nov review: grey_frame let subprocess.TimeoutExpired and OSError out, and source_look runs inside ideas() and
    export_check, so one slow frame would have crashed the stage. A failed measure is now 'not measured'."""
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")

    def slow(*a, **k):
        raise P.subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)
    monkeypatch.setattr(P.subprocess, "run", slow)
    assert P.grey_frame(str(src), 1.0, 64, 36) is None
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("ffmpeg gone")))
    assert P.grey_frame(str(src), 1.0, 64, 36) is None
    monkeypatch.setattr(P, "probe_wh", lambda path: (64, 36, 10.0))
    monkeypatch.setattr(P, "grey_frame", lambda *a: (_ for _ in ()).throw(MemoryError("frame too large")))
    assert P.source_look(str(src), keep=False) is None
    assert P.look_line(P.source_look(str(src), keep=False)) == "not measured"
