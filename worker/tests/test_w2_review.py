"""Wave 2 independent review: regressions for what the review found.

- Render faces: a typeset item whose stored face zones do not speak for the
  canvas it renders on (another aspect master, a reframe, a stale client's
  apply_edit_batch item with no footprint) is solved against the faces the
  index shows at that canvas, never the centred prior over a real face.
- apply_format_pack writes SOLVED lockups (faces, voice, family, the
  lockup before it ended; a line no layout fits is dropped and said).
- Batch freshness: voices and devices the sibling shorts used rank lower.
- Detached transcript signs ("26 %") print as one word.
- A short hero line in a justified column is justified by size, never
  letterspaced apart.
"""

import copy

from w2_support import Ctx, edl, index

import schemas                                            # noqa: E402
from typeset import layoutdoc, layouts, roles             # noqa: E402

FACE_LEFT = [0.06, 0.18, 0.40, 0.50]       # source fractions (1920x1080)


def _index_with_faces():
    idx = index()
    # the speaker sits on the LEFT third of the 16:9 source
    idx["spatial"] = {"samples": [{"t": t + 0.5, "faces": [FACE_LEFT]}
                                  for t in range(0, 30)]}
    return idx


def _item(text="perhaps AI is bigger than the internet", **kw):
    it = {"id": "ts1", "template": "typeset", "start": 0.5, "end": 6.0,
          "layer": "above_captions",
          "params": {"text": text, "voice": "condensed_poster",
                     "layout": "auto", "depth": "front", "verb": "auto"}}
    it.update(kw)
    return it


def _runs_on(doc, zones):
    W, H = doc["canvas"]["w"], doc["canvas"]["h"]
    bad = []
    for r in doc.get("runs") or []:
        b = (r["ink"][0] / W, r["ink"][1] / H, r["ink"][2] / W,
             r["ink"][3] / H)
        for z in zones:
            if min(b[2], z[2]) > max(b[0], z[0]) and \
                    min(b[3], z[3]) > max(b[1], z[1]):
                bad.append(r["text"])
    return bad


def test_a_stale_client_lockup_is_solved_against_the_real_face():
    import keepout
    import motion_layer
    from timeline import Timeline
    idx = _index_with_faces()
    e = edl(keep=((0.0, 20.0),), ratio="16:9")
    e["motion"] = [_item()]
    e = schemas.validate_edl(e, 60.0).model_dump()
    W, H = 1920, 1080
    tl = Timeline(e["keep"], [], [])
    track = keepout.face_track(e, idx, W, H, 0.5, 6.0)
    zones = [tuple(z[:4]) for z in keepout.zones_of(track)]
    assert zones and zones[0][0] < 0.3          # the face is on the left
    raw = [f for _t, fs in track for f in fs]
    # the prior (no footprint) solves around a centred face: type can land
    # on the real one; refreshed, it keeps clear of it
    e2 = copy.deepcopy(e)
    motion_layer.fill_footprints(e2, W, H, 30.0, index=idx, tl=tl)
    m = e2["motion"][0]
    assert m["footprint"]["faces"], m.get("footprint")
    doc = layoutdoc.solve_item(m, W, H, 30.0)
    assert doc["runs"]
    assert not _runs_on(doc, raw)


def test_a_fresh_measured_footprint_is_kept_at_its_own_canvas():
    idx = _index_with_faces()
    e = edl(keep=((0.0, 20.0),), ratio="16:9")
    it = _item()
    fp = {"box": [0.5, 0.2, 0.9, 0.5], "ar": round(1920 / 1080, 4),
          "faces": [[0.6, 0.1, 0.8, 0.5]]}
    it["footprint"] = fp
    e["motion"] = [it]
    e = schemas.validate_edl(e, 60.0).model_dump()
    layoutdoc.refresh_faces(e, idx, 1920, 1080)
    assert e["motion"][0]["footprint"]["faces"] == [[0.6, 0.1, 0.8, 0.5]]
    # another canvas: the 9:16 master re-measures from the index
    layoutdoc.refresh_faces(e, idx, 1080, 1920)
    fp2 = e["motion"][0]["footprint"]
    assert abs(fp2["ar"] - 1080 / 1920) < 0.01 and fp2["faces"]


def test_refresh_never_fails_a_render():
    e = edl(keep=((0.0, 20.0),))
    e["motion"] = [_item(start="bad")]
    assert layoutdoc.refresh_faces(e, index(), 1080, 1920) is e
    assert layoutdoc.refresh_faces({"motion": []}, None, 1080, 1920)


