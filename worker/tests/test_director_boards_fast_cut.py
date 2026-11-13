"""Ticket #922: the Director's prep fell on a fast-cut source.

A 91 s 4K gameplay upload (project 5569) was indexed into 34 shots; the frame
boards hold 27 cells (3 boards of 3x3 for 16:9). `pick_times` gave every shot
at least two cells, then shaved the largest counts until they fit, pushing
counts to zero and dividing by them: "ZeroDivisionError: float division by
zero" at stage prep, and the customer got the chain's cut instead of the film.
"""
import pytest

from score import brief


def _per(n_shots, dur, events=()):
    step = dur / n_shots
    shots = [dict(t=[round(i * step, 2), round((i + 1) * step, 2)]) for i in range(n_shots)]
    return dict(dur=dur, shots=shots, events=list(events))


@pytest.mark.parametrize("n_shots,n", [
    (34, 27),    # the ticket: project 5569, 34 shots, 3 boards of 9
    (14, 27),    # one more than the two-per-shot floor fits
    (27, 27),    # one cell each
    (120, 36),   # a montage far denser than the boards
    (3, 27),     # a calm source: two-per-shot floor holds
])
def test_pick_times_fills_every_cell_whatever_the_shot_count(n_shots, n):
    per = _per(n_shots, 91.2, events=[dict(t=10.0), dict(t=[20.0, 22.0])])
    times = brief.pick_times(per, n)
    assert len(times) == n
    assert times == sorted(times)
    assert all(0.0 <= t <= per["dur"] for t in times)


def test_pick_times_keeps_every_shot_while_the_boards_have_room():
    per = _per(13, 60.0)
    times = brief.pick_times(per, 27)
    for a, b in (sh["t"] for sh in per["shots"]):
        assert sum(1 for t in times if a <= t <= b) >= 2


def test_pick_times_with_uneven_fast_cuts_shares_cells_by_length():
    # Many 0.3 s flashes and two long takes, more shots than cells.
    shots = [dict(t=[i * 0.3, (i + 1) * 0.3]) for i in range(40)]
    shots += [dict(t=[12.0, 40.0]), dict(t=[40.0, 70.0])]
    per = dict(dur=70.0, shots=shots, events=[])
    times = brief.pick_times(per, 27)
    assert len(times) == 27
    assert sum(1 for t in times if t >= 12.0) >= 20


def test_pick_times_without_shots_spreads_over_the_film():
    times = brief.pick_times(dict(dur=30.0, shots=[], events=[]), 9)
    assert len(times) == 9 and times[0] < 1.0 and times[-1] > 29.0
