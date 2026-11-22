"""Exercise the unregistered proof builders and their signal-dependent geometry."""

from importlib import import_module

import numpy as np
import pytest
from scene import alien
from scene.alien.studies import (
    double_signature,
    human_orbit,
    material,
    phrase_register,
    room_between,
)
from scene.plan import eligible


def test_two_readings_make_two_signatures():
    t = np.arange(8000) / 8000
    first = np.sin(2 * np.pi * 130 * t)
    second = np.sin(2 * np.pi * 215 * t) * (0.2 + 0.8 * t)
    a, p = double_signature.signature(first, 640, 150)
    b, q = double_signature.signature(second, 640, 150)
    assert np.ptp(a[:, 0]) > 600
    assert np.mean(np.abs(a[:, 1] - b[:, 1])) > 15
    assert q[7] < q[-8] and p.min() >= 0


def test_quiet_window_follows_room_tone():
    level = np.ones(100)
    level[12:16] = 0
    level[35:61] = 0.012
    assert room_between.quiet_gap(level, 8, 78) == (35, 61)
    assert room_between.quiet_gap(np.roll(level, 5), 8, 78) == (40, 66)


def test_eyes_register_across_scales_without_changing_original():
    fr = np.zeros((160, 240, 3), np.uint8)
    fr[66:73, 56:63] = 255
    target = fr.copy()
    ms = [(np.array([59.0, 69.0]), 20, None), (np.array([130.0, 70.0]), 30, None)]
    warped = phrase_register.register([fr, target], ms)
    assert np.all(warped[0][70, 130] == 255)
    assert np.all(fr[70, 130] == 0)
    assert np.array_equal(warped[1], target)


@pytest.mark.parametrize(
    "name",
    ["double_signature", "room_between", "moat_cast", "phrase_register", "human_orbit", "short_century"],
)
def test_builder_emits_matching_span_and_changes_the_take(name, monkeypatch, tmp_path):
    w, h, fps, n = 240, 136, 12, 84
    rng = np.random.default_rng(452)
    fr = rng.integers(30, 180, (h, w, 3), np.uint8)
    src = {"k": "shot", "id": "take", "pic": "main", "f": [0, n], "src": [0, n / fps]}
    doc = {
        "l1": 1,
        "fps": fps,
        "dur_f": n,
        "frame": [w, h],
        "items": [src],
        "assets": {"main": {"path": "take.mp4", "kind": "video"}},
    }
    beats = {
        "double_signature": [18, 36],
        "room_between": [19, 42],
        "moat_cast": [17, 27, 39],
        "phrase_register": [15, 26, 41],
        "human_orbit": [12, 26, 40, 55],
        "short_century": [5, 18, 38, 56],
    }
    ask = {
        "id": name,
        "beats": beats[name],
        "ends": [28, 46],
        "models": str(tmp_path),
        "years": [2005, 2105, 2012],
        "line": "Again.",
    }
    monkeypatch.setattr(material, "take_frames", lambda l, work, a, z: (fr.copy() for _ in range(z - a)))
    monkeypatch.setattr(material, "moments", lambda l, work, fs: [np.roll(fr, f % 9, axis=0) for f in fs])
    monkeypatch.setattr(
        material, "face", lambda rgb, model: (np.array([120.0, 47.0]), 28, np.array([92, 22, 56, 68]))
    )
    monkeypatch.setattr(material.sense, "level", lambda *a: np.r_[np.ones(21), np.zeros(18), np.ones(n - 39)])
    monkeypatch.setattr(material.sense, "_band", lambda *a: np.sin(np.arange(4000) * 0.16).astype(np.float32))

    def hands(doc, work, a, z, model):
        track = np.tile([80.0, 85.0, 160.0, 76.0], (z - a, 1))
        track[:, 2] += np.linspace(0, 34, z - a)
        return track, z - a

    monkeypatch.setattr(human_orbit, "hand_track", hands)

    if name == "room_between":
        # A second random seek at the exact boundary can return the incoming
        # frame twice. Measure both sides in one decode, including the outgoing face.
        cut = beats[name][0]
        incoming = np.subtract(255, fr)

        def reverse_angle(l1, work, start, end):
            return (fr if frame < cut else incoming for frame in range(start, end))

        monkeypatch.setattr(material, "take_frames", reverse_angle)

    def examine(l, work, request, a, z, pictures, log, items=(), voices=None):
        diffs = [float(np.abs(frame.astype(float) - fr).mean()) for frame in pictures]
        assert len(diffs) == z - a and max(diffs) > 2
        if name == "room_between":
            assert log["reverse_cut"] == cut
            assert log["mean_reverse_delta"] > 40
        kept, gone, _ = material.sense.carve(l, a, z)
        return {
            "items": kept + [dict(src, id=request["id"] + ".exposure", f=[a, z]), *items],
            "drop": gone,
            "voices": voices or {},
        }

    monkeypatch.setattr(material, "plate", examine)
    patch = import_module("scene.alien.studies." + name).plan(doc, str(tmp_path), ask)
    assert eligible(alien.merge(doc, patch)) == (True, [])
    assert doc["items"] == [src]