def test_detached_signs_print_as_one_word():
    assert roles.clean_text("and scored 26 % better") == \
        "and scored 26% better"
    assert roles.clean_text("their non -player colleagues") == \
        "their non-player colleagues"
    assert roles.clean_text("it costs $ 5 now") == "it costs $5 now"
    assert [w for w, _s in roles.tokens("scored *26 %* better")] == \
        ["scored", "26%", "better"]
    assert [s for _w, s in roles.tokens("scored *26 %* better")] == \
        [False, True, False]
    # the reading is per whitespace word: '26' and '%' map onto '26%'
    item = {"params": {"text": "scored 26 % better"},
            "reading": {"rows": [[0.1, 0.5, 0.9, 1.2]]}}
    on = layoutdoc.onsets_of(item, 3, roles.spans("scored 26 % better"))
    assert on == [0.1, 0.5, 1.2]


def test_a_short_hero_line_is_justified_by_size_not_letterspacing():
    doc = layoutdoc.build_doc({"text": "scored 26%", "voice": "swiss_grid",
                               "layout": "side_column"}, W=1080, H=1920,
                              duration=3.0, onsets=[0.1, 0.6],
                              faces=[(0.45, 0.2, 0.85, 0.5)],
                              watermark=(0.0, 0.0, 0.42, 0.13))
    assert doc["runs"], doc["solver_report"]
    for r in doc["runs"]:
        assert r["tracking_em"] <= layouts.TRACK_SOFT + 0.25, r


def test_apply_format_pack_writes_solved_lockups(monkeypatch):
    from tools_ext import format_tools, typeset_tools
    monkeypatch.setattr(typeset_tools, "_faces",
                        lambda *a, **k: [(0.3, 0.2, 0.7, 0.46)])
    ctx = Ctx(edl(keep=((0.0, 14.0),), ratio="9:16"))
    out = format_tools.apply_format_pack(ctx, pack="C")
    assert out.startswith("EDL"), out
    ts = [m for m in ctx.edl["motion"] if m["template"] == "typeset"]
    assert ts
    for m in ts:
        assert m["params"]["voice"] != "auto"
        assert (m.get("footprint") or {}).get("box"), m
        assert m["layer"] == ("behind_subject" if m["params"].get("depth")
                              == "behind" else "above_captions") or \
            m["params"].get("depth") == "behind"
    spans = sorted((m["start"], m["end"]) for m in ts)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert a1 <= b0 + 1e-6               # never two lockups at once


def test_suggest_story_footer_names_the_exact_apply_call():
    from tools_ext import format_tools
    ctx = Ctx(edl(keep=((0.0, 14.0),), ratio="9:16"))
    out = format_tools.suggest_story(ctx, pack="none", intensity="calm")
    assert "apply_format_pack(pack='none', intensity='calm'" in out


def test_batch_freshness_rotates_voices_and_devices():
    import creative
    import story_planner
    e = edl(keep=((0.0, 14.0),), ratio="9:16")
    base = story_planner.suggest(e, index(), base=[])
    voices = [c["op"]["value"]["params"]["voice"] for c in base
              if c.get("op") and c["op"].get("layer") == "motion"]
    assert voices
    # three sibling shorts already opened on those voices
    sib = {"motion": [{"id": f"ts{k}", "template": "typeset", "start": 0,
                       "end": 1, "params": {"voice": v}}
                      for k, v in enumerate(voices)]}
    batch = creative.ledger_of([sib, sib, sib])
    again = story_planner.suggest(e, index(), base=[], batch=batch)
    v2 = [c["op"]["value"]["params"]["voice"] for c in again
          if c.get("op") and c["op"].get("layer") == "motion"]
    assert v2 and v2 != voices
    # a voice used by the batch ranks lower in pick_voice too
    v_a, _ = creative.pick_voice("we never gave up", position="hook",
                                 seed=3)
    v_b, _ = creative.pick_voice("we never gave up", position="hook",
                                 seed=3, batch=[v_a] * 3)
    assert v_a != v_b


def test_the_colour_turn_is_earned_and_fresh_in_the_batch():
    import creative
    import story_planner
    e = edl(keep=((0.0, 14.0),), ratio="9:16")
    got = story_planner.suggest(e, index(), base=[])
    bw = [c for c in got if c["device"] == "grade_section"]
    sib = {"fx": [{"id": "gw1", "device": "grade_section"}]}
    later = story_planner.suggest(e, index(), base=[],
                                  batch=creative.ledger_of([sib, sib]))
    assert not [c for c in later if c["device"] == "grade_section"]
    for c in bw:
        v = c["op"]["value"]
        # a device, never a look: bounded and ending on the payoff
        assert v["end"] - v["start"] <= 4.0 + 1e-6


