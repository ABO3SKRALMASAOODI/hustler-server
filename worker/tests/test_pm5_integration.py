"""Where two premium-motion tracks touch the same write: the unified order.

Each track's own suite pins its behaviour; these pin how they compose.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import motion_tools  # noqa: E402
from test_caption_carry import _Ctx, _probe_with  # noqa: E402


def test_a_number_lands_before_its_reading_is_timed(monkeypatch):
    # number landing (punch) moves a figure slam onto its spoken number; the
    # one reading path (fidelity) is worked out on the window as it LANDS,
    # so its caption note names the moved exit, not the requested one
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_with((0.1, 0.62, 0.9, 0.82)))
    ctx = _Ctx()
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.9, 3.0,
                                          params={"text": "*140*"}, id="slam")
    item = ctx.latest_edl()["json"]["motion"][0]
    assert (item["start"], item["end"]) == (2.78, 3.88), item
    assert out.index("NUMBER LANDED") < out.index("NOTE (captions)"), out
    # "characters" is said while the moved slam is up: the reading names it
    # and the exit the slam really has
    assert "until it leaves at 3.88s" in out and '"characters' in out, out
    # the end it names gives "characters" back (before its midpoint, 3.6 s)
    assert "End it at 3.58s" in out, out
    again = motion_tools.set_motion_graphic(ctx, "slam", end=3.58)
    assert "NOTE (captions)" not in again and "NUMBER LANDED" not in again, again


# ── a lockup's reveal times: one definition for the page and the engine ──
# The headline band yields from a phrase_build's first drawn word (layout)
# and a sound's visual partner is a row landing (sound); since the one
# reading path (fidelity) a lockup's rows land on their SPOKEN onsets, so
# both read caption_carry.lockup_reveals — the page's own timing.

import asyncio  # noqa: E402

import pytest  # noqa: E402

import caption_carry  # noqa: E402
import motion_layer  # noqa: E402
import motion_templates  # noqa: E402
import sfx_placement  # noqa: E402
from test_one_reading_path import PAPER_ROWS, _job, _states, needs_browser  # noqa: E402

SPOKEN = {"v": 1, "rows": [[0.7, 0.92, 1.28], [3.28, 3.5, 3.66], [3.9, 4.2]],
          "bridges": [{"after": -1, "words": [{"t": "and", "s": 0.4}, {"t": "so", "s": 0.52}]},
                      {"after": 0, "words": [{"t": "three", "s": 1.6}, {"t": "years", "s": 1.7}]}]}


def _pb(rows, reading=None, start=0.0, end=5.3, **params):
    m = {"id": "pb", "template": "phrase_build", "start": start, "end": end,
         "params": motion_templates.check_params("phrase_build", dict(params, rows=rows))}
    if reading:
        m["reading"] = reading
    return m


def test_reveals_follow_the_reading_not_the_fallback_at_times():
    # rows in reading order; a stored reading's bridge lines are not drawn
    # (round 4: a lockup sets only its rows), so they reveal nothing
    assert caption_carry.lockup_reveals(_pb(PAPER_ROWS, SPOKEN)) == [0.7, 3.28, 3.9]
    # no reading: the rows' own 'at' times
    assert caption_carry.lockup_reveals(_pb(PAPER_ROWS)) == [0.7, 3.28, 3.9]
    # a 'rise' word starts 0.06 s early; reading order is never broken
    rise = _pb([{"text": "late", "at": "2.0"}, {"text": "early", "at": "1.0"}], entrance="rise")
    assert caption_carry.lockup_reveals(rise) == [2.0, 2.0]
    assert caption_carry.lockup_reveals(_pb(PAPER_ROWS[:1], {"v": 1, "rows": [[0.7, 0.92, 1.28]]},
                                            entrance="rise")) == [0.64]
    assert caption_carry.lockup_reveals({"template": "word_slam", "params": {}}) == []


def test_the_headline_yields_from_the_lockups_first_spoken_word():
    lock = _pb(PAPER_ROWS, SPOKEN, start=10.0, end=15.3)
    assert motion_layer._ink_lead(lock) == 0.7          # the first spoken row
    unread = _pb(PAPER_ROWS, start=10.0, end=15.3)
    assert motion_layer._ink_lead(unread) == 0.7        # the first row's 'at'
    # a stitched piece 0.5 s into the composition waits that much less
    assert motion_layer._ink_lead(dict(lock, phase_s=0.5)) == pytest.approx(0.2)
    assert motion_layer._ink_lead(dict(lock, phase_s=1.0)) == 0.0


def test_a_sound_partner_is_a_spoken_row_landing():
    lock = _pb(PAPER_ROWS, SPOKEN, start=10.0, end=15.3)
    lands = sfx_placement._motion_landings(lock)
    assert 13.28 in lands and 13.9 in lands and 10.7 in lands and 10.4 not in lands
    # the fallback 'at' of a spoken row is no landing any more
    lock2 = _pb([dict(PAPER_ROWS[0], at="0.2")] + PAPER_ROWS[1:], SPOKEN, start=10.0, end=15.3)
    assert 10.2 not in sfx_placement._motion_landings(lock2)


def test_a_standing_headline_and_a_cut_step_are_no_sound_partners():
    from schemas import default_edl, validate_edl
    edl = default_edl(30.0)
    edl["motion"] = [{"id": "hl", "template": "headline", "start": 0.0, "end": 30.0,
                      "params": motion_templates.check_params("headline", {"text": "A *claim*"})},
                     {"id": "mid", "template": "headline", "start": 12.0, "end": 30.0,
                      "params": motion_templates.check_params("headline", {"text": "Chapter"})}]
    edl["effects"] = {"zooms": [{"id": "cs1", "start": 4.0, "end": 6.0, "scale": 1.08,
                                 "mode": "punch", "ramp_s": 0.0, "cut_step": True}]}
    edl = validate_edl(edl, 30.0).model_dump()
    ev = sfx_placement.visual_events(edl)
    times = [round(t, 2) for t, _label in ev]
    assert 0.0 not in times and 30.0 not in times      # there from the first frame to the last
    assert 12.0 in times                                # a mid-program entrance still counts
    assert 4.0 not in times and 6.0 not in times        # a concealed jump cut is an ordinary cut


@needs_browser
def test_lockup_reveals_match_the_page():
    cases = [(PAPER_ROWS, SPOKEN, {}),
             (PAPER_ROWS, None, {}),
             ([{"text": "Steve Jobs, 1983", "role": "sans", "size": "0.45", "at": "0"},
               {"text": "computer fonts were", "role": "serif", "size": "0.8", "at": ""},
               {"text": "*GARBAGE*", "role": "condensed", "size": "1.5", "at": "0.3"}], None, {})]
    for rows, reading, params in cases:
        reveals = caption_carry.lockup_reveals(_pb(rows, reading, **params))
        times = []
        for t in reveals:
            times += [max(0.0, t - 0.012), t + 0.012]
        st = asyncio.run(_states(_job(rows, reading, end=5.3, **params), times))
        for k, t in enumerate(reveals):
            before, after = st[2 * k][k], st[2 * k + 1][k]
            assert after["words"][0] == 1, (rows, reading, k, t)
            if t > 0.012:
                assert before["words"][0] == 0, (rows, reading, k, t)


def test_the_end_it_at_advice_never_makes_a_flash(monkeypatch):
    # "ENOUGH" lands and "to take our civilization" follows at once: ending
    # the slam before "to" would leave it up for 0.43 s, a flash — the note
    # offers carrying the words instead
    import motion_tools as mt
    from test_one_reading_path import _edl, _index
    from timeline import Timeline
    words = [("it's", 4.9, 5.0), ("not", 5.0, 5.1), ("quite", 5.1, 5.2),
             ("been", 5.2, 5.3), ("enough", 5.45, 5.62), ("to", 5.66, 5.74),
             ("take", 5.74, 5.95), ("our", 5.95, 6.1), ("civilization", 6.1, 6.6),
             ("to", 6.6, 6.7), ("the", 7.0, 7.1), ("next", 7.1, 7.3), ("level.", 7.3, 7.8)]
    slam = {"id": "enough", "template": "word_slam", "start": 5.25, "end": 6.9,
            "params": {"text": "*enough*"},
            "footprint": {"box": [0.1, 0.3, 0.9, 0.42], "ar": round(1080 / 1920, 4), "faces": []}}
    edl = _edl([[0.0, 9.0]], [slam], words=words)
    ix = _index(words)
    tl = Timeline(edl["keep"])
    notes = mt._word_level_notes(edl, ix, tl, edl["motion"][0], canvas=(1080, 1920))
    said = [n for n in notes if "never reads" in n]
    assert said and "End it at" not in said[0] and "Carry them on it" in said[0], notes


# ── the band above a card: lockups grown by bridge lines stay in it ──────
# The headline band (layout) is the free band above a picture card; a
# lockup set there grows with its one-reading-path bridge lines (fidelity)
# and could touch the card (judged on the Jobs 'paper' lockup). The write
# says so, from the same band the headline uses.

def _band_probe(box):
    return lambda item, W_, H_, fps=30.0: {
        "errors": [], "visible_frames": 4, "samples": 4, "bboxes": [list(box)],
        "ink": [list(box)] * 4}


def test_a_lockup_spilling_out_of_the_band_is_named(monkeypatch):
    from test_headline_band import _Ctx as BandCtx, _card_edl
    rows = [{"text": "no college student", "role": "sans", "size": "0.6", "at": "0.2"},
            {"text": "WITHOUT *ONE*", "role": "condensed", "size": "1.4", "at": "1.0"}]
    ctx = BandCtx(_card_edl())
    monkeypatch.setattr(motion_tools, "_probe_item", _band_probe((0.12, 0.062, 0.88, 0.335)))
    out = motion_tools.add_motion_graphic(ctx, "phrase_build", 20.0, 24.0,
                                          params={"rows": rows, "y": 0.15}, id="paper")
    assert "NOTE (band)" in out and "onto the top of the picture card" in out \
        and "rises into the feed header" in out, out
    # inside the band: nothing to say
    monkeypatch.setattr(motion_tools, "_probe_item", _band_probe((0.12, 0.09, 0.88, 0.28)))
    out = motion_tools.set_motion_graphic(ctx, "paper", params={"width": 0.66, "y": 0.19})
    assert "NOTE (band)" not in out, out
    # a graphic set ON the card is not a band graphic
    monkeypatch.setattr(motion_tools, "_probe_item", _band_probe((0.12, 0.40, 0.88, 0.55)))
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 26.0, 27.0,
                                          params={"text": "*one*", "y": 0.48}, id="slam")
    assert "NOTE (band)" not in out, out


# ── follow: a turn the frontal index loses is measured ───────────────────
# The showcase index's spatial samples are frontal-only: Elon's turn to
# Rogan (136.8-137.8 s) has no face in them, so the dense-index path saw a
# still speaker and never followed the turn the reframe track fixed on the
# proxy. A shot whose index loses the face for >= 1 s is measured instead.

def _ix(faces_at):
    return {"spatial": {"samples": [
        {"t": t, "faces": [[0.4, 0.2, 0.6, 0.6]] if has else []} for t, has in faces_at]}}


def test_the_index_losing_the_face_mid_shot_is_noticed():
    import follow
    steady = _ix([(t / 2, True) for t in range(0, 20)])
    assert not follow.index_loses_face(steady, [(0.0, 10.0)])
    turned = _ix([(t / 2, t < 14) for t in range(0, 20)])          # lost from 7.0 to the end
    assert follow.index_loses_face(turned, [(0.0, 10.0)])
    blink = _ix([(t / 2, t != 8) for t in range(0, 20)])            # one faceless sample
    assert not follow.index_loses_face(blink, [(0.0, 10.0)])
    never = _ix([(t / 2, False) for t in range(0, 20)])             # no speaker to lose
    assert not follow.index_loses_face(never, [(0.0, 10.0)])


def test_a_shot_the_index_loses_is_followed_from_the_proxy(monkeypatch):
    import agent_tools
    import follow

    class C:
        index = dict(_ix([(t / 2, t < 14) for t in range(0, 20)]),
                     video={"width": 1920, "height": 1080})

        def proxy_path(self):
            return "proxy.mp4"
    calls = []
    monkeypatch.setattr(agent_tools, "_measure_faces",
                        lambda ctx, w, a, cuts: calls.append(w) or ([(0.0, [])], set(), None, {}))
    monkeypatch.setattr(follow, "speaker_track", lambda frames, cuts: [(0.0, [0.4, 0.2, 0.6, 0.6], 0.0)])
    samples, how, _counts, failure = agent_tools._follow_samples(C(), [(0.0, 10.0)])
    assert how == "measured" and calls and failure is None, how
    C.index = dict(_ix([(t / 2, True) for t in range(0, 20)]), video={"width": 1920, "height": 1080})
    samples, how, _counts, _failure = agent_tools._follow_samples(C(), [(0.0, 10.0)])
    assert how == "index"


def test_a_lost_face_never_costs_the_dense_index(monkeypatch):
    # the proxy measurement replaces a dense index that loses a turn only
    # when it can: more footage than one call measures, or a measurement
    # that finds nothing, keeps following from the index (it used to come
    # back 'too_long' / 'sparse': no follow and no framing checks at all)
    import agent_tools
    import follow

    long_s = follow.MAX_MEASURE_S + 40.0
    n = int(long_s * 2)

    class C:
        index = dict(_ix([(t / 2, not (100 <= t < 110)) for t in range(n)]),
                     video={"width": 1920, "height": 1080})

        def proxy_path(self):
            return "proxy.mp4"
    calls = []
    monkeypatch.setattr(agent_tools, "_measure_faces",
                        lambda ctx, w, a, cuts: calls.append(w) or ([], set(), None, {}))
    monkeypatch.setattr(follow, "speaker_track", lambda frames, cuts: [])
    _s, how, _c, _f = agent_tools._follow_samples(C(), [(0.0, long_s)])
    assert how == "index" and not calls, how
    # a short program the measurement finds nothing in: still the index
    C.index = dict(_ix([(t / 2, t < 14) for t in range(0, 20)]),
                   video={"width": 1920, "height": 1080})
    _s, how, _c, _f = agent_tools._follow_samples(C(), [(0.0, 10.0)])
    assert how == "index" and calls, how
    # ...and so is one the measurement could not complete (the caller must
    # not be told the follow failed while the index still follows)
    monkeypatch.setattr(agent_tools, "_measure_faces",
                        lambda ctx, w, a, cuts: ([], set(), {"why": "budget"}, {}))
    _s, how, _c, failure = agent_tools._follow_samples(C(), [(0.0, 9.5)])
    assert how == "index" and failure is None, how


# ── the plate under a graphic in a card layout is the card's backdrop ────
# Elon's stack (reframeqc) put the stat slams in the band above the speaker
# panel (layout's band); the plate probe measured the uncropped frame there
# (the bright neon sign), so word_slam switched its light words to dark ink
# — black type on the dark backdrop. The plate is now the composed card.

def test_a_card_layout_plate_is_the_backdrop_and_the_panels():
    import plate
    from PIL import Image
    img = Image.new("L", (320, 180), 255)                       # a bright frame
    img.paste(0, (160, 0, 320, 180))                            # right half black
    rows = plate.grid_rows(1080, 1920)
    card = {"box": [0.04, 0.3, 0.96, 0.6], "background": "#241414",
            "background_color2": "#0A0606", "background_style": "radial_gradient",
            "vignette": 0.35,
            "panels_at": [[[0.04, 0.3, 0.96, 0.6], [0.0, 0.0, 0.5, 1.0]]]}   # the white half
    g = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop", card=card)
    band = g[2 * plate.COLS:(int(0.25 * rows)) * plate.COLS]   # the band above the card
    inside = g[(int(0.45 * rows)) * plate.COLS:(int(0.45 * rows) + 1) * plate.COLS]
    assert max(band) < 60                                       # the dark backdrop
    assert min(inside[2:-2]) > 200                              # the panel shows the white half
    full = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop", focus=(0.2, 0.5))
    assert min(full[2 * plate.COLS:int(0.25 * rows) * plate.COLS]) > 200   # what it measured before


def test_the_probe_composes_the_card_live_at_that_moment():
    import plate
    edl = {"effects": {"picture_cards": [
        {"id": "s", "start": 2.0, "end": 6.0, "box": [0.04, 0.245, 0.96, 0.88],
         "panels": [{"box": [0.04, 0.245, 0.96, 0.555], "source": [0.155, 0.148, 0.595, 0.6165]},
                    {"box": [0.04, 0.587, 0.96, 0.883], "source": [0.53, 0.52, 0.98, 0.98]}],
         "background": "#241C14", "background_style": "radial_gradient"}]}}
    assert plate._card_at(edl, 1.0, 100.0) is None
    card = plate._card_at(edl, 3.0, 100.0)
    assert card and [p[1] for p in card["panels_at"]] == [[0.155, 0.148, 0.595, 0.6165],
                                                           [0.53, 0.52, 0.98, 0.98]]


def test_a_browserless_estimate_is_named_only_past_its_own_error(monkeypatch):
    # production agent, MCP and shorts lanes have no browser at write time:
    # a lockup's box there is the template's estimate, which runs ~0.03-0.05
    # taller than the real block. The Jobs hook (a real 0.087-0.273 in this
    # band) estimates 0.054-0.306 — it must not be told to shrink; a block
    # set grossly into the header still is, and the note says it estimated
    from test_headline_band import _Ctx as BandCtx, _card_edl
    rows = [{"text": "Steve Jobs, 1983", "role": "sans", "size": "0.45", "at": "0"},
            {"text": "computer fonts were", "role": "serif", "size": "0.8", "at": ""},
            {"text": "*GARBAGE*", "role": "condensed", "size": "1.5", "at": "0.3"}]
    ctx = BandCtx(_card_edl())
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W_, H_, fps=30.0: None)
    out = motion_tools.add_motion_graphic(ctx, "phrase_build", 0.0, 4.2,
                                          params={"rows": rows, "y": 0.18, "width": 0.82},
                                          id="hook")
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["footprint"].get("estimated"), item.get("footprint")
    assert "NOTE (band)" not in out, out
    out = motion_tools.set_motion_graphic(ctx, "hook", params={"y": 0.12})
    assert "NOTE (band): by its estimated box" in out and "feed header" in out, out


def test_a_blur_backdrop_is_the_first_panels_footage_as_the_card_draws_it():
    # picture_cards._blur_chain fills the canvas with the FIRST panel's
    # footage, cover-scaled, blurred and its luma dimmed toward 16 by
    # background_dim (default BLUR_DIM_DEFAULT) — not the whole frame
    # stretched (a bright wall beside a dark-shirted speaker read as a
    # bright band above the card)
    import picture_cards
    import plate
    from PIL import Image
    img = Image.new("L", (320, 180), 255)                       # a bright frame
    img.paste(0, (0, 0, 160, 180))                              # the speaker's dark half
    rows = plate.grid_rows(1080, 1920)
    card = {"box": [0.04, 0.3, 0.96, 0.6], "background_style": "blur",
            "panels_at": [[[0.04, 0.3, 0.96, 0.6], [0.0, 0.0, 0.5, 1.0]]]}
    g = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop", card=card)
    band = g[2 * plate.COLS:(int(0.25 * rows)) * plate.COLS]
    assert max(band) < 40, band                                 # dark, as drawn
    lit = dict(card, background_dim=0.0,
               panels_at=[[[0.04, 0.3, 0.96, 0.6], [0.5, 0.0, 1.0, 1.0]]])
    g = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop", card=lit)
    assert min(g[2 * plate.COLS:(int(0.25 * rows)) * plate.COLS]) > 230   # 0 dims nothing
    assert picture_cards.BLUR_DIM_DEFAULT > 0
