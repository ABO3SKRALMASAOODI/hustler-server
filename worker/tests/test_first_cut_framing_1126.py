"""First-cut framing, type and camera, measured (tickets #904, #1298, #944, #1191).

Geometry and signal only: text boxes against the crop window, a subtitle
line's band, the recognizer's own confidence, source pixels per output pixel
and the size of a cut. Nothing reads what the words say.
"""
from types import SimpleNamespace

import pytest

import captions
import first_cut
import first_cut_visual as fcv
import source_typography
from score import compiler, source_guard


W916 = (9 / 16) * (1080 / 1920)       # a 9:16 crop of a 16:9 source: 0.316 wide


def _screen_rows(t0, t1, step=3.0, faces=None, subtitle=True, spread=(.17, .85)):
    """p6312's spatial samples: UI lines across most of the width and the
    tutorial's own subtitle pill at y 0.85-0.91, its width changing."""
    rows, k, t = [], 0, t0
    while t <= t1:
        w = (.20, .32, .38, .26, .44)[k % 5]
        text = [[spread[0], .30, .45, .36], [.50, .30, spread[1], .36]]
        if subtitle:
            text.append([.5 - w / 2, .85, .5 + w / 2, .91])
        rows.append({"t": round(t, 2), "text": text, "faces": list(faces or []),
                     "dense_ui": False})
        t += step
        k += 1
    return rows


def _screen_index(rows, perception=None, duration=213.4):
    return {"_first_film_source_guard": True,
            "video": {"width": 1920, "height": 1080, "duration": duration},
            "spatial": {"samples": rows},
            "director_preparation": {"version": 1, "perception": perception} if perception else {},
            "shots": [{"start": 0, "end": duration}]}


# ---------------------------------------------------------------- #1298


def test_moving_screen_recording_with_a_wide_subtitle_is_fitted_not_cropped():
    # p6312's measured perception: a screen recording (change 6.9-90.8), no
    # face, its subtitle line 0.40 wide in 38 samples against a 0.316 crop.
    per = {"shots": [{"t": [0., 100.], "change": 15.66, "faces_seen": 0.}],
           "text": [{"t": [55.78, 196.4], "box": [.3, .8444, .7, .9111], "seen": 38}],
           "palette": [{"hex": "#101418"}]}
    film = {"frame": [720, 1280], "items": [{"id": "s", "pic": "main",
            "src": [60., 62.], "crop": [.34, 0, .66, 1]}]}
    report = source_guard.apply(film, _screen_index([], duration=100.), per)
    assert report[0]["reason"] == "static_source_text_fit"
    assert film["items"][0]["crop"][0] <= .3 and film["items"][0]["crop"][2] >= .7


def test_spatial_text_spread_fits_the_kept_footage_without_director_perception():
    rows = _screen_rows(160., 212.)
    assert source_guard.kept_text_needs_fit(_screen_index(rows), [[170.31, 209.41]])
    # one sample read near a one-second piece still sees the layout around it
    assert source_guard.text_needs_fit(None, 171., 172., W916, 1., rows)


def test_a_face_or_narrow_text_keeps_the_subject_crop():
    face = [[.45, .2, .55, .4]]
    assert not source_guard.kept_text_needs_fit(
        _screen_index(_screen_rows(160., 212., faces=face)), [[170., 209.]])
    narrow = _screen_rows(160., 212., subtitle=False, spread=(.40, .60))
    assert not source_guard.kept_text_needs_fit(_screen_index(narrow), [[170., 209.]])


def _idx(rows, duration=60.):
    return {"video": {"width": 1920, "height": 1080, "duration": duration},
            "spatial": {"samples": rows}, "shots": [{"start": 0, "end": duration}]}


@pytest.mark.parametrize("boxes", [
    # a channel bug top-left and a date stamp bottom-right
    [[.03, .04, .12, .08], [.85, .92, .97, .96]],
    # a game HUD's four corners
    [[.02, .02, .15, .06], [.85, .02, .98, .06], [.02, .92, .15, .97], [.85, .92, .98, .97]],
    # two street signs on one row, a street apart
    [[.1, .3, .3, .36], [.6, .32, .8, .38]],
])
def test_separate_pieces_of_text_on_faceless_footage_keep_the_crop(boxes):
    rows = [{"t": float(t), "text": boxes, "faces": []} for t in range(0, 60, 3)]
    assert not source_guard.kept_text_needs_fit(_idx(rows), [])
    ctx = SimpleNamespace(index=_idx(rows), latest_edl=lambda: {"json": {"keep": [[0., 60.]]}})
    assert not first_cut._text_wider_than_crop(ctx)


