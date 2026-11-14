from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

import lutlib


@pytest.mark.parametrize('shape', [(3,), (10000, 3), (80, 120, 3)])
def test_colour_transform_preserves_channels_for_pixel_arrays(shape):
    rng = np.random.default_rng(912)
    pixels = rng.random(shape)
    matrix = rng.normal(size=(3, 3))
    expected = np.stack([
        pixels[..., 0] * row[0] + pixels[..., 1] * row[1] + pixels[..., 2] * row[2]
        for row in matrix
    ], axis=-1)
    np.testing.assert_allclose(lutlib._colour_matrix(pixels, matrix), expected, atol=1e-12)


def test_concurrent_colour_conversions_preserve_the_image():
    pixels = np.random.default_rng(913).random((96, 128, 3))
    def convert(_):
        return lutlib.lab_to_rgb(lutlib.rgb_to_lab(pixels))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result(timeout=5) for f in [pool.submit(convert, i) for i in range(12)]]
    for actual in results:
        np.testing.assert_allclose(actual, pixels, atol=2e-6)


def test_colour_covariance_preserves_unbiased_sample_statistics():
    pixels = np.random.default_rng(914).normal(size=(120, 3))
    mean, cov = lutlib._stats(pixels)
    np.testing.assert_allclose(mean, pixels.mean(axis=0), atol=1e-12)
    np.testing.assert_allclose(cov, np.cov(pixels, rowvar=False) + np.eye(3)*1e-6, atol=1e-12)
