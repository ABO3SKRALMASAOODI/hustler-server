"""Small NumPy/OpenCV equivalents of the fixed meter's SciPy operations.

The worker image already ships these two libraries. Nearest-edge filtering
and four-connected labelling preserve the meter's signal thresholds.
"""
import cv2
import numpy as np


def _windows(a, size, mode):
    n = int(size)
    pad = (n // 2, (n - 1) // 2)
    return np.lib.stride_tricks.sliding_window_view(
        np.pad(a, pad, mode='edge' if mode == 'nearest' else 'symmetric'), n)


def median_filter(a, size, mode='reflect'):
    w = _windows(a, size, mode)
    return np.partition(w, int(size) // 2, axis=-1)[:, int(size) // 2]


def uniform_filter1d(a, size, mode='reflect'):
    return _windows(a, size, mode).mean(axis=-1).astype(np.asarray(a).dtype)


def maximum_filter1d(a, size, mode='reflect'):
    return _windows(a, size, mode).max(axis=-1)


def label(a):
    n, labels = cv2.connectedComponents(np.asarray(a, np.uint8), connectivity=4)
    return labels, n - 1


def next_fast_len(n, real=False):
    return 1 << (int(n) - 1).bit_length()


rfft = np.fft.rfft
irfft = np.fft.irfft