def test_one_printed_line_wider_than_the_crop_is_one_line_across_word_gaps():
    words = [[.20 + .1 * k, .85, .28 + .1 * k, .90] for k in range(5)]     # five words, 0.02 apart
    lines = source_guard.printed_lines({"text": words})
    assert len(lines) == 1 and lines[0][2] - lines[0][0] == pytest.approx(.48)
    rows = [{"t": float(t), "text": words, "faces": []} for t in range(0, 60, 3)]
    assert source_guard.kept_text_needs_fit(_idx(rows), [])


def test_chain_composes_wide_text_footage_like_a_screen():
    rows = _screen_rows(160., 212.)
    ctx = SimpleNamespace(index=_screen_index(rows),
                          latest_edl=lambda: {"json": {"keep": [[170.31, 209.41]]}})
    assert first_cut._text_wider_than_crop(ctx)
    ctx.latest_edl = lambda: {"json": {"keep": [[0., 40.]]}}
    assert not first_cut._text_wider_than_crop(ctx)


# ---------------------------------------------------------------- #904


def _words(t0, t1, conf=.95):
    out, t = [], t0
    while t < t1:
        out.append({"w": "word", "t0": round(t, 2), "t1": round(t + .3, 2), "confidence": conf})
        t += .4
    return out


def test_the_uploads_own_changing_subtitle_line_is_measured_as_captions():
    index = _screen_index(_screen_rows(160., 212.))
    index["words"] = _words(165., 209.)
    assert source_typography.subtitle_share(index, 165., 209.) >= .8
    assert source_typography.kept_subtitles(index, [[170.31, 209.41]])


def test_wandering_scene_print_and_a_fixed_footer_are_not_subtitles():
    wander = [{"t": float(t), "text": [[.35, .40 + .06 * (k % 6), .35 + .15 + .03 * (k % 4), .46 + .06 * (k % 6)]]}
              for k, t in enumerate(range(0, 60, 3))]
    assert source_typography.subtitle_share({"spatial": {"samples": wander}}, 0., 60.) == 0.
    footer = [{"t": float(t), "text": [[.3, .88, .7, .93]]} for t in range(0, 60, 3)]
    assert source_typography.subtitle_share({"spatial": {"samples": footer}}, 0., 60.) == 0.
    edge = [{"t": float(t), "text": [[.3 + .01 * (t % 7), .94, .7, 1.]]} for t in range(0, 60, 3)]
    assert source_typography.subtitle_share({"spatial": {"samples": edge}}, 0., 60.) == 0.


def test_print_on_a_talking_heads_chest_is_not_the_uploads_subtitles():
    # a talking head with chest print in the lower half: the detector's box moves with the speaker, or stays put and
    # jitters by a few hundredths. Before: burned True, the first film went out with no captions, hook or slams
    words = _words(0., 60.)
    sway = [{"t": float(t), "text": [[.5 + d - w / 2, .58, .5 + d + w / 2, .64]], "faces": [[.4, .1, .6, .35]]}
            for k, t in enumerate(range(0, 60, 3))
            for d, w in [((-.06, .03, -.02, .06, 0., -.04)[k % 6], (.16, .20, .24, .18, .22)[k % 5])]]
    jitter = [{"t": float(t), "text": [[.5 - w / 2, .58, .5 + w / 2, .64]], "faces": [[.4, .1, .6, .35]]}
              for k, t in enumerate(range(0, 60, 3)) for w in [(.18, .20, .22, .19, .21)[k % 5]]]
    for rows in (sway, jitter):
        index = {"spatial": {"samples": rows}, "words": words}
        assert source_typography.subtitle_share(index, 0., 60.) == 0.
        assert not source_typography.kept_subtitles(index, [[0., 60.]])


def test_director_reads_the_same_subtitle_measure():
    from score import taste
    index = _screen_index(_screen_rows(160., 212.))
    words = _words(165., 209.)
    assert taste.upload_captions(SimpleNamespace(index=index, words=words)) >= .5


# ---------------------------------------------------------------- #944


