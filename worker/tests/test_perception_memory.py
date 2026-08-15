"""Regression checks for the Render audio-perception OOM (Aug 14 2026)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import perception                                             # noqa: E402


def test_fft_batch_stays_inside_the_dispatcher_memory_budget():
    # 2048 frames plus the old n x N_FFT int64 index matrix produced a
    # ~132-MiB transient RSS jump on the 512-MiB Render worker.
    assert perception.CHUNK_FRAMES <= 512
    assert perception.CHUNK_SAMPLES == (
        perception.HOP * perception.CHUNK_FRAMES)


def test_strided_frame_view_matches_the_old_sampling_geometry():
    n = 7
    samples = perception.N_FFT + perception.HOP * (n - 1)
    buf = np.arange(samples, dtype=np.float32)
    view = np.lib.stride_tricks.sliding_window_view(
        buf, perception.N_FFT)[::perception.HOP][:n]

    assert view.shape == (n, perception.N_FFT)
    assert np.shares_memory(view, buf)
    assert np.array_equal(view[:, 0],
                          np.arange(n, dtype=np.float32) * perception.HOP)