def test_type_on_a_card_ignores_the_talking_head_prior():
    """A lockup on black (call-and-response) has no face to keep clear of:
    'over: card' drops the centred prior a face-less picture assumes."""
    from typeset import freespace
    p = {"text": "technology changes everything", "voice": "condensed_poster"}
    wm = layoutdoc._watermark(1080, 1920)
    pic = layoutdoc.build_doc(p, W=1080, H=1920, duration=2.0, faces=[],
                              watermark=wm)
    card = layoutdoc.build_doc(dict(p, over="card"), W=1080, H=1920,
                               duration=2.0, faces=[], watermark=wm)
    prior = freespace.Space(1080, 1920).face_zones[0]
    assert not any(freespace.inter(r["ink"], prior) for r in pic["runs"])
    assert card["solver_report"]["payload_cap_pct"] >= \
        pic["solver_report"]["payload_cap_pct"]


def test_build_item_marks_type_on_black_as_on_a_card(monkeypatch):
    from tools_ext import typeset_tools
    ctx = Ctx(edl(keep=((0.0, 14.0),), ratio="9:16"))
    item, err = typeset_tools.build_item(ctx, ctx.edl, "flying cars", 11.7,
                                         13.0, "ts7", faces=[])
    assert err is None and item["params"]["over"] == "card"


def test_the_edit_review_does_not_tell_the_agent_to_drop_a_typeset_line():
    """A typeset lockup is the line set INSTEAD of its captions (ROADMAP
    2A); the review's re-typeset finding is for graphics that duplicate
    running captions, so it must not ask the agent to drop the device."""
    import edit_review
    e = edl(keep=((0.0, 14.0),), ratio="9:16")
    e["captions"] = {"enabled": True}
    lock = {"id": "ts1", "template": "typeset", "start": 7.2, "end": 9.9,
            "layer": "above_captions",
            "params": {"text": "the potential to reaccelerate growth",
                       "voice": "staircase_red"}}
    e["motion"] = [lock]
    e = schemas.validate_edl(e, 60.0).model_dump()
    notes = edit_review.review(e, index(), force=True)
    assert not [n for n in notes if n["code"] == "restates_captions"], notes
    # the same words as a phrase_build beside the captions still are one
    e2 = copy.deepcopy(e)
    e2["motion"][0]["template"] = "phrase_build"
    e2["motion"][0]["params"] = {"rows": [
        {"text": "the potential"}, {"text": "to reaccelerate growth"}]}
    e2 = schemas.validate_edl(e2, 60.0).model_dump()
    notes2 = edit_review.review(e2, index(), force=True)
    assert [n for n in notes2 if n["code"] == "restates_captions"], notes2


def _shorts_ctx(words, sentences):
    from types import SimpleNamespace
    import db as dbx
    seen = {}

    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.has_active_job:
                return False
            if fn is dbx.enqueue_job:
                seen["payload"] = args[3]
                return 990
            raise AssertionError(fn)
    return SimpleNamespace(
        project={}, has_main_video=True, duration=2000.0,
        index={"words": words, "sentences": sentences}, db=FakeDb(),
        project_id=7, job={"user_id": 60, "type": "mcp_tool"}), seen


def _q_words(pause_before=0.0):
    toks = [("and", 1362.9), ("the", 1363.1), ("question", 1363.2),
            ("is", 1363.3), ("what", 1363.44 + pause_before),
            ("are", 1363.7), ("the", 1363.8), ("odds", 1363.9),
            ("Tesla", 1364.01), ("survives?", 1364.4)]
    return [{"w": w, "t0": t, "t1": round(t + 0.09, 2)} for w, t in toks]