def test_the_first_film_does_not_treat_a_transcript_the_recognizer_doubts_as_speech():
    musica = {"words": [{"w": "MÚSICA", "t0": 2.5, "t1": 3.9, "confidence": .5237}] * 9}
    assert not first_cut._speech(musica)
    fireworks = {"words": [{"w": w, "t0": i * .3, "t1": i * .3 + .2, "confidence": c}
                           for i, (w, c) in enumerate(zip(
                               ["Tämä", "on", "hän,", "tämä", "on", "hän.", "Jahita,", "jahita."],
                               [.35, .85, .31, .34, .88, .52, .6, .7]))]}
    assert not first_cut._speech(fireworks)


def test_real_speech_in_a_loud_room_and_unknown_confidences_are_speech():
    party = {"words": [{"w": "word", "t0": i, "t1": i + .5, "confidence": c}
                       for i, c in enumerate([.91, .98, .72, .71, .66, .78, 1., .73, .85, .29, .71, .6])]}
    assert first_cut._speech(party)
    corrected = {"words": [{"w": "word", "t0": i, "t1": i + .5, "confidence": None} for i in range(9)]}
    assert first_cut._speech(corrected)


def test_an_accented_narrator_the_recognizer_doubts_is_still_speech():
    # a faceless narration in a language whisper is less sure of: mean about
    # 0.62, under NONSPEECH_MEAN, but most of its words different and a third
    # of them heard clearly. Before: _speech False, cut and left as silent footage
    text = ("today we look at how the old harbour was rebuilt after the storm and "
            "why the fishermen came back to work there every single morning").split()
    pattern = [.92, .55, .48, .85, .5, .6, .42, .88, .52, .58]
    narration = {"words": [{"w": w, "t0": i * .4, "t1": i * .4 + .3, "confidence": pattern[i % len(pattern)]}
                           for i, w in enumerate(text)]}
    mean = sum(w["confidence"] for w in narration["words"]) / len(narration["words"])
    assert .6 < mean < first_cut.NONSPEECH_MEAN
    assert first_cut._speech(narration)
    # a hallucinated sign-off repeated over a silent upload, the same mean: the same three words, never speech
    signoff = {"words": [{"w": w, "t0": i * .4, "t1": i * .4 + .3, "confidence": pattern[i % len(pattern)]}
                         for i, w in enumerate(["Thanks", "for", "watching!"] * 8)]}
    assert not first_cut._speech(signoff)
    # under the floor nothing counts
    doubted = {"words": [dict(w, confidence=.4) for w in narration["words"]]}
    assert not first_cut._speech(doubted)


def test_captions_the_user_asks_for_keep_every_word_whatever_the_confidence():
    # the shared caption source never drops a transcript (a lyric video, an
    # accented speaker on whisper): only the first film's own choice reads it
    accented = {"words": [{"w": "word", "t0": i, "t1": i + .5, "confidence": .4} for i in range(12)]}
    assert len(captions._spoken_index_words(accented)) == 12


def test_a_low_resolution_source_is_never_punched_past_the_ceiling():
    ctx = SimpleNamespace(max_up=compiler.MAX_UP, soft_at=3.4, soft={}, found=[])
    ctx.find = ctx.found.append
    asset = {"w": 480, "h": 854}
    # p5571: a 1.75x punch on a 480-wide portrait clip in a 1080 frame (3.9x)
    w = 1 / 1.75
    r = compiler.limit_up(ctx, [.5 - w / 2, .5 - w / 2, .5 + w / 2, .5 + w / 2], asset, 1080, 1920, "main")
    up = 1080 / ((r[2] - r[0]) * 480)
    assert up <= compiler.MAX_UP + 1e-6 and ctx.found
    assert (r[2] - r[0]) == pytest.approx(r[3] - r[1])            # its shape kept
    # a landscape cover crop already past the ceiling stays the whole height,
    # never stretched wider than its shape
    cover = [.5 - .158, 0., .5 + .158, 1.]
    r = compiler.limit_up(ctx, list(cover), {"w": 640, "h": 360}, 1080, 1920, "main")
    assert r == pytest.approx(cover)


# ---------------------------------------------------------------- #1191


