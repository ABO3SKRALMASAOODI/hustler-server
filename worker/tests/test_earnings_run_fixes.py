"""The Valmera defects the Elon Musk Q3 2026 results-call run (22-23 Oct,
w1023-earnings-lVNHybwudXI) recorded, each pinned to its evidence."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import validate_edl                      # noqa: E402
from timeline import Timeline, remap_program_items    # noqa: E402


# 4527 v24 -> v25: the opening keep's start moved 495.66 -> 495.97 (a 0.31 s
# head trim); ov1/ov2 (SpaceX animation cutaways) and tx1 ('SIMULATION') were
# cued to the rocket words at 13.95 s and stayed there while the words now
# play at 13.64 s.
K24 = [[495.66, 501.34], [503.11, 507.23], [507.37, 508.56],
       [509.19, 512.15], [512.48, 516.08], [518.08, 519.36],
       [519.87, 528.4]]
K25 = [[495.97, 501.34]] + K24[1:]


def _4527(keep):
    return {"keep": [list(k) for k in keep],
            "overlays": [
                {"id": "ov1", "kind": "video", "asset_key": "a.mp4",
                 "start": 13.95, "duration_s": 2.65, "source_start_s": 64.0},
                {"id": "ov2", "kind": "video", "asset_key": "a.mp4",
                 "start": 16.6, "duration_s": 2.7, "source_start_s": 5.5}],
            "texts": [{"id": "tx1", "text": "SIMULATION · SpaceX animation",
                       "start": 14.0, "end": 16.5}]}


def test_cutaways_and_callouts_follow_their_words_through_a_head_trim():
    edl = _4527(K25)
    notes = remap_program_items(edl, Timeline(K24), Timeline(K25))
    ov = {o["id"]: o for o in edl["overlays"]}
    assert ov["ov1"]["start"] == 13.64 and ov["ov1"]["duration_s"] == 2.65
    assert ov["ov2"]["start"] == 16.29 and ov["ov2"]["duration_s"] == 2.7
    assert ov["ov1"]["source_start_s"] == 64.0      # the clip plays as chosen
    tx = edl["texts"][0]
    assert (tx["start"], tx["end"]) == (13.69, 16.19)
    assert not any("SYNC REVIEW" in n for n in notes)
    assert any("overlay ov1 moved to 13.64" in n for n in notes)
    validate_edl(edl, 3742.4)


def test_cutaway_whose_words_were_cut_is_removed_and_a_shrunk_one_clips():
    # 4563 v16 -> v17 shape: a cut inside a cutaway's window shortens it
    # (the clip plays from its own start for less), and a cutaway whose
    # footage was cut away entirely is dropped instead of covering new words
    old = [[0.0, 10.0], [12.0, 30.0]]
    new = [[0.0, 10.0], [12.0, 15.0], [16.0, 24.0], [27.0, 30.0]]
    edl = {"keep": [list(k) for k in new],
           "overlays": [
               {"id": "keep_me", "kind": "image", "asset_key": "p.png",
                "start": 12.0, "duration_s": 3.0,
                "x": [{"t": 0.0, "v": 0.2}, {"t": 2.9, "v": 0.8}]},
               {"id": "gone", "kind": "video", "asset_key": "b.mp4",
                "start": 22.2, "duration_s": 0.6, "source_start_s": 1.0}]}
    notes = remap_program_items(edl, Timeline(old), Timeline(new))
    ov = {o["id"]: o for o in edl["overlays"]}
    assert "gone" not in ov
    assert any("overlay gone was removed" in n for n in notes)
    k = ov["keep_me"]
    # source 12-14 -> 12-13 kept, 14 -> 15 cut point... 12.0-15.0 out spans
    # source 14.0-17.0: 14-15 kept (1 s), 15-16 cut, 16-17 kept (1 s)
    assert (k["start"], k["duration_s"]) == (12.0, 2.0)
    assert all(p["t"] <= 2.0 for p in k["x"])


def test_whole_programme_overlay_and_text_keep_the_programme():
    # a frame laid over the whole edit, an externally cropped copy of the
    # programme and a title held throughout are layout, not cues
    edl = {"keep": [[2.0, 10.0]],
           "overlays": [{"id": "portrait", "kind": "video",
                         "asset_key": "crop.mp4", "start": 0.0,
                         "duration_s": 10.0, "fit": "cover"}],
           "texts": [{"id": "t", "text": "TITLE", "start": 0.0, "end": 10.0}]}
    notes = remap_program_items(edl, Timeline([[0.0, 10.0]]),
                                Timeline([[2.0, 10.0]]))
    assert any("SYNC REVIEW REQUIRED" in n for n in notes)
    assert edl["overlays"][0]["start"] == 0.0
    assert edl["overlays"][0]["duration_s"] == 8.0
    assert (edl["texts"][0]["start"], edl["texts"][0]["end"]) == (0.0, 8.0)


def test_cutaway_on_a_spliced_insert_rides_the_insert():
    old_ins = [{"id": "i1", "kind": "image", "asset_key": "s.png",
                "duration_s": 2.0, "at_output_s": 5.0}]
    new_ins = [dict(old_ins[0], at_output_s=4.0)]
    old_tl = Timeline([[0.0, 5.0], [6.0, 12.0]], old_ins)
    edl = {"keep": [[1.0, 5.0], [6.0, 12.0]], "inserts": new_ins,
           "texts": [{"id": "lab", "text": "THE CHIP", "start": 5.2,
                      "end": 6.8}]}
    new_tl = Timeline(edl["keep"], new_ins)
    remap_program_items(edl, old_tl, new_tl)
    assert (edl["texts"][0]["start"], edl["texts"][0]["end"]) == (4.2, 5.8)


# 4563 v21 (source words, from the 4444 index): a restart left inside the
# opening take and a cut that repeats 'to be' across the join
W4563 = [
    {"w": "you", "t0": 2531.99, "t1": 2532.18}, {"w": "you", "t0": 2532.18, "t1": 2532.38},
    {"w": "don't", "t0": 2532.38, "t1": 2532.59},
    {"w": "you", "t0": 2532.59, "t1": 2532.75},
    {"w": "don't", "t0": 2532.75, "t1": 2533.0},
    {"w": "need", "t0": 2533.0, "t1": 2533.3},
    {"w": "insane", "t0": 2544.49, "t1": 2544.89},
    {"w": "to", "t0": 2544.89, "t1": 2545.05},
    {"w": "be", "t0": 2545.05, "t1": 2545.45},
    {"w": "act...", "t0": 2545.45, "t1": 2545.85},
    {"w": "To", "t0": 2546.09, "t1": 2546.3},
    {"w": "be", "t0": 2546.3, "t1": 2546.6},
    {"w": "honest", "t0": 2546.6, "t1": 2547.0},
    {"w": "we", "t0": 2547.2, "t1": 2547.4},
    {"w": "we", "t0": 2547.4, "t1": 2547.6},
    {"w": "build", "t0": 2547.6, "t1": 2548.0},
]
K4563 = [[2532.17, 2533.3], [2544.4, 2545.45], [2546.09, 2548.0]]


def test_repeated_phrases_and_a_repeat_the_cut_made_are_found():
    import audit
    hits = audit.repeated_phrases(K4563, W4563)
    by = {h["text"]: h for h in hits}
    assert set(by) == {"you don't you don't", "to be to be", "we we"}
    assert by["you don't you don't"]["n"] == 2
    assert not by["you don't you don't"]["across_join"]
    assert by["you don't you don't"]["cut"] == (2532.18, 2532.59)
    assert by["to be to be"]["across_join"]
    assert by["we we"]["n"] == 1
    # a deliberate double and a new sentence after a pause are not stutters
    ok = [{"w": "no", "t0": 1.0, "t1": 1.2}, {"w": "no", "t0": 1.2, "t1": 1.4},
          {"w": "Right.", "t0": 2.0, "t1": 2.3},
          {"w": "right", "t0": 3.6, "t1": 3.9}]
    assert audit.repeated_phrases([[0.0, 5.0]], ok) == []
    # a word set_volume silences is not heard
    assert not [h for h in audit.repeated_phrases(
        K4563, W4563, ducks=[(2547.2, 2547.4)]) if h["text"] == "we we"]


def test_verification_blocks_a_repeated_phrase_and_advises_a_stutter():
    import quality_verifier as qv
    edl = {"keep": [list(k) for k in K4563]}
    rows = qv._repeat_findings(edl, {"words": W4563})
    codes = sorted(r["code"] for r in rows)
    assert codes == ["repeated_phrase", "repeated_phrase", "speaker_stutter"]
    blocking = [r for r in rows if qv.is_blocking(r)]
    assert {r["evidence"]["repeat"]["text"] for r in blocking} == {
        "you don't you don't", "to be to be"}
    assert any("the cut repeats it" in r["message"] for r in blocking)
    stutter = next(r for r in rows if r["code"] == "speaker_stutter")
    assert not qv.is_blocking(stutter) and "we we" in stutter["message"]
    rec = qv.build_verification_record(
        1, 2, {}, edl, {"words": W4563},
        preview={"edl_version": 2, "asset_id": 7, "duration_s": 5.0})
    assert rec["status"] == "repair_required"


def test_stack_captions_are_judged_at_their_reading_size():
    # 4562 v36: a stack look with size_scale 0.5 - each cue one emphasised
    # word at ~6% over small lines at ~2%; MEASURES said 'captions 6.0%'
    import motion_critic
    import render_qc
    cues = [{"t": 1.0 + i, "cap": 0.06, "cap_min": 0.019, "box": [.1, .6, .9, .75],
             "text": "so we BUILD it"} for i in range(5)]
    f = render_qc.type_findings({"items": {}, "cues": cues},
                                {"items": [], "W": 1080, "H": 1920}, [])
    caps = f["caps"]
    assert caps["captions"] == 0.06 and caps["captions_read"] == 0.019
    line = render_qc.measures_line({"type": f})
    assert "captions 6.0% (largest line; their other lines read at 1.9%)" in line
    out, measure = motion_critic.lens_type({"picture_qc": {"type": f}})
    assert measure["captions_read_cap"] == 0.019
    assert any(o.get("code") == "type_too_small" for o in out)
    # one-size captions: no reading line, judged as before
    plain = [dict(c, cap=0.04, cap_min=0.04) for c in cues]
    f2 = render_qc.type_findings({"items": {}, "cues": plain},
                                 {"items": [], "W": 1080, "H": 1920}, [])
    assert "captions_read" not in f2["caps"]
    assert not any(o.get("code") == "type_too_small"
                   for o in motion_critic.lens_type({"picture_qc": {"type": f2}})[0])


def test_an_editorial_line_sharing_one_word_with_the_speech_keeps_its_window():
    # 4562 v42 job 100397: add_typeset('the chip secret Elon *won't share*',
    # 0.1-2.7) matched only 'chip' (4.68 s) and moved its start past its end
    from tools_ext import typeset_tools as tt
    pw = [{"w": w, "t0": t, "t1": t + 0.2} for w, t in (
        ("some", 1.0), ("significant", 1.3), ("breakthroughs", 3.6),
        ("in", 4.2), ("chip", 4.68), ("fabrication,", 4.92), ("but", 5.6))]
    assert tt.match_words("the chip secret Elon *won't share*", pw) is None
    hit = tt.match_words("breakthroughs in *chip* fabrication", pw)
    assert hit and hit[0]["w"] == "breakthroughs" and hit[2] == 4
    assert tt.match_words("*chip*", pw)[0]["t0"] == 4.68


def test_proof_device_proves_a_named_plain_graphic():
    # 18 proof_device calls in the 24 h to 23 Oct named plain graphics
    # (4489 's45-hook', 4467 's23-agency-list') and were refused
    import motion_qa
    edl = {"motion": [{"id": "s45-hook", "template": "word_slam",
                       "start": 0.2, "end": 2.0, "params": {"text": "X"}},
                      {"id": "beh", "template": "word_slam", "start": 3.0,
                       "end": 4.0, "layer": "behind_subject",
                       "params": {"text": "Y"}}],
           "fx": [{"id": "acc1", "type": "accent", "start": 1.0, "end": 1.1}]}
    devs = motion_qa.device_items(edl, ids=["s45-hook", "acc1"])
    assert [(d["id"], d["kind"]) for d in devs] == [("s45-hook", "graphic"),
                                                  ("acc1", "fx")]
    assert motion_qa._family(devs[0], {}) == "layout"
    out = motion_qa.ablate(edl, devs)
    assert [m["id"] for m in out["motion"]] == ["beh"] and out["fx"] == []
    # unnamed, a plain graphic is still no device
    assert [d["id"] for d in motion_qa.device_items(edl)] == ["acc1", "beh"]


def test_a_one_word_payoff_is_estimated_at_the_size_it_draws():
    # 4562 '*TERAWATT*' payoff at width 0.765: rendered cap 6.3% (width-
    # bound), estimated 0.49-0.78 by the fixed tier box
    import keepout
    import motion_templates
    sp = motion_templates.spec("word_slam")
    one = keepout.nominal_ink("word_slam", sp, {"text": "*TERAWATT*", "tier": "payoff",
                                                "y": 0.635, "width": 0.765},
                              frame=(1080, 1920))
    assert one[3] - one[1] < 0.12 and abs((one[1] + one[3]) / 2 - 0.635) < 0.01
    sz, _w = keepout.payoff_layout({"text": "*TERAWATT*", "width": 0.765})
    assert abs(sz[0] * keepout.CAP_RATIO - 0.063) < 0.004
    # a stacked lockup is estimated as the stack it draws (taller than before)
    stack = keepout.nominal_ink("word_slam", sp, {"text": "far more *AGENTS*",
                                                  "tier": "payoff", "y": 0.645,
                                                  "width": 0.765}, frame=(1080, 1920))
    assert stack[3] - stack[1] > 0.4


def test_a_contrast_written_as_a_change_is_read_as_one():
    # 4566 job 96714 / 4570 job 98438 (0.1) and 4578 job 96468 (-0.08) were
    # refused by the schema's 0.25 floor
    from tools_ext import finish_tools as ft
    assert ft._contrast_factor(0.1)[0] == 1.1
    assert ft._contrast_factor(-0.08)[0] == 0.92
    assert "read as a change" in ft._contrast_factor(0.1)[1]
    assert ft._contrast_factor(1.08) == (1.08, "")
    assert ft._contrast_factor(None) == (None, "")


def test_remove_ui_scene_takes_one_id():
    import agent_tools
    name, args, notes = agent_tools._normalize_tool_call(
        "remove_ui_scene", {"id": "requests"})
    assert (name, args) == ("remove_ui_scene", {"ids": ["requests"]})
