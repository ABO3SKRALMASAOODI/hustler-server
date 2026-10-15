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
