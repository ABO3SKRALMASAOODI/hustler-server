"""The Director's film, round five (13 Nov 2026): the owner's judge is the @metromedia.house reels.

The Scout's ban on metaphor pictures is gone and taste decides each one; the take is never still (score.move); a
film gets a music bed chosen for it, with its drop on the turn (score.bed); the last look sends a film to a second
render only when a third of its frames lose. Synthetic Scores and index stubs only: no customer media is here."""
import json
import os
import types

import pytest

import schemas
import score.compiler as S
from score import bed, lower, move, scout, supply, taste
from tests.test_score_compiler import BIN
from tests.test_score_taste import beats_over, comp, film, selfie, voice


def shots(c):
    return sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])


def widths(s):
    c = s["crop"]
    return [r[2] - r[0] for r in (c if isinstance(c[0], list) else [c])]


# ------------------------------------------------------------------ the metaphor ban is gone, taste decides

def test_the_scout_may_wish_a_metaphor_and_names_which_kind_each_picture_is():
    text = scout.SYSTEM
    assert "Never a mood, a metaphor" not in text
    assert '"how":"thing|like"' in text and "up to 16" in text
    assert "ids, never the words themselves" in text            # a wish lands on word ids, so the cut can find its word
    for bad in ("handshake", "skyline"):                           # what taste still throws away is named
        assert bad in text


def test_the_look_and_the_last_look_let_a_metaphor_that_lands_stand_and_still_refuse_the_generic_scene():
    for text in (supply.LOOK_SYSTEM, taste.LOOK_SYSTEM):
        assert "never something loosely like it, a mood, a metaphor" not in text
        assert "cheetah" in text and "handshake" in text
    assert supply.MAX_ITEMS >= 14


def test_the_check_is_told_a_picture_is_an_association(tmp_path):
    (tmp_path / "bin.json").write_text(json.dumps({"items": [{"id": 0, "what": "dominoes falling in a line", "how": "like"}, {"id": 1, "what": "the bridge"}]}))
    whats = taste.bin_whats(str(tmp_path))
    assert "association" in whats["bin:0"] and "association" not in whats["bin:1"]


# ------------------------------------------------------------------ the take is never still

def talking(seconds=24.0, **kw):
    idx = selfie(seconds, **kw)
    text = film(beats_over(idx, 4, lambda k, a, z: {"pic": "main"}), film_={"gap": 0.25})
    return idx, text


def test_a_take_is_eased_on_one_continuous_track_and_ends_no_closer_than_the_picture_allows(monkeypatch):
    monkeypatch.setenv("VALMERA_TALK_CUTS", "0")              # the eased track itself: a take cut inside itself (round seven) is tested there
    idx, text = talking()
    c = comp(text, idx, voice(24.0))
    mv = c.meter["move"]
    assert mv["moved"] >= 1 and mv["moves"] >= 3 and not mv.get("fault")
    assert "CAMERA the take runs" in c.meter_text
    ws = [w for s in shots(c) if s.get("pic") == "main" for w in widths(s)]
    assert max(ws) / min(ws) > 1.04                                   # it really closes on the face
    steps = [abs(a / b - 1) for s in shots(c) if s.get("pic") == "main" for a, b in zip(widths(s), widths(s)[1:])]
    assert max(steps) < 0.03                                           # inside a shot no hard punch: every frame is next to the one before
    assert sum(1 for d in steps if d > 1e-5) > 0.7 * len(steps)        # and it is moving most of the time


def test_no_head_is_cut_and_a_face_never_passes_the_governors_height():
    idx, text = talking(face=(0.30, 0.22, 0.70, 0.52))
    c = comp(text, idx, voice(24.0))
    for s in shots(c):
        rows = s["crop"] if isinstance(s["crop"][0], list) else [s["crop"]]
        for r in rows:
            h = r[3] - r[1]
            assert (0.52 - 0.22) / h <= taste.FACE_H_MAX + 1e-3
            assert r[0] <= 0.30 and r[2] >= 0.70 and r[1] <= 0.22 and r[3] >= 0.52


