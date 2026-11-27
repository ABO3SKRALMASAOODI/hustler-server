from types import SimpleNamespace

import numpy as np
import pytest

import edit_batch
import inpaint
import motion_engine
import motion_templates
import picture_map
import reply_grounding
import schemas
import timeline
from tools_ext import photo_tools


@pytest.mark.parametrize("factor", [6, 10, 12, 50, 150])
def test_speed_round_trip_batch_clock_and_reply(factor):
    before = schemas.default_edl(300)
    edit = edit_batch.apply_batch(before, [{"action": "upsert", "layer": "speed", "id": "sp1",
        "value": {"start": 0, "end": 300, "factor": factor}}], 300, {})
    assert edit["speed"][0]["factor"] == factor
    assert timeline.Timeline(edit["keep"], [], edit["speed"]).out_duration == 300 / factor
    assert f"{factor}x" in reply_grounding.change_list_text(reply_grounding.edl_changes(before, edit))


def test_internal_bands_survive_a_bright_part_of_the_same_shot():
    bright = np.full((100, 80), 145, np.uint8)
    banded = bright.copy()
    banded[:15] = banded[-15:] = 0
    shot = {"start": 0, "end": 3, "bars": False, "rect": [0, 0, 1, 1]}
    rows = picture_map._fill_rows(shot, [(2, bright), (17, bright),
                                        (32, banded), (47, banded)], 20)
    assert [r["bars"] for r in rows] == [False, True]
    assert rows[1]["rect"] == [0, .15, 1, .85]
    assert photo_tools._needs_fill(rows[1], 80 / 70, {"width": 80, "height": 100})


def test_same_take_dissolve_changes_only_a_removed_interval():
    index = {"shots": [{"id": 1, "start": 0, "end": 8},
                       {"id": 2, "start": 8, "end": 12}]}
    edl = {"keep": [[0, 3], [4, 7], [8, 12]],
           "effects": {"transition": {"scope": "every_cut", "style": "dissolve"}}}
    assert timeline.transition_junctions(edl, index) == {1}


def test_source_replacement_retains_old_ids_and_draws_inside_the_sign():
    spec = {"x": .1, "y": .2, "w": .6, "h": .45}
    identity = lambda row: schemas.patch_fingerprint("sha", [row], (0, 2))
    assert identity(spec) == identity(dict(spec, replacement=None))
    corrected = dict(spec, replacement="Maths")
    assert identity(spec) != identity(corrected)
    r = inpaint._Region(corrected, 320, 160)
    frame = np.full((160, 320, 3), 230, np.uint8)
    r.draw_replacement(frame)
    y, x = np.where(frame[:, :, 0] < 100)
    assert len(x) > 100 and min(x) >= 32 and max(x) < 224 and min(y) >= 32 and max(y) < 104


def test_landscape_stage_and_pencil_crayon_marker_have_visible_ink():
    items = [{"id": "cta", "template": "html",
              "html": '<div style="position:absolute;left:0;top:0;width:1080px;height:1920px;padding-top:500px"><b style="background:red;font-size:128px">LIKE</b></div>'}]
    items += [{"id": medium, "template": "el_doodle",
               "params": {"medium": medium, "kind": "scribble", "boil": False}}
              for medium in ["pencil", "crayon", "marker"]]
    jobs = [motion_templates.build_job(dict(item, start=0, end=2), 780, 360, 24) for item in items]
    for report in motion_engine.probe(jobs, [[.6, 1.4]] * len(jobs)):
        assert not report["errors"] and report["visible_frames"] == 2, report


def test_continuation_picks_an_unattempted_write_and_persists_the_choice():
    import agent_loop
    asks = [{"id": "a1", "want": "intro title", "write_tool": "add_text"},
            {"id": "a2", "want": "a narrated opening", "write_tool": "add_voiceover"}]
    ctx = SimpleNamespace(ask_ledger={"asks": asks}, turn_tool_outcomes=[{"tool": "add_text"}])
    assert "add_voiceover" in agent_loop._next_ask_write(ctx)
    assert agent_loop._next_ask_write(ctx) == ""
