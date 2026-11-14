from types import SimpleNamespace as NS
import word_edges


def test_quiet_inside_first_word_cannot_survive_last_placement_pass():
    words = [{'w': 'Photoshop', 't0': 1.123, 't1': 1.71}]
    keep, notes = word_edges.whole([[1.25, 4]], words, 5, [[0, 5]])
    assert keep == [[1.12, 4]]
    assert notes
    assert word_edges.refusal(NS(index={'words': words}, duration=5),
                              {'keep': [[0, 5]]}, {'keep': keep}) is None


def test_batch_and_exact_time_writes_cannot_bypass_word_check():
    ctx = NS(index={'words': [{'w': 'Nike', 't0': 2, 't1': 2.5}]}, duration=10)
    for keep in ([[2.2, 8]], [[0, 2.3]], [[2.01, 8]]):
        assert 'Nike' in word_edges.refusal(ctx, {'keep': [[0, 10]]}, {'keep': keep})


def test_unrelated_edit_does_not_move_existing_cut_or_split_camera_seam():
    words = [{'w': 'Coke', 't0': 1, 't1': 2}]
    assert not word_edges.introduced([[1.2, 4]], [[1.2, 3]], words, 5)
    assert not word_edges.introduced([[0, 5]], [[0, 1.5], [1.5, 5]], words, 5)


def test_overlapping_words_expand_until_no_word_is_clipped():
    words = [{'w': 'a', 't0': 1, 't1': 1.8}, {'w': 'b', 't0': 1.7, 't1': 2.3}]
    keep, _ = word_edges.whole([[0, 1.75]], words, 4, [[0, 4]])
    assert keep == [[0, 2.3]]
    assert word_edges.introduced([[0, 4]], keep, words, 4) == []