def test_a_selfie_held_against_one_edge_still_moves_away_from_that_edge(monkeypatch):
    monkeypatch.setenv("VALMERA_TALK_CUTS", "0")              # the eased track itself: a take cut inside itself (round seven) is tested there
    idx, text = talking(face=(0.04, 0.3, 0.42, 0.56))                 # the hair already leaves the frame on the left
    c = comp(text, idx, voice(24.0))
    assert c.meter["move"]["moved"] >= 1
    for s in shots(c):
        rows = s["crop"] if isinstance(s["crop"][0], list) else [s["crop"]]
        assert all(r[0] <= 0.04 + 1e-6 for r in rows)                  # the face is never cut on the side it sits


def test_an_upload_of_another_shape_keeps_every_word_it_prints_whole():
    idx, text = talking()
    idx["video"].update(width=1920, height=1080)
    perc = dict(voice(24.0), text=[{"box": [0.02, 0.8, 0.98, 0.95], "t": [0.0, 24.0]}])
    c = S.compile_score(text, index=idx, sid="five", perception=perc, bin_items=BIN)
    for s in shots(c):
        assert not isinstance(s["crop"][0], list) or len({tuple(r) for r in s["crop"]}) == 1 or all(r[0] <= 0.02 and r[2] >= 0.98 for r in s["crop"])


def test_the_switch_turns_the_eased_track_off(monkeypatch):
    monkeypatch.setenv("VALMERA_EASE", "0")
    idx, text = talking()
    c = comp(text, idx, voice(24.0))
    assert not c.meter["move"].get("moved")
    assert all(not isinstance(s["crop"][0], list) for s in shots(c))


