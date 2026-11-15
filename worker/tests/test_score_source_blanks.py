"""Source black must reach the editor as a defect, never as extra coverage."""
import numpy as np
import pytest

from score import picture_cuts


def test_blank_intervals_exclude_a_dark_subject_and_one_frame_flash():
    frames = np.full((60, 24, 24, 3), 80, np.uint8)
    frames[8:12] = 0
    frames[20] = 0
    frames[30:40] = 5
    frames[30:40, 6:18, 6:18] = 35  # dim subject is still a picture
    frames[50:53] = 8  # compressed black need not equal zero
    assert picture_cuts.blank_intervals(frames, 15) == [
        [round(8 / 15, 4), .8], [round(50 / 15, 4), round(53 / 15, 4)]]


def test_reordered_source_blanks_report_their_actual_programme_clock():
    l1 = {'fps': 30, 'items': [
        {'id': 'hook', 'k': 'shot', 'pic': 'main', 'f': [0, 24], 'src': [6.64, 7.44]},
        {'id': 'cover', 'k': 'shot', 'pic': 'checked_clip', 'f': [24, 48], 'src': [7., 7.8]},
        {'id': 'clear', 'k': 'shot', 'pic': 'main', 'f': [48, 72], 'src': [1., 1.8]}]}
    index = {'director_cut_measure': {'blank_intervals': [[7.2, 7.4], [8.2, 8.4]]}}
    result = picture_cuts.selected_blanks(l1, index)
    assert len(result) == 1 and result[0]['id'] == 'hook'
    assert result[0]['source'] == [7.2, 7.4]
    assert result[0]['programme'] == pytest.approx([.56, .76])
    assert picture_cuts.selected_blanks(l1, {}) == []


def test_brief_exposes_black_spans_without_calling_them_new_shots():
    from score.brief import footage_part
    per = {'W': 1080, 'H': 1920, 'dur': 9., 'fps': 30, 'shots': [],
           'samples': [], 'step': .2}
    text = '\n'.join(footage_part(per, {
        'director_cut_measure': {'blank_intervals': [[7.2, 7.4]]}}))
    assert '7.200-7.400' in text and 'not extra shots' in text


def test_blank_facts_survive_local_refinement_without_editing_shared_index(monkeypatch):
    index = {'video': {'duration': 9.}, 'shots': []}
    monkeypatch.setattr(picture_cuts, 'measure', lambda *a: {
        'cuts': [7.2, 7.4], 'blank_intervals': [[7.2, 7.4]]})
    result = picture_cuts.refine(index, 'unused')
    assert result['director_cut_measure']['blank_intervals'] == [[7.2, 7.4]]
    assert 'director_cut_measure' not in index


def test_compiled_black_selection_reaches_the_existing_repair_round():
    import json
    from score import compiler, direct
    from tests.test_score_compiler import wordless_index
    index = wordless_index()
    index['director_cut_measure'] = {'blank_intervals': [[7.2, 7.4]]}
    text = '\n'.join(json.dumps(row) for row in [
        {'k': 'film', 'fps': 30, 'w': 1080, 'h': 1920},
        {'k': 'b', 'id': 'hook', 'at': '6.64..7.44', 'pic': 'main'}])
    result = compiler.compile_score(text, index=index, govern=False)
    assert result.meter['selected_source_blanks']
    ask = direct.repair_ask(result, [], [], None)
    assert 'blank source:' in ask and '7.200-7.400' in ask
