"""Tools and turns, 26 Nov 2026 (session reviewer tickets).

#1841  cut_range on short ranges (a repeated word: "de de", "con con") came
       back NO CHANGE while re-reporting an unrelated edge repair; the agent
       looped to the no-progress stop and the condense kept its repetitions.

Run:  python -m pytest tests/test_ec2_tools_1126.py -q     (from worker/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import audit                                                  # noqa: E402
import sentence_snap                                          # noqa: E402
import word_edges                                             # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402


def _w(text, t0, t1):
    return {"w": text, "t0": t0, "t1": t1}


# Project 6307's own words (Spanish talk), three-decimal ASR times.
WORDS = [
    _w("y", 66.80, 67.30), _w("requiere", 67.40, 68.12),
    _w("presupuestos", 68.28, 68.84), _w("y", 68.84, 69.08),
    _w("mano", 69.08, 69.32), _w("de", 69.32, 69.48), _w("obra", 69.48, 69.72),
    _w("de", 69.72, 70.28), _w("de", 70.44, 71.08),
    _w("diferentes", 71.08, 71.56), _w("áreas.", 71.60, 72.10),
    _w("seguro", 73.21, 73.77), _w("para", 73.775, 74.015),
    _w("las", 74.015, 74.175), _w("personas", 74.175, 74.495),
    _w("con", 74.495, 74.815), _w("con", 74.815, 75.215),
    _w("discapacidad.", 75.215, 75.775),
]


class _Ctx:
    def __init__(self, keep, dur=90.0, shots=None, trial=True):
        self.project_id = 1
        self.duration = dur
        self.has_main_video = True
        self.workdir = None
        self.index = {"video": {"duration": dur, "width": 1920,
                                "height": 1080, "fps": 25.0},
                      "shots": shots or [], "words": list(WORDS),
                      "sentences": [], "silences": []}
        self.written = []
        edl = default_edl(dur)
        edl["keep"] = keep
        self._edl = validate_edl(edl, dur).model_dump()
        self.claude_trial = {"lane": 1} if trial else None
        self.claude_paid = None
        self._executing_tool = self._editor_tool = "cut_range"

    def cut_sound_source(self):
        return None

    def clamp(self, t):
        return round(min(max(float(t), 0.0), self.duration), 2)

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


def _removed(before, after):
    return agent_tools.output_duration(before) - agent_tools.output_duration(after)


def test_a_repeated_word_cut_at_its_shown_times_lands():
    """'con con' (74.495-74.815 shown as 74.50-74.81): the edges sat 5 ms
    inside the word on both sides and the whole-word pass restored it."""
    before = [[60.0, 80.0]]
    ctx = _Ctx(before)
    res = agent_tools.cut_range(ctx, 74.50, 74.81, snap_to_words=True)
    assert res.startswith("EDL v"), res
    keep = ctx._edl["keep"]
    assert len(keep) == 2
    assert abs(keep[0][1] - 74.495) < 0.002 and abs(keep[1][0] - 74.815) < 0.002
    assert abs(_removed(before, keep) - 0.32) < 0.01
    assert "Left the word 'con' out whole" in res


def test_an_in_sentence_stutter_cut_is_not_snapped_shut():
    """'obra de de diferentes': the sentence snap moved both edges of the
    cut to the same clause gap, so the cut closed and nothing was removed."""
    before = [[60.0, 80.0]]
    ctx = _Ctx(before)
    res = agent_tools.cut_range(ctx, 69.72, 70.4, snap_to_words=True)
    assert res.startswith("EDL v"), res
    keep = ctx._edl["keep"]
    # the first 'de' (69.72-70.28) is out, the second (70.44) kept whole
    assert keep[0][1] == 69.72 and 70.28 <= keep[1][0] <= 70.44
    assert "SENTENCE SNAP" not in res


def test_sentence_snap_keeps_a_trim_inside_one_sentence():
    prev = [[60.0, 80.0]]
    keep, notes = sentence_snap.snap_keep([[60.0, 69.72], [70.4, 80.0]],
                                          WORDS, 90.0, prev_keep=prev)
    assert keep == [[60.0, 69.72], [70.4, 80.0]] and notes == []


def test_a_sentence_snap_never_closes_a_cut():
    # a cut across the clause boundary after 'áreas.' still snaps, but the
    # two pieces never meet
    prev = [[60.0, 80.0]]
    keep, _notes = sentence_snap.snap_keep([[60.0, 71.3], [73.5, 80.0]],
                                           WORDS, 90.0, prev_keep=prev)
    assert len(keep) == 2 and keep[0][1] < keep[1][0]


def test_a_cut_leaves_every_other_edge_exactly_as_saved():
    """Rounding every edge rewrote word-exact edges far from the cut
    (511.525 -> 511.52) and each write re-placed and re-reported them."""
    before = [[60.0, 74.495], [74.815, 80.0], [81.0, 85.525]]
    ctx = _Ctx(before)
    res = agent_tools.cut_range(ctx, 69.72, 70.4, snap_to_words=True)
    keep = ctx._edl["keep"]
    assert [74.815, 80.0] in keep and [81.0, 85.525] in keep
    assert keep[1][1] == 74.495
    assert "74.495" not in res and "85.525" not in res
    assert audit.subtract_spans([[1.0, 5.525]], [[2.0, 3.004]]) == \
        [(1.0, 2.0), (3.0, 5.525)]


def test_a_saved_edge_is_not_pulled_back_onto_a_shot_cut():
    """The write boundary moved the end 1040.22 -> 1040.26 (sound still
    running); shot hygiene moved it back on every write, so every cut
    re-reported that stale repair."""
    shots = [{"id": 1, "start": 0.0, "end": 85.24},
             {"id": 2, "start": 85.24, "end": 90.0}]
    before = [[60.0, 85.26]]
    ctx = _Ctx(before, shots=shots)
    res = agent_tools.cut_range(ctx, 69.72, 70.4, snap_to_words=True)
    assert ctx._edl["keep"][-1][1] == 85.26
    assert "SHOT-CUT HYGIENE" not in res
    # a NEW edge on the cut is still moved off it
    keep, notes = agent_tools._snap_keep_to_shots(
        [[60.0, 85.26]], ctx.index, WORDS, prev_keep=[[60.0, 80.0]])
    assert keep[0][1] < 85.24 and notes


def test_whole_word_snap_still_keeps_a_word_an_edge_really_cuts():
    spans, moves = word_edges.whole([[60.0, 74.60]], WORDS, 90.0,
                                    previous=[[60.0, 80.0]])
    assert spans[0][1] >= 74.815 and "Kept the whole word 'con'" in moves[0]


# ── #1838: the First cut director's plates are not the customer's images ──

PLATE = {"id": 42421, "kind": "image_ref", "storage_key": "fetched/6309/d883d9b57349.png",
         "meta": {"director": True, "filename": "7eaeb7362a5ca7.png",
                  "made_from": "design", "pixel_opaque": True}}
MASK = {"id": 42422, "kind": "image_ref", "storage_key": "fetched/6309/8aca301b260d.png",
        "meta": {"director": True, "filename": "11d6960e78db75.png",
                 "made_from": "design", "pixel_opaque": False}}
PHOTO = {"id": 42500, "kind": "image_ref", "storage_key": "uploads/6309/meme.png",
         "meta": {"filename": "meme.png"}}


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def run(self, fn, *args, **kw):
        return list(self.rows)


def test_list_assets_never_offers_the_first_cut_plates():
    import read_tools
    from types import SimpleNamespace
    ctx = SimpleNamespace(project_id=6309, db=_Db([PLATE, MASK]),
                          latest_edl=lambda: {"version": 3, "json": {}})
    res = read_tools.list_assets(ctx, "image")
    assert "d883d9b57349" not in res and "8aca301b260d" not in res
    assert res.startswith("No image assets")
    ctx.db = _Db([PLATE, MASK, PHOTO])
    res = read_tools.list_assets(ctx, "image")
    assert "uploads/6309/meme.png" in res and "d883d9b57349" not in res


def test_the_turns_still_inventory_query_leaves_the_plates_out():
    import agent_loop
    import read_tools
    seen = {}

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args):
            seen["sql"] = sql

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

    agent_loop._image_assets(_Conn(), 6309)
    assert read_tools.NOT_DESIGN_PLATE_SQL in seen["sql"]
    assert read_tools.product_design_plate(PLATE)
    assert read_tools.product_design_plate(
        {**MASK, "meta": '{"director": true, "made_from": "design"}'})
    assert not read_tools.product_design_plate(PHOTO)
    # a director-made sound or moving still is not a plate
    assert not read_tools.product_design_plate(
        {"kind": "video_clip", "meta": {"director": True, "made_from": "design"}})


def test_a_montage_pool_never_holds_a_plate():
    from types import SimpleNamespace
    from tools_ext import montage_tools
    ctx = SimpleNamespace(project_id=6309, db=_Db([PLATE, MASK, PHOTO]))
    rows = montage_tools._project_assets(ctx, ("image_ref",))
    assert [r["id"] for r in rows] == [42500]


# ── #1837: a long film of a 16:9 presentation keeps the upload's frame ──

def test_a_long_condense_of_the_upload_keeps_the_uploads_own_frame():
    """p6307: 'reduce this 17-minute video to 10 minutes' started again from
    the upload and carried the first cut's 9:16 centre crop; the projected
    slides were off-frame for all 611 s."""
    from types import SimpleNamespace as NS
    import agent_loop
    from score import asks
    assert "frame" in asks.LEDGER_SCHEMA["required"]
    assert asks.LEDGER_SCHEMA["properties"]["frame"]["enum"] == ["first_cut", "upload"]
    base = {"lang": "es", "start": "upload", "asks": []}
    assert asks.settle(dict(base, frame="upload"), {"keep": [[0, 1]]})["frame"] == "upload"
    assert asks.settle(dict(base), {"keep": [[0, 1]]})["frame"] == "first_cut"

    first = {"keep": [[30.0, 52.0], [400.0, 421.7]], "frame": {"mode": "crop", "ratio": "9:16"},
             "captions": {"preset": "reels"}}
    writes = []

    def ctx():
        c = NS(request_edit_origin={"made_by": "automatic_first_cut", "customer_requests_since": 0},
               has_main_video=True, versions_written=[], duration=1042.25,
               latest_edl=lambda: {"version": 19, "json": first})

        def write(edl, what):
            writes.append(edl)
            c.versions_written.append(20)
            return "EDL v19 -> v20: " + what
        c.write_edl = write
        return c
    note = agent_loop._typed_start_from_upload(
        ctx(), dict(base, frame="upload"), {"id": 139897})
    fresh = writes[-1]
    assert fresh["keep"] == [[0.0, 1042.25]]
    assert not (fresh.get("frame") or {}).get("ratio")          # the upload's 16:9
    assert "keeps the upload's own frame" in note
    # a short or vertical film of the upload still keeps the first cut's shape
    agent_loop._typed_start_from_upload(ctx(), dict(base, frame="first_cut"), {"id": 2})
    assert writes[-1]["frame"]["ratio"] == "9:16"


# ── #1824 (covered on the base by 9fd6b1bd): a grade-only ask keeps the first cut ──

def test_a_grade_only_ask_on_the_first_cut_runs_on_that_cut():
    from types import SimpleNamespace as NS
    import agent_loop
    from score import asks
    # the ledger model is told a look/sound-only ask is about the film they see
    assert "p6303" in asks.LEDGER_SCHEMA["properties"]["start"]["description"]
    assert "only change how that film looks or sounds" in asks.LEDGER_SYSTEM
    first = {"keep": [[3.0, 8.0]], "frame": {"mode": "crop", "ratio": "9:16"},
             "texts": [{"id": "t1"}], "music": [{"id": "m1"}]}
    writes = []
    c = NS(request_edit_origin={"made_by": "automatic_first_cut", "customer_requests_since": 0},
           has_main_video=True, versions_written=[], duration=40.0,
           latest_edl=lambda: {"version": 3, "json": first},
           write_edl=lambda *a: writes.append(a) or "EDL v3 -> v4")
    led = asks.settle({"lang": "en", "start": "current", "frame": "first_cut", "asks": []}, first)
    assert agent_loop._typed_start_from_upload(c, led, {"id": 1}) == "" and writes == []


# ── p6307: the condense ends on a whole sentence and its repeats are named ──

END_WORDS = [_w("que", 1033.805, 1034.125), _w("5º", 1034.125, 1034.525),
             _w("y", 1034.525, 1034.765), _w("3º.", 1034.765, 1035.165),
             _w("Y,", 1035.165, 1035.325), _w("bueno,", 1035.325, 1035.645),
             _w("ya", 1035.645, 1035.725), _w("para", 1035.725, 1036.205),
             _w("terminar", 1036.205, 1036.845), _w("1º,", 1036.845, 1037.405),
             _w("porque", 1037.405, 1037.565), _w("parte", 1039.885, 1040.045),
             _w("de", 1040.045, 1040.205)]


def test_the_films_last_piece_ends_on_a_sentence_not_a_comma():
    """The last cut of the p6307 condense (1035.5-1040.5) left the film
    ending '...5º y 3º. Y, bueno,': the comma counted as clean."""
    prev = [[990.0, 1040.26]]
    keep, notes = sentence_snap.snap_keep([[990.0, 1035.645]], END_WORDS,
                                          1042.25, prev_keep=prev)
    assert len(keep) == 1 and abs(keep[0][1] - 1035.165) <= 0.006
    assert notes[0][3] == "sentence"
    # between two pieces a clause gap stays a clean cut point
    keep, notes = sentence_snap.snap_keep(
        [[990.0, 1035.645], [1036.2, 1040.26]], END_WORDS, 1042.25,
        prev_keep=prev)
    assert keep[0][1] == 1035.645


def test_the_spoken_repetition_note_names_where_the_repeats_are():
    note = agent_tools._spoken_repetition_note([
        {"phrase": "de de", "kind": "spoken_repetition",
         "occurrences": [{"program_s": 40.1, "source_s": 69.72},
                         {"program_s": 300.0, "source_s": 539.63}]}])
    assert "'de de' at source 69.72s, 539.63s" in note
    assert "unless the request asks to remove repetitions" in note
    assert "should not be cut" not in note


# ── #1830: a 9:16 window over a collage slideshow lands on a photo ──

def _collage(w=320, h=180):
    """A cream page with a photo panel on each side and a cream gutter in
    the middle (project 6305's last collage)."""
    import numpy as np
    rng = np.random.default_rng(7)
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[:, :] = (243, 236, 222)                          # cream page
    a[20:160, 10:120] = rng.integers(0, 255, (140, 110, 3))
    a[30:170, 215:310] = rng.integers(0, 255, (140, 95, 3))
    return a


def test_a_window_on_the_collage_gutter_slides_onto_a_photo():
    from score import panels
    mask, page = panels.picture_mask(_collage())
    assert mask is not None and page > 0.4
    centre = [0.342, 0.0, 0.658, 1.0]                  # the 9:16 middle of 16:9
    new, moved = panels.place(mask, centre)
    assert moved and moved[0] < 0.2 and moved[1] > 0.6
    assert abs((new[2] - new[0]) - (centre[2] - centre[0])) < 1e-3
    assert new[2] <= 0.4 or new[0] >= 0.66             # on the left or the right photo


def test_ordinary_footage_keeps_its_window():
    import numpy as np
    from score import panels
    noisy = np.random.default_rng(3).integers(0, 255, (180, 320, 3)).astype(np.uint8)
    mask, _share = panels.picture_mask(noisy)
    assert mask is None
    assert panels.place(mask, [0.342, 0.0, 0.658, 1.0]) == ([0.342, 0.0, 0.658, 1.0], None)


def test_the_lowering_moves_the_main_uploads_overlay_and_reports_it():
    from PIL import Image
    from score import panels
    img = Image.fromarray(_collage())
    ovs = [{"id": "ov.b07.s0", "kind": "video", "asset_key": "main", "start": 29.47,
            "duration_s": 4.43, "source_start_s": 41.47, "source_crop": [0.3298, 0.0, 0.6462, 1.0]},
           {"id": "ov.x", "kind": "image", "asset_key": "fetched/plate.png", "source_crop": [0.3, 0, 0.6, 1]}]
    rep = {}
    panels.repair_windows(ovs, lambda k: "/src.mp4" if k == "main" else None, rep,
                          grab=lambda path, t, width=None: img)
    assert ovs[0]["source_crop"] != [0.3298, 0.0, 0.6462, 1.0]
    assert rep["collage_windows"][0]["picture_after"] > 0.6
    assert ovs[1]["source_crop"] == [0.3, 0, 0.6, 1]


def test_a_person_before_a_plain_wall_keeps_the_window_on_them():
    import numpy as np
    from score import panels
    a = np.zeros((180, 320, 3), dtype=np.uint8)
    a[:, :] = (240, 240, 238)                                    # plain wall
    a[40:180, 120:200] = np.random.default_rng(1).integers(0, 120, (140, 80, 3))   # the person
    a[10:60, 250:318] = np.random.default_rng(2).integers(0, 255, (50, 68, 3))     # a busy poster
    mask, _ = panels.picture_mask(a)
    on_person = [0.342, 0.0, 0.658, 1.0]
    assert panels.place(mask, on_person) == (on_person, None)