def test_make_shorts_never_snaps_a_start_forward_into_a_question():
    """Run report defect 27 on the MCP path it happened on (caller clips,
    snap='sentence'): the sentence index split a question at a cap, and
    the start moved FORWARD from 1363.44 to 1364.01 — mid-question."""
    import agent_tools
    sents = [{"t0": 1355.0, "t1": 1363.99, "speaker": 0,
              "text": "and the question is what are the odds"},
             {"t0": 1364.01, "t1": 1380.0, "speaker": 0,
              "text": "Tesla survives? I'd say under ten percent."}]
    ctx, seen = _shorts_ctx(_q_words(), sents)
    out = agent_tools.make_shorts(ctx, clips=[{
        "start": 1363.44, "end": 1380.0, "title": "Under ten percent"}],
        snap="sentence")
    assert "1364.01" not in out.split("REJECTED")[-1] or \
        out.startswith("REJECTED"), out
    assert "payload" not in seen or \
        seen["payload"]["clips"][0]["start"] != 1364.01, out
    # a start on a word after a breath is the thought's own edge: kept
    ctx2, seen2 = _shorts_ctx(_q_words(pause_before=0.4), [
        dict(sents[0], t1=1363.39), dict(sents[1], t0=1364.41)])
    out2 = agent_tools.make_shorts(ctx2, clips=[{
        "start": 1363.84, "end": 1380.0, "title": "Under ten percent"}],
        snap="sentence")
    assert "job 990" in out2, out2
    assert seen2["payload"]["clips"][0]["start"] == 1363.84


def test_variable_font_axes_never_need_fonttools(monkeypatch):
    """Production images had no fontTools (only the dev venv did): every
    variable face was measured at its default instance while the page set
    the voice's weight and width. HarfBuzz reads the axes itself."""
    import builtins
    from typeset import shaping
    real = builtins.__import__

    def no_fonttools(name, *a, **k):
        if name.startswith("fontTools"):
            raise ImportError("no fontTools on this lane")
        return real(name, *a, **k)
    shaping._axes.cache_clear()
    monkeypatch.setattr(builtins, "__import__", no_fonttools)
    try:
        path = [f["path"] for f in shaping.faces()
                if f["path"].endswith("Anybody-Variable.ttf")][0]
        axes = shaping._axes(path)
        assert axes.get("wdth") == (50.0, 100.0, 150.0)
        assert axes.get("wght")[2] == 900.0
    finally:
        shaping._axes.cache_clear()


def test_fonttools_is_a_worker_requirement():
    import os
    req = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "requirements.txt")).read()
    assert "\nfonttools==" in req and "\nuharfbuzz==" in req


def test_a_speech_habit_is_not_a_callback():
    """suggest_story proposed 'we have a' (said twice on V02) as a callback
    rhyme: a repeat must say something."""
    import callbacks
    ws = [{"w": w, "t0": i * 0.5} for i, w in enumerate(
        "we have a tagline we have a site the rowing joke is great and "
        "then the rowing joke again".split())]
    assert [c["text"] for c in callbacks.find(ws)] == ["the rowing joke"]


def test_a_lockup_that_fits_nowhere_on_this_canvas_gives_its_words_back():
    """The degrade ladder ends in 'omit, said', never a silent loss: a
    typeset lockup no layout fits at render (another aspect, a face that
    fills the frame) would draw nothing while its words stayed muted."""
    import caption_carry
    from timeline import Timeline
    e = edl(keep=((0.0, 20.0),), ratio="9:16")
    e["captions"] = {"enabled": True}
    it = _item(text=" ".join(["internet"] * 20))
    # a face filling the frame at THIS canvas (fresh, measured)
    it["footprint"] = {"box": [0.1, 0.1, 0.9, 0.9],
                       "ar": round(1080 / 1920, 4),
                       "faces": [[0.02, 0.05, 0.98, 0.95]]}
    e["motion"] = [it]
    e = schemas.validate_edl(e, 60.0).model_dump()
    layoutdoc.refresh_faces(e, index(), 1080, 1920)
    assert not [m for m in e["motion"] if m["id"] == "ts1"]
    tl = Timeline(e["keep"], [], [])
    assert not caption_carry.heard_unshown(e, index(), tl)


def test_the_layout_is_the_same_on_a_slow_lane_and_the_page_is_stable(
        monkeypatch):
    """The solver stopped on a CLOCK (2 s): a 0.5 vCPU MCP lane writing the
    item and a batch lane rendering it solved different layouts (the
    footprint the captions avoid was not what was drawn), and the solve's
    timing in the page made every render's motion cache key new."""
    import copy as _copy
    import motion_templates
    from typeset import solver
    p = {"text": "perhaps AI is *bigger* than the internet",
         "voice": "magazine"}
    kw = dict(W=1080, H=1920, duration=4.0, faces=[(0.3, 0.2, 0.75, 0.6)],
              watermark=(0.0, 0.0, 0.42, 0.13))
    fast = layoutdoc.build_doc(p, **kw)
    ticks = {"n": 0}

    def slow():
        ticks["n"] += 1
        return ticks["n"] * 0.02           # a lane several times slower
    monkeypatch.setattr(solver.time, "monotonic", slow)
    slowdoc = layoutdoc.build_doc(p, **kw)
    monkeypatch.undo()
    assert [(r["text"], r["ink"]) for r in fast["runs"]] == \
        [(r["text"], r["ink"]) for r in slowdoc["runs"]]
    item = {"id": "ts1", "template": "typeset", "start": 1.0, "end": 4.0,
            "params": motion_templates.normalize_params("typeset", p)}
    j1 = motion_templates.build_job(_copy.deepcopy(item), 1080, 1920, 30.0)
    layoutdoc._CACHE.clear()
    j2 = motion_templates.build_job(_copy.deepcopy(item), 1080, 1920, 30.0)
    assert j1.key() == j2.key()