def test_a_sub_perceptual_silence_cut_inside_one_take_is_joined():
    shots = [{"start": 1753.13, "end": 1761.37}, {"start": 1761.37, "end": 1767.23}]
    words = [{"w": "thank", "t0": 1759.02, "t1": 1759.42}, {"w": "you,", "t0": 1760.06, "t1": 1760.3}]
    # p6269: the silence cuts left a 40 ms jump inside shot 199, between words
    assert first_cut.micro_cut_keep([[1744.86, 1759.76], [1759.8, 1762.96]], shots, words) == \
        [[1744.86, 1762.96]]
    # a real pause cut, and a tiny cut on the source's own cut, both stay
    assert first_cut.micro_cut_keep([[0., 5.], [5.4, 9.]], [{"start": 0, "end": 10}]) is None
    assert first_cut.micro_cut_keep([[1750., 1761.33], [1761.4, 1764.]], shots) is None


def test_a_short_filler_or_stutter_cut_at_its_word_times_stays_cut():
    # remove_filler_words cuts exactly at the word: a 90 ms "uh" is a word's gap
    words = [{"w": "so", "t0": 4.6, "t1": 4.9}, {"w": "uh", "t0": 5.0, "t1": 5.09, "filler": True},
             {"w": "this", "t0": 5.12, "t1": 5.4}]
    assert first_cut.micro_cut_keep([[0., 5.0], [5.09, 9.]], [{"start": 0, "end": 10}], words) is None


def _zoom_ctx(edl, w=1280, h=720):
    return SimpleNamespace(has_main_video=True, latest_edl=lambda: {"json": edl},
                           index={"video": {"width": w, "height": h, "duration": 30.}})


def test_move_room_is_measured_where_the_move_goes_not_over_the_whole_film():
    # a 720p landscape source cropped to 9:16 with one 0.12 zoom at 3-5 s
    # (conceal_jump_cuts): 10-13 s still holds a visible move
    edl = {"keep": [[0., 30.]], "frame": {"ratio": "9:16", "mode": "crop"},
           "effects": {"zooms": [{"start": 3., "end": 5., "strength": .12}]}}
    ctx = _zoom_ctx(edl)
    assert fcv._move_room(ctx, edl, 3., 5.) < fcv.CUT_IN_VISIBLE
    assert fcv._move_room(ctx, edl, 10., 13.) >= fcv.CUT_IN_VISIBLE


def test_cut_ins_hold_the_framing_only_where_the_source_cannot_show_a_move(monkeypatch):
    words = [{"w": "word,", "t0": i * .5, "t1": i * .5 + .4} for i in range(60)]
    edl = {"keep": [[0., 30.]], "frame": {"ratio": "9:16", "mode": "crop"},
           "effects": {"zooms": [{"start": 0., "end": 15., "strength": .12}]}}
    ctx = _zoom_ctx(edl)
    ctx.index["words"] = words
    import device_planner
    monkeypatch.setattr(device_planner, "program_words", lambda e, i: words)
    calls = []

    def run(ctx, steps, name, args, started):
        calls.append(args)
        return "EDL v2 -> v3"
    steps = []
    n = fcv.cut_ins(ctx, steps, 0, run, [], (.5, .4), 1.0)
    # every move written lies where the source still holds a visible one
    assert n > 0 and calls and all(c["start"] >= 15. - 1e-6 for c in calls)
    assert any(s.get("tool") == "cut_ins" and "not written" in s["note"] for s in steps)
    # a source with room everywhere gets its moves from the start
    calls.clear()
    edl["effects"] = {}
    assert fcv.cut_ins(ctx, [], 0, run, [], (.5, .4), 1.0) > 0 and calls[0]["start"] < 15.


def test_a_slide_decks_changing_headings_are_not_subtitles():
    rows = []
    for t in range(0, 120, 3):
        k = t // 15                       # a slide every 15 s, read every 3 s
        w, y = .2 + .05 * (k % 5), .42 + .02 * (k % 4)
        rows.append({"t": float(t), "text": [[.5 - w / 2, y, .5 + w / 2, y + .06]]})
    assert source_typography.subtitle_share({"spatial": {"samples": rows}}, 0., 120.) == 0.


def test_a_wide_subtitle_line_off_centre_or_on_the_edge_is_still_read():
    rows = [{"t": float(t), "text": [[.05, .90, .5 + .05 * (k % 5), 1.]]}
            for k, t in enumerate(range(0, 60, 3))]
    assert source_typography.subtitle_share({"spatial": {"samples": rows}}, 0., 60.) >= .9
