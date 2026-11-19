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
    assert {item["id"] for item in scene["items"]} == {"upload", "outside"}


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
    assert len(cuts) == 4
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
    pages. Every line naming the recording now gets its frames, spread across the export."""
    from types import SimpleNamespace
    monkeypatch.setattr(P, 'still', lambda path, t, w: 'aW1hZ2U=')
    monkeypatch.setattr(P, 'ask', lambda system, content, **kw: ({'windows': [dict(line=0, candidate=0)]}, {'usd': 0}))
    sc = dict(receipts={}, script=dict(segments=[dict(say='The result', pictures=['THE EXPORT'], duration_s=1.0),
                                                dict(say='Bold captions below the face', pictures=['THE EXPORT'], duration_s=2.2),
                                                dict(say='Try Valmera', pictures=['THE EXPORT'], duration_s=1.2)]))
    rows, _ = P.product_windows(SimpleNamespace(supply=str(tmp_path)), sc, {'THE EXPORT': ('/fixture.mp4', 12, [(4, 7)])})
    per = {i: [r for r in rows if r['line'] == i] for i in range(3)}
    assert len(per[0]) == 1 and len(per[1]) == 2 and len(per[2]) == 1
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


def test_product_windows_use_grounded_candidates_once_and_reserve_cutins(tmp_path, monkeypatch):
    from types import SimpleNamespace
    seen = []
    monkeypatch.setattr(P, 'still', lambda path, t, w: 'aW1hZ2U=')
    def response(system, content, **kwargs):
        seen.extend(x.get('text', '') for x in content)
        return {'windows': [dict(line=0, candidate=0), dict(line=1, candidate=0),
                            dict(line=1, candidate=1), dict(line=0, candidate=999)]}, {'usd': 0}
    monkeypatch.setattr(P, 'ask', response)
    sc = dict(receipts={}, script=dict(segments=[dict(say='Lamp shown', pictures=['THE EXPORT']),
                                                dict(say='Chairs shown', pictures=['THE EXPORT'])]))
    rows, _ = P.product_windows(SimpleNamespace(supply=str(tmp_path)), sc,
                                {'THE EXPORT': ('/fixture.mp4', 6, [(1, 3)])})
    assert len(rows) == 2 and len({r['id'] for r in rows}) == 2
    assert all(not (r['end'] > 1 and r['start'] < 3) for r in rows)
    assert 'Lamp shown' in seen[0] and any('Candidate 0' in x for x in seen)


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