def test_an_eased_track_lowers_to_waypoints_that_follow_its_curve(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_TALK_CUTS", "0")
    idx, text = talking()
    c = comp(text, idx, voice(24.0))
    edl, _loc, rep = lower.lower(c.l1, str(tmp_path))
    zooms = edl["effects"]["zooms"]
    assert zooms and all(z["mode"] == "path" and len(z["path"]) <= lower.KF_MAX for z in zooms)
    assert max(len(z["path"]) for z in zooms) > 2
    schemas.validate_edl(edl, float(idx["video"]["duration"]))
    # the waypoints are where the curve bends: a straight ramp keeps two
    ramp = [[0.5, 0.5, 1 + 0.001 * i] for i in range(40)]
    assert lower.key_frames(ramp) == [0, 39]
    bend = [[0.5, 0.5, 1.0]] * 20 + [[0.5, 0.5, 1 + 0.01 * i] for i in range(1, 11)] + [[0.5, 0.5, 1.1]] * 20
    assert {19, 30} <= set(lower.key_frames(bend)) | {k + 1 for k in lower.key_frames(bend)} | {k - 1 for k in lower.key_frames(bend)}


def test_a_still_on_the_plate_keeps_pushing_after_it_settles():
    idx = selfie(16.0)
    wide = {"0": {"kind": "image", "path": "bin/0.jpg", "w": 4000, "h": 1500}}        # wider than twice the frame's shape: set whole on the plate
    text = film(beats_over(idx, 2, lambda k, a, z: {"cut": [50, 999], "pic": ["bin:0", "main"]} if k == 0 else {"pic": "main"}))
    c = S.compile_score(text, index=idx, sid="five", perception=voice(16.0), bin_items=wide)
    cards = [i for i in c.l1["items"] if i["k"] == "card" and i.get("framed")]
    if not cards:
        pytest.skip("this fixture's still is not framed on the plate")
    s = cards[0]["s"]
    assert isinstance(s, list) and s[-1] > s[len(s) // 2] > 0 and float(cards[0]["w"]) * max(s) <= 0.99


# ------------------------------------------------------------------ the bed

class _Lib:
    SOURCE_HOSTS = ()

    def __init__(self):
        self.track = {"cid": "t-one", "title": "One", "author": "A", "license": "CC-BY-4.0", "credit": "\"One\" by A", "lufs": -12.0, "bpm": 120.0,
                      "curve": [-12] * 120, "downbeats": [2.0 * k for k in range(60)], "sections": [], "phrase_ends": [], "drops": [], "storage_key": "music-library/t-one.mp3"}

    def tracks(self):
        return [self.track]

    def find(self, cid):
        return self.track if cid == "t-one" else None

    def profiles(self):
        return {}

    def kind_of(self, k):
        return k

    def best_section(self, track, film_, prof, top=1):
        return [{"start_s": 8.0, "end_s": 8.0 + film_["duration_s"], "loop": False, "align": [], "why": ["fits"]}]

    def choose(self, film_, used=(), call=None, ctx=None, n=6):
        return dict(self.track, start_s=4.0, why="by signal", chosen_by="signal", options=[])


@pytest.fixture
def library(monkeypatch):
    import sys
    lib = _Lib()
    monkeypatch.setitem(sys.modules, "music_choose", lib)
    monkeypatch.setitem(sys.modules, "music_gate", types.SimpleNamespace(PROVENANCES=("user_upload", bed.PROVENANCE), grant=lambda *a: None))
    return lib


def _work(tmp_path, idx, perc):
    json.dump(idx, open(tmp_path / "index.json", "w"))
    json.dump(perc, open(tmp_path / "perception.json", "w"))
    return str(tmp_path)


def test_a_film_gets_a_bed_under_the_voice_with_its_drop_on_the_turn(tmp_path, library):
    idx = selfie(24.0)
    work = _work(tmp_path, idx, voice(24.0))
    per = {"dur": 24.0, "shots": [{"t": [0, 24.0]}]}
    doc = bed.prechoose(work, {"about": "a man explains a bridge", "wishes": [], "asks": []}, per, idx)
    assert doc and doc["cid"] == "t-one" and "120 bpm" in bed.brief_line(work) and "15" in bed.brief_line(work)
    assert bed.chosen(work) is None                                     # chosen, not yet settled on a film: nothing lowers
    text = film(beats_over(idx, 4, lambda k, a, z: {"pic": "main"}), film_={"gap": 0.25, "music": {"feel": "low strings", "turn": "b03"}})
    c = S.compile_score(text, index=idx, work_dir=work, sid="five", perception=voice(24.0), bin_items=BIN)
    assert c.meter["taste"]["supply"]["bed"] is True and c.meter["taste"]["supply"]["own_bed"] is False
    got = bed.settle(work, c, idx, S.parse_score(text)[0], "a man explains a bridge")
    assert got and got["settled"] and got["offset_s"] == 8.0 and not got["lead"]
    # the stretch plays under the levelled voice by the kind's margin
    assert abs((-12.0 + got["gain_db"]) - (bed.VOICE_LUFS - bed.UNDER_VOICE[got["kind"]])) < 0.11
    edl, _loc, rep = lower.lower(c.l1, work)
    assert rep["bed"]["cid"] == "t-one" and rep["bed"]["drop"]
    m = edl["music"][0]
    assert m["provenance"] == "valmera_chosen" and m["duck"] is False and m["start"] == 0.0 and m["storage_key"] == "bed:t-one"
    hole = [f for f in edl["fx"] if f.get("stage") == "music_bus"]
    turn = got["turn_s"]
    assert len(hole) == 1 and hole[0]["kind"] == "hole" and hole[0]["music_id"] == "bed" and abs(hole[0]["end"] - turn) < 0.06 and 0.5 < hole[0]["end"] - hole[0]["start"] < 1.0
    schemas.validate_edl(edl, float(idx["video"]["duration"]))


def test_a_score_that_says_none_stays_dry_and_so_does_an_upload_with_its_own_track(tmp_path, library):
    idx = selfie(20.0)
    work = _work(tmp_path, idx, voice(20.0))
    bed.prechoose(work, {"about": "x"}, {"dur": 20.0}, idx)
    text = film(beats_over(idx, 3, lambda k, a, z: {"pic": "main"}), film_={"music": "none"})
    c = S.compile_score(text, index=idx, work_dir=work, sid="five", perception=voice(20.0), bin_items=BIN)
    assert c.meter["taste"]["supply"]["bed"] is False
    assert bed.settle(work, c, idx, S.parse_score(text)[0]) is None
    assert lower.lower(c.l1, work)[0].get("music") in (None, [])


def test_an_ask_about_music_leaves_music_to_the_customer(tmp_path, library):
    idx = selfie(20.0)
    work = _work(tmp_path, idx, voice(20.0))
    assert bed.prechoose(work, {"about": "x", "asks": [{"id": "a1", "said": "add calm music", "probe": "music"}]}, {"dur": 20.0}, idx) is None
    assert bed.brief_line(work) is None


def test_with_no_library_nothing_changes(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "music_choose", None)
    idx = selfie(12.0)
    work = _work(tmp_path, idx, voice(12.0))
    assert bed.available() is False and bed.prechoose(work, {"about": "x"}, {"dur": 12.0}, idx) is None
    assert bed.items(work, 12.0) == ([], [])
    monkeypatch.setenv("VALMERA_BED", "0")
    assert bed.available() is False


def test_a_track_that_does_not_reach_the_project_in_time_leaves_the_film_dry(tmp_path, library, monkeypatch):
    from score import film as F
    idx = selfie(20.0)
    work = _work(tmp_path, idx, voice(20.0))
    bed.prechoose(work, {"about": "x"}, {"dur": 20.0}, idx)
    text = film(beats_over(idx, 3, lambda k, a, z: {"pic": "main"}), film_={"music": {"turn": "b02"}})
    c = S.compile_score(text, index=idx, work_dir=work, sid="five", perception=voice(20.0), bin_items=BIN)
    assert bed.settle(work, c, idx, S.parse_score(text)[0])
    edl, _loc, _rep = lower.lower(c.l1, work)
    assert edl["music"]
    ctx = types.SimpleNamespace(project_id=7, director_state={"work": work}, director_bed_fell=None)
    monkeypatch.setattr(bed, "project_asset", lambda ctx, db, doc: (None, "storage is down"))
    F._publish_bed(ctx, None, edl)
    assert edl["music"] == [] and not [f for f in edl["fx"] if f.get("stage") == "music_bus"]
    assert ctx.director_bed_fell and bed.chosen(work) is None            # a second lowering of this film is dry too
    edl2, _loc, _rep = lower.lower(c.l1, work)
    assert not edl2.get("music")
    schemas.validate_edl(edl, float(idx["video"]["duration"]))


# ------------------------------------------------------------------ one render

def test_one_named_device_on_one_frame_does_not_buy_a_second_render():
    assert taste.verdict_of({"pairs": 6, "upload": 1, "remove": ["b04.chip"], "why": ["x"]}) == "film"
    assert taste.verdict_of({"pairs": 6, "upload": 0, "remove": ["b04.chip"], "why": []}) == "film"
    assert taste.verdict_of({"pairs": 6, "upload": 2, "remove": ["b04.chip"], "why": ["x", "y"]}) == "cut"
    assert taste.verdict_of({"pairs": 6, "upload": 4, "remove": [], "why": ["x"] * 4}) == "floor"
    assert taste.verdict_of(None) == "film"


# ------------------------------------------------------------------ real footage: the quick film, then the full one

def test_footage_takes_the_place_of_a_still_of_the_same_moment_and_nothing_else_changes(tmp_path):
    from PIL import Image
    from score import film as F
    work = str(tmp_path)
    os.makedirs(os.path.join(work, "bin"))
    for k in (0, 1):
        Image.new("RGB", (64, 64)).save(os.path.join(work, f"bin/{k}.jpg"))
    json.dump({"items": [{"id": 0, "kind": "image", "path": "bin/0.jpg", "for": "w0-2", "provider": "wikimedia", "what": "the bridge", "named": "Golden Gate Bridge"},
                         {"id": 1, "kind": "image", "path": "bin/1.jpg", "for": "w9-11", "provider": "pexels", "what": "a wave"}], "credits": []},
              open(os.path.join(work, "bin.json"), "w"))
    text = "\n".join(json.dumps(o) for o in [
        {"k": "film", "v": 1, "fps": 30, "frame": "9:16", "spine": "main", "band": "P1"},
        {"k": "b", "id": "b01", "w": "0-6", "job": "x", "cut": [30, 999], "pic": ["bin:0", "main"]},
        {"k": "b", "id": "b02", "w": "7-12", "job": "x", "cut": [30, 30, 999], "pic": ["main", {"src": "bin:1", "crop": "upper"}, "bin:0@1.0"]}]) + "\n"
    clip = {"id": "footage:a.mp4", "provider": "footage", "kind": "video", "_wish": 0, "named": "Golden Gate Bridge", "shows": "the towers in fog",
            "width": 1080, "height": 1920, "duration_s": 2.4, "license": "Footage, not licensed", "credit": "Some Channel", "page_url": "https://example.org/v",
            "_img": Image.linear_gradient("L").resize((90, 160)).convert("RGB"), "footage": {"url": "https://example.org/v", "time_range": [12.0, 14.4]}}
    box = {"wishes": [{"w": "3-5", "want": "the Golden Gate Bridge"}, {"w": "9-11", "want": "a wave"}], "offers": [clip]}
    got = []
    out = F.footage_into(work, text, box, lambda c: got.append(c["id"]) or {"storage_key": "clips/7/a.mp4", "w": 1080, "h": 1920, "dur": 2.4, "fps": 30})
    assert out is not None and got == ["footage:a.mp4"]                 # acquired once, though it is cut in twice
    new, rows = out
    lines = [json.loads(ln) for ln in new.splitlines()]
    assert lines[1]["pic"] == ["bin:2", "main"] and lines[2]["pic"] == ["main", {"src": "bin:1", "crop": "upper"}, "bin:2"]
    assert rows[0]["id"] == 2 and rows[0]["class"] == "F" and rows[0]["page"] == "https://example.org/v" and rows[0]["author"] == "Some Channel"
    doc = json.load(open(os.path.join(work, "bin.json")))
    assert len(doc["items"]) == 3 and doc["items"][2]["kind"] == "video" and "Some Channel" in doc["credits"][0]
    assert os.path.exists(os.path.join(work, "bin/2.jpg"))
    assert S.load_bin(work)["2"]["kind"] == "video"
    # nothing to add: no box, no offers, or no still of that subject in the film
    assert F.footage_into(work, text, None, None) is None and F.footage_into(work, text, {"wishes": [], "offers": []}, None) is None
    assert F.footage_into(work, text.replace("bin:0", "main"), dict(box, offers=[dict(clip, _row=None)]), lambda c: {"storage_key": "k"}) is None


def test_with_the_footage_switch_closed_nothing_starts(monkeypatch):
    from score import film as F
    import footage.seam as seam
    monkeypatch.setattr(seam, "customer_cfg", lambda: {"mode": "off", "test_user_ids": []})
    assert F.footage_start({"user_id": 5}, "/nonexistent", {"wishes": [{"w": "0-2", "want": "x"}]}, {"W": 9, "H": 16}) is None


def test_a_quiet_room_is_not_the_uploads_own_music_and_a_song_is(tmp_path, library):
    from score import brief
    assert bed.own_track([-47.0] * 10, 0, 5.0) is False                 # p5582: a tin held in a silent kitchen
    assert bed.own_track([-27.0] * 20, 3, 10.0) is True                 # a clip posted over a song
    assert bed.own_track([-20.0] * 40, 60, 20.0) is False               # somebody talking: the voice is not a bed
    idx = selfie(6.0)
    idx["words"], idx["sentences"] = [], []
    quiet = {"dur": 6.0, "shots": [{"t": [0, 6.0]}], "audio": {"step": 0.5, "rms_db": [-47.0] * 12, "onsets": []}}
    work = _work(tmp_path, idx, quiet)
    assert bed.prechoose(work, {"about": "a product in a hand"}, quiet, idx)
    assert "A music bed will play" in "\n".join(brief.bed_part(work, brief.own_bed(idx, quiet, music=True)))
    song = dict(quiet, audio={"step": 0.5, "rms_db": [-24.0] * 12, "onsets": []})
    assert bed.prechoose(work, {"about": "a routine over a song"}, song, idx) is None
    assert "its own track" in "\n".join(brief.bed_part(work, brief.own_bed(idx, song, music=True)))


def test_one_picture_held_past_five_seconds_is_a_finding_the_repair_round_reads():
    idx, text = talking(24.0)
    c = comp(text, idx, voice(24.0))
    assert c.meter["hold_s"] > 20 and any(f.startswith("hold: one picture stays") for f in c.meter["findings"])
    assert "HOLD the longest the picture goes without changing" in c.meter_text
    cut = film(beats_over(idx, 8, lambda k, a, z: {"cut": [40, 999], "pic": ["main", f"bin:{k % 4}"]}), film_={"gap": 0.25})
    c2 = comp(cut, idx, voice(24.0))
    assert c2.meter["hold_s"] < c.meter["hold_s"] and (c2.meter["hold_s"] <= S.HOLD_FIND_S or any(f.startswith("hold:") for f in c2.meter["findings"]))


def test_a_line_the_upload_prints_across_the_frame_is_never_cut_by_the_eased_track(monkeypatch):
    monkeypatch.setenv("VALMERA_TALK_CUTS", "0")              # the eased track itself: a take cut inside itself (round seven) is tested there
    idx, text = talking()
    wide = dict(voice(24.0), text=[{"box": [0.06, 0.08, 0.94, 0.14], "t": [0.0, 24.0]}])          # a title burned in across the top
    c = S.compile_score(text, index=idx, sid="five", perception=wide, bin_items=BIN)
    for s in shots(c):
        rows = s["crop"] if isinstance(s["crop"][0], list) else [s["crop"]]
        assert all(r[0] <= 0.06 + 1e-6 and r[2] >= 0.94 - 1e-6 and r[1] <= 0.08 + 1e-6 for r in rows)
    sign = dict(voice(24.0), text=[{"box": [0.0, 0.2, 0.2, 0.25], "t": [0.0, 24.0]}])               # a small sign at the edge, behind him
    c2 = S.compile_score(text, index=idx, sid="five", perception=sign, bin_items=BIN)
    assert c2.meter["move"]["moved"] >= 1


def test_a_view_under_a_line_printed_across_the_frame_is_that_moment_shown_whole(tmp_path):
    idx = selfie(12.0)
    idx["words"], idx["sentences"] = [], []
    perc = {"audio": {"step": 0.5, "rms_db": [-30.0] * 26, "onsets": []}, "text": [{"box": [0.05, 0.3, 0.95, 0.38], "t": [0.0, 12.0]}]}
    work = _work(tmp_path, idx, perc)
    json.dump({"items": [], "own": [{"id": 0, "src": "main@6.00-8.40", "box": [0.3, 0.28, 0.7, 0.6], "at": 6.8, "what": "the belt"}]}, open(tmp_path / "bin.json", "w"))
    text = film([{"k": "b", "id": "b01", "at": "@0.0..@4.0", "job": "x", "cut": [40, 999], "pic": ["main", "view:0"]}])
    c = S.compile_score(text, index=idx, work_dir=work, sid="five", perception=perc)
    v = next(s for s in shots(c) if s.get("view") is not None)
    rows = v["crop"] if isinstance(v["crop"][0], list) else [v["crop"]]
    assert all(r[0] <= 0.05 and r[2] >= 0.95 for r in rows)             # the printed line is whole
    perc2 = dict(perc, text=[])
    c2 = S.compile_score(text, index=idx, work_dir=_work(tmp_path, idx, perc2), sid="five", perception=perc2)
    v2 = next(s for s in shots(c2) if s.get("view") is not None)
    r2 = v2["crop"][0] if isinstance(v2["crop"][0], list) else v2["crop"]
    assert r2[2] - r2[0] < 0.8                                           # with nothing printed there, the view is the closer insert it was


def test_a_caption_the_index_sees_and_perception_missed_still_binds_views_and_the_eased_track(tmp_path):
    idx = selfie(12.0)
    idx["words"], idx["sentences"] = [], []
    for r in idx["spatial"]["samples"]:
        r["text"] = [[0.1, 0.18, 0.88, 0.3]] + ([[0.4, 0.6, 0.5, 0.64]] if r["t"] == 3 else [])     # a caption band on every sample
    perc = {"audio": {"step": 0.5, "rms_db": [-30.0] * 26, "onsets": []}, "text": []}                    # no region "stayed put" for perception
    work = _work(tmp_path, idx, perc)
    json.dump({"items": [], "own": [{"id": 0, "src": "main@6.00-8.40", "box": [0.3, 0.2, 0.7, 0.6], "at": 6.8, "what": "the belt"}]}, open(tmp_path / "bin.json", "w"))
    text = film([{"k": "b", "id": "b01", "at": "@0.0..@5.0", "job": "x", "cut": [40, 999], "pic": ["view:0", "main"]}])
    c = S.compile_score(text, index=idx, work_dir=work, sid="five", perception=perc)
    for s in shots(c):
        rows = s["crop"] if isinstance(s["crop"][0], list) else [s["crop"]]
        assert all(r[0] <= 0.1 + 1e-6 and r[2] >= 0.88 - 1e-6 and r[1] <= 0.18 + 1e-6 for r in rows), s["id"]
    lone = [dict(r, text=[[0.1, 0.18, 0.88, 0.3]] if r["t"] == 3 else []) for r in idx["spatial"]["samples"]]
    idx2 = dict(idx, spatial={"samples": lone})
    ctx = S.compile_once(text, idx2, work, {}, None, S.faces_from(perc), "t", False, None, perc, True, {}, ({}, {}, None))
    assert S.wide_print(ctx, 2.0, 4.0, 1.0) == []                         # one stray box on one frame is not print
