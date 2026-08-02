"""Round 79 — the timeline's scissors reach the music lane, and a drop can
aim at a lane.

split_music cuts one music item into two sample-continuous halves (the tail's
offset_s advances by the head's length, wrapping through a looped track's real
duration), so "delete that part of the song" becomes split + delete instead of
remove-and-re-add-twice. add_overlay is the B-ROLL lane's drop target: media
laid OVER the program as a full-frame cutaway instead of spliced into it.

    cd backend && python -m pytest tests/test_music_split_and_lane_ops.py -q
"""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402

wschemas = video.wschemas
SRC_DUR = 400.0


def base_edl(music=None):
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [[100.0, 130.0]]
    if music:
        e["music"] = music
    return wschemas.validate_edl(e, SRC_DUR).model_dump()


def apply(edl, op, args, assets=None):
    new, desc = video._apply_edl_op(edl, op, args, assets or {},
                                    src_dur=SRC_DUR)
    new, desc = video._reanchor_after_op(edl, new, desc)
    return wschemas.validate_edl(new, SRC_DUR).model_dump(), desc


MUS = {"id": "mus1", "storage_key": "music/1/song.mp3",
       "start": 2.0, "end": 26.0, "gain_db": -4.0,
       "offset_s": 10.0, "fade_in_s": 0.6, "fade_out_s": 0.8}


def test_split_music_is_sample_continuous():
    edl, desc = apply(base_edl([dict(MUS)]), "split_music",
                      {"id": "mus1", "at_program_s": 10.0})
    head, tail = edl["music"]
    assert (head["start"], head["end"]) == (2.0, 10.0)
    assert (tail["start"], tail["end"]) == (10.0, 26.0)
    # the tail plays the very next sample of the track
    assert tail["offset_s"] == 18.0            # 10 + (10 - 2)
    # fades stay at the OUTER edges only
    assert head["fade_in_s"] == 0.6 and not head.get("fade_out_s")
    assert tail["fade_out_s"] == 0.8 and not tail.get("fade_in_s")
    assert tail["id"] != head["id"]
    assert "split music" in desc


def test_split_music_finds_the_item_under_the_playhead():
    edl, _ = apply(base_edl([dict(MUS)]), "split_music",
                   {"at_program_s": 12.5})
    assert len(edl["music"]) == 2
    assert edl["music"][1]["start"] == 12.5


def test_split_music_wraps_a_looped_track():
    m = dict(MUS, loop=True, offset_s=5.0)
    assets = {7: {"id": 7, "kind": "music", "storage_key": "music/1/song.mp3",
                  "duration_s": 12.0}}
    edl, _ = apply(base_edl([m]), "split_music",
                   {"id": "mus1", "at_program_s": 14.0}, assets)
    # 5 + 12 = 17 → wraps through the 12s track to 5.0
    assert edl["music"][1]["offset_s"] == 5.0


def test_split_music_rejects_the_edges_and_empty_lane():
    with pytest.raises(ValueError):
        apply(base_edl([dict(MUS)]), "split_music",
              {"id": "mus1", "at_program_s": 2.1})
    with pytest.raises(ValueError):
        apply(base_edl(), "split_music", {"at_program_s": 10.0})


def test_split_then_delete_removes_one_half_only():
    edl, _ = apply(base_edl([dict(MUS)]), "split_music",
                   {"id": "mus1", "at_program_s": 10.0})
    tail_id = edl["music"][1]["id"]
    edl2, _ = apply(edl, "remove_music", {"id": "mus1"})
    assert [m["id"] for m in edl2["music"]] == [tail_id]
    assert (edl2["music"][0]["start"], edl2["music"][0]["end"]) == (10.0, 26.0)


# ── the B-ROLL lane's drop target ──────────────────────────────────────────

CLIP_ASSET = {3: {"id": 3, "kind": "video_clip",
                  "storage_key": "clips/1/broll.mov", "duration_s": 6.5}}
IMG_ASSET = {4: {"id": 4, "kind": "image_ref",
                 "storage_key": "images/1/pic.jpg"}}


def test_add_overlay_lays_a_clip_over_the_program():
    edl, desc = apply(base_edl(), "add_overlay",
                      {"asset_id": 3, "start": 5.0}, CLIP_ASSET)
    ov = edl["overlays"][0]
    assert ov["kind"] == "video" and ov["fit"] == "cover"
    assert (ov["start"], ov["duration_s"]) == (5.0, 6.5)
    assert "b-roll" in desc
    # the program's length did NOT change — that is the point of a cutaway
    assert wschemas.program_duration(edl) == 30.0


