from types import SimpleNamespace as NS
import word_edges


def test_quiet_inside_first_word_cannot_survive_last_placement_pass():
    words = [{'w': 'Photoshop', 't0': 1.123, 't1': 1.71}]
    keep, notes = word_edges.whole([[1.25, 4]], words, 5, [[0, 5]])
    assert keep == [[1.123, 4]]
    assert notes and "0.127 s earlier" in notes[0]
    assert word_edges.introduced([[0, 5]], keep, words, 5) == []


def test_batch_and_exact_time_edges_inside_a_word_move_to_its_nearest_edge():
    words = [{'w': 'Nike', 't0': 2, 't1': 2.5}]
    for keep, want in (([[2.2, 8]], [[2, 8]]), ([[0, 2.3]], [[0, 2.5]]),
                       ([[2.01, 8]], [[2, 8]]), ([[2.4, 8]], [[2.5, 8]]),
                       ([[0, 2.1]], [[0, 2]])):
        fixed, moves = word_edges.nearest(keep, words, 10, [[0, 10]])
        assert fixed == want and moves and moves[0]['word'] == 'Nike'
        assert word_edges.introduced([[0, 10]], fixed, words, 10) == []
        line = word_edges.move_lines(moves)[0]
        assert 'Nike' in line and ' s ' in line


def _write_ctx(words, duration=4.):
    from schemas import default_edl
    from w0c_support import tool_context
    ctx, fake = tool_context(default_edl(duration), duration=duration)
    ctx.index['words'] = words
    ctx.cut_sound_source = lambda: 'actual-source.wav'
    return ctx, fake


def test_shared_write_moves_a_measured_clipped_onset_to_the_pause_and_saves(monkeypatch):
    import cut_audio
    from schemas import default_edl
    words = [{'w': 'story', 't0': 1.63, 't1': 1.87},
             {'w': 'but', 't0': 1.87, 't1': 1.95},
             {'w': 'Galaxy', 't0': 1.95, 't1': 2.27},
             {'w': 'Quest', 't0': 2.27, 't1': 2.59}]
    monkeypatch.setattr(cut_audio, 'source_levels', lambda *a, **k:
        lambda t: -37. if 1.79 <= round(t, 2) <= 1.86 else (-14. if t > 1.86 else -48.))
    ctx, _fake = _write_ctx(words)
    result = ctx.write_edl(dict(default_edl(4.), keep=[[1.95, 4.]]), 'Trim')
    assert result.startswith('EDL v'), result
    assert 'CUT EDGES REPAIRED' in result and 'Galaxy' in result and 'earlier' in result
    start = ctx.latest_edl()['json']['keep'][0][0]
    assert 1.79 <= start < 1.95


def test_shared_write_keeps_the_release_after_an_early_asr_word_end(monkeypatch):
    import cut_audio
    from schemas import default_edl
    # The existing acoustic regression: Whisper ends "fonts" before its /s/.
    words = [{'w': 'fonts,', 't0': .7, 't1': 1.30},
             {'w': 'and', 't0': 1.8, 't1': 2.1}]
    monkeypatch.setattr(cut_audio, 'source_levels', lambda *a, **k:
        lambda t: -13. if .7 <= t < 1.45 else -60.)
    ctx, _fake = _write_ctx(words)
    result = ctx.write_edl(dict(default_edl(4.), keep=[[0., 1.30]]), 'Trim')
    assert result.startswith('EDL v'), result
    assert 'fonts' in result and 'later' in result
    end = ctx.latest_edl()['json']['keep'][0][1]
    assert 1.30 < end < 1.8


def test_release_guard_does_not_move_existing_seams_or_swallow_the_next_word():
    import cut_audio
    words = [{'w': 'fonts', 't0': .7, 't1': 1.3},
             {'w': 'and', 't0': 1.31, 't1': 1.6}]
    level = lambda t: -13. if .7 <= t < 1.45 else -60.
    assert not cut_audio.clipped_releases([[0, 1.3]], words, 4., level)
    assert not cut_audio.clipped_releases([[0, 1.3], [1.3, 4]], words, 4., level)
    assert not cut_audio.clipped_releases([[0, 1.3]], words[:1], 4., level, [[0, 1.3]])
    assert not cut_audio.clipped_releases([[0, 1.3]], words[:1], 1.3, level)
    assert not cut_audio.clipped_releases([[0, 1.3]], words[:1], 4., lambda t: -13.)


def test_unrelated_edit_does_not_move_existing_cut_or_split_camera_seam():
    words = [{'w': 'Coke', 't0': 1, 't1': 2}]
    assert not word_edges.introduced([[1.2, 4]], [[1.2, 3]], words, 5)
    assert not word_edges.introduced([[0, 5]], [[0, 1.5], [1.5, 5]], words, 5)


def test_overlapping_words_expand_until_no_word_is_clipped():
    words = [{'w': 'a', 't0': 1, 't1': 1.8}, {'w': 'b', 't0': 1.7, 't1': 2.3}]
    keep, _ = word_edges.whole([[0, 1.75]], words, 4, [[0, 4]])
    assert keep == [[0, 2.3]]
    assert word_edges.introduced([[0, 4]], keep, words, 4) == []


def test_late_boundary_cannot_discard_the_first_word_during_acoustic_placement(monkeypatch):
    import agent_tools
    import cut_audio
    from schemas import default_edl
    words = [{'w': 'Photoshop', 't0': 1., 't1': 2.}]
    saved = []
    ctx = NS(index={'words': words}, duration=4.,
             latest_edl=lambda: {'version': 1, 'json': default_edl(4)},
             write_edl=lambda edl, desc: (saved.append(edl) or 'EDL v2'))
    def audio(ctx, keep, prev, words):
        placed, moves, checked = cut_audio.refine_keep(keep, words, 4., prev,
                                  lambda t: -15. if 1 <= t <= 2 else -62.)
        return placed, cut_audio.report(moves, checked), set()
    monkeypatch.setattr(agent_tools, '_audio_safe_keep', audio)
    agent_tools._write_keep(ctx, [[1.8, 4]], 'Trim lead-in')
    assert saved and saved[0]['keep'][0][0] <= 1.


def test_touching_words_have_a_representable_exact_cut():
    from schemas import default_edl, validate_edl
    from read_tools import clamp_seconds
    words = [{'w': 'list,', 't0': 15.855, 't1': 16.415},
             {'w': 'and', 't0': 16.415, 't1': 16.655}]
    end = clamp_seconds(29.27, 16.415)
    saved = validate_edl(dict(default_edl(29.27), keep=[[0, end]]), 29.27).model_dump()
    assert saved['keep'] == [[0, 16.415]]
    assert not word_edges.introduced([[0, 29.27]], saved['keep'], words, 29.27)


def test_outward_word_snap_does_not_consume_the_next_touching_word():
    words = [{'w': 'list,', 't0': 15.855, 't1': 16.415},
             {'w': 'and', 't0': 16.415, 't1': 16.655}]
    keep, _ = word_edges.whole([[0, 16.3]], words, 29.27, [[0, 29.27]], pad=True)
    assert keep == [[0, 16.415]]


def test_exact_word_boundary_reaches_audio_trim():
    from schemas import validate_edl
    from timeline import Timeline
    from renderer import build_filtergraph
    edl = validate_edl({'keep': [[0, 16.415]]}, 29.27).model_dump()
    graph = build_filtergraph(edl, 29.27, True, Timeline(edl['keep']), None, [],
                              {'video': {'duration': 29.27}}, preview=True)
    assert 'atrim=start=0.000:end=16.415,' in graph
