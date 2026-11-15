import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location("voice_envelope_test", Path(__file__).with_name("voice.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def test_opposite_gain_rides_join_continuously_and_independent_of_order():
    spans = [(0.5, 1.4, 4), (1.6, 2.6, -4)]
    g = V.envelope(4000, 1000, spans, 0.8)
    assert np.max(np.abs(np.diff(g))) < 0.02
    assert g.min() >= -4 and g.max() <= 4
    np.testing.assert_allclose(g, V.envelope(4000, 1000, spans[::-1], 0.8))


def test_single_gain_ride_keeps_its_cosine_ramps():
    g = V.envelope(4000, 1000, [(1, 2, 4)], 0.1)
    expected = np.zeros(4000)
    expected[900:2100] = 4
    ramp = 2 - 2 * np.cos(np.linspace(0, np.pi, 100))
    expected[900:1000], expected[2000:2100] = ramp, ramp[::-1]
    np.testing.assert_allclose(g, expected, atol=1e-6)