def test_add_overlay_image_defaults_and_clamps():
    edl, _ = apply(base_edl(), "add_overlay",
                   {"asset_id": 4, "start": 28.0}, IMG_ASSET)
    ov = edl["overlays"][0]
    assert ov["kind"] == "image"
    assert ov["start"] + ov["duration_s"] <= 30.0 + 1e-6


def test_add_overlay_rejects_audio():
    with pytest.raises(ValueError):
        apply(base_edl(), "add_overlay", {"asset_id": 9, "start": 0.0},
              {9: {"id": 9, "kind": "music", "storage_key": "m.mp3"}})


# ── round 79f: the track outlives the window ───────────────────────────────

TRACK_ASSET = {8: {"id": 8, "kind": "music", "storage_key": "music/1/song.mp3",
                   "duration_s": 66.0}}


def test_left_trim_consumes_track_head_and_restores_it():
    m = dict(MUS, offset_s=0.13)
    edl, _ = apply(base_edl([m]), "trim_music",
                   {"id": "mus1", "start": 5.0, "end": 26.0}, TRACK_ASSET)
    got = edl["music"][0]
    assert got["start"] == 5.0
    assert got["offset_s"] == 3.13          # 0.13 + (5.0 - 2.0)
    # pulling the edge back out restores the head, bounded by the track
    edl2, _ = apply(edl, "trim_music", {"id": "mus1", "start": 0.0,
                                        "end": 26.0}, TRACK_ASSET)
    got2 = edl2["music"][0]
    # 0.0 normalizes to None in the schema — same meaning, stable signature
    assert (got2["offset_s"] or 0.0) == 0.0
    assert got2["start"] == 1.87            # only 3.13s of head existed


def test_right_trim_clamps_to_the_tracks_remainder():
    m = dict(MUS, offset_s=50.0)            # 16s of track left
    edl, _ = apply(base_edl([m]), "trim_music",
                   {"id": "mus1", "start": 2.0, "end": 28.0}, TRACK_ASSET)
    got = edl["music"][0]
    assert got["end"] == 18.0               # 2.0 + (66 - 50)


def test_slip_music_slides_and_clamps():
    m = dict(MUS)                           # window 24s, track 66s
    edl, desc = apply(base_edl([m]), "slip_music",
                      {"id": "mus1", "offset_s": 30.0}, TRACK_ASSET)
    assert edl["music"][0]["offset_s"] == 30.0
    assert "slipped" in desc
    edl2, _ = apply(edl, "slip_music", {"id": "mus1", "offset_s": 60.0},
                    TRACK_ASSET)
    assert edl2["music"][0]["offset_s"] == 42.0   # 66 - 24
    edl3, _ = apply(edl2, "slip_music", {"id": "mus1", "offset_s": -3.0},
                    TRACK_ASSET)
    assert (edl3["music"][0]["offset_s"] or 0.0) == 0.0


# ── round 79i: mute is a press, not a deletion ─────────────────────────────

def test_set_music_mute_toggles():
    edl, desc = apply(base_edl([dict(MUS)]), "set_music_mute",
                      {"id": "mus1", "mute": True})
    assert edl["music"][0]["mute"] is True
    assert "stays on the lane" in desc
    edl2, _ = apply(edl, "set_music_mute", {"id": "mus1", "mute": False})
    assert not edl2["music"][0].get("mute")


def test_set_insert_mute_toggles_and_rejects_images():
    e = wschemas.default_edl(SRC_DUR)
    e["keep"] = [[100.0, 130.0]]
    e["inserts"] = [
        {"id": "ins1", "kind": "video", "asset_key": "clips/1/c.mov",
         "at_output_s": 30.0, "duration_s": 4.0},
        {"id": "im1", "kind": "image", "asset_key": "images/1/p.jpg",
         "at_output_s": 30.0, "duration_s": 2.0}]
    e = wschemas.validate_edl(e, SRC_DUR).model_dump()
    edl, _ = apply(e, "set_insert_mute", {"id": "ins1", "mute": True})
    assert edl["inserts"][0]["mute"] is True
    edl2, _ = apply(edl, "set_insert_mute", {"id": "ins1", "mute": False})
    assert not edl2["inserts"][0].get("mute")
    with pytest.raises(ValueError):
        apply(e, "set_insert_mute", {"id": "im1", "mute": True})