def test_render_variants_labels_only_voices_that_fit(monkeypatch):
    """A voice whose faces do not fit the frame falls back to the plainest
    one; the sheet labelled it as the voice asked for ('techno_crt' showed
    whisper_shout on V01), misleading the pick."""
    from tools_ext import format_tools
    import variants_edit
    ctx = Ctx(edl(keep=((0.0, 14.0),), ratio="9:16"))
    ctx.edl["motion"] = [_item("bigger than the *internet*", start=4.5,
                               end=6.4)]
    ctx.workdir = None
    import numpy as np
    monkeypatch.setattr(format_tools, "_program_frame",
                        lambda *a, **k: np.full((1920, 1080, 3), 40,
                                                np.uint8))
    seen = []
    real = variants_edit.raster

    def raster(doc, frame):
        seen.append(doc.get("voice"))
        return real(doc, frame)
    monkeypatch.setattr(variants_edit, "raster", raster)
    monkeypatch.setattr(variants_edit, "sheet", lambda *a, **k: None)
    out = format_tools.render_variants(ctx, id="ts1")
    labels = out.split(": ", 1)[1].split(". Apply")[0].split("; ")
    assert [l.split(" ")[0] for l in labels] == seen
    assert len(set(seen)) == len(seen) >= 2


def test_main_video_is_described_where_the_short_is(tmp_path):
    """library.analyze_clip read only the FIRST ten minutes of a file: a
    short cut at 22 min of a 2 h podcast found no described footage under
    its own frames (plan_loop: 'the program does not open on described
    footage'; find_shots like=program seconds, pick_still the same)."""
    import library
    import w0b_probe
    from types import SimpleNamespace
    from tools_ext import structure_tools
    src = w0b_probe.make_source(str(tmp_path / "s.mp4"), rate="25")
    doc = library.analyze_clip(src, max_s=4.0, start=5.0)
    assert doc["shots"] and min(sh["t0"] for sh in doc["shots"]) >= 5.0
    assert max(sh["t1"] for sh in doc["shots"]) <= 9.05
    deep = SimpleNamespace(latest_edl=lambda: {"json": {
        "keep": [[1363.4, 1380.0], [1385.0, 1401.2]]}})
    assert structure_tools._program_window(deep) == 1333.4
    early = SimpleNamespace(latest_edl=lambda: {"json": {
        "keep": [[12.0, 48.0]]}})
    assert structure_tools._program_window(early) == 0.0


def test_apply_format_pack_without_picks_is_restrained(monkeypatch):
    """With no picks it wrote all eight proposals (three lockups, two split
    tones, a fill flash, a B&W section on V04): restraint is the default —
    the best few for the intensity; the rest are named, still pickable."""
    from tools_ext import format_tools, typeset_tools
    monkeypatch.setattr(typeset_tools, "_faces",
                        lambda *a, **k: [(0.3, 0.2, 0.7, 0.46)])
    ctx = Ctx(edl(keep=((0.0, 14.0),), ratio="9:16"))
    out = format_tools.apply_format_pack(ctx, pack="none", intensity="calm")
    written = [m for m in ctx.edl.get("motion") or []] + \
        [f for f in ctx.edl.get("fx") or []]
    assert len(written) <= format_tools.APPLY_N["calm"] + 2, out
    import story_planner
    got = story_planner.suggest(ctx.edl, index(), base=[
        {"device": "grade_split_tone", "family": "finish", "at": 1.0,
         "op": {"action": "upsert", "layer": "fx", "id": "g1",
                "value": {}}},
        {"device": "grade_split_tone", "family": "finish", "at": 5.0,
         "op": {"action": "upsert", "layer": "fx", "id": "g2",
                "value": {}}}])
    assert sum(c["device"] == "grade_split_tone" for c in got) <= 1
