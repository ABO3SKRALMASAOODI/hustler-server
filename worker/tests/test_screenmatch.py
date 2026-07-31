"""Round 65 — the filmed screen found by matching WHAT IS ON IT.

A real render shipped with the recording appearing as a flat, axis-aligned
rectangle floating over an angled laptop — because the pixel detector had
declined (it measured a portrait shelf) and the vision read returned a
plausible-but-sloppy quad with no rotation. The corners ARE the effect, and
the strongest source of them was never consulted: the content about to be
pinned, which the filmed glass is almost always displaying. These tests build
a synthetic room with the content perspective-warped onto a known rotated
quad and assert the homography recovers that quad — rotation, keystone and
all — and refuses honestly when the glass shows something else.

Run:  python -m pytest tests/test_screenmatch.py -q        (from worker/)
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

import screenmatch                                            # noqa: E402

cv2 = pytest.importorskip("cv2")

FW, FH = 960, 540           # filmed frame
CW, CH = 800, 500           # content frame

# The known screen quad in the filmed frame, in the perspective filter's
# order (TL, TR, BL, BR) — deliberately ROTATED and keystoned, because the
# whole point is recovering orientation the other detectors flatten away.
QUAD_PX = [(300.0, 150.0), (640.0, 190.0), (270.0, 360.0), (620.0, 430.0)]


def _content():
    """A UI-like content frame the way a real screen recording looks: chrome
    around a VIDEO PANEL of rich, unique texture (the panel is what anchors
    a match in practice — UI chrome alone is self-similar rows and icons)."""
    rng = np.random.default_rng(7)
    img = np.zeros((CH, CW, 3), np.uint8)
    img[:] = (28, 26, 24)
    img[0:36, :] = (50, 48, 46)                       # "menu bar"
    for x in range(30, CW - 30, 64):                  # "toolbar icons"
        cv2.rectangle(img, (x, 8), (x + 22, 28),
                      tuple(int(v) for v in rng.integers(90, 255, 3)), -1)
    # the "video panel": a smoothed noise photo — unique texture everywhere
    ph, pw = CH - 170, CW - 160
    photo = rng.integers(0, 255, (ph // 6, pw // 6, 3)).astype(np.uint8)
    photo = cv2.resize(photo, (pw, ph), interpolation=cv2.INTER_CUBIC)
    img[60:60 + ph, 80:80 + pw] = photo
    cv2.rectangle(img, (40, CH - 70), (CW - 40, CH - 30), (70, 60, 55), -1)
    for x in range(48, CW - 48, 24):                  # "timeline ticks"
        cv2.line(img, (x, CH - 66), (x, CH - 34), (140, 130, 120), 1)
    return img


def _room_with_screen(content, quad_px, dim=0.85):
    """A dark room with the content perspective-warped onto the quad."""
    rng = np.random.default_rng(3)
    room = rng.integers(8, 46, (FH, FW, 3)).astype(np.uint8)
    cv2.circle(room, (820, 120), 60, (90, 120, 160), -1)      # a "lamp"
    src = np.float32([[0, 0], [CW, 0], [0, CH], [CW, CH]])
    dst = np.float32(quad_px)
    H = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective((content * dim).astype(np.uint8), H,
                                 (FW, FH))
    mask = cv2.warpPerspective(np.full((CH, CW), 255, np.uint8), H,
                               (FW, FH))
    out = room.copy()
    out[mask > 0] = warped[mask > 0]
    noise = rng.integers(-6, 7, out.shape)
    return np.clip(out.astype(np.int16) + noise, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="round65_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def paths(workdir):
    content = _content()
    filmed = _room_with_screen(content, QUAD_PX)
    cp = os.path.join(workdir, "content.png")
    fp = os.path.join(workdir, "filmed.png")
    cv2.imwrite(cp, content)
    cv2.imwrite(fp, filmed)
    return cp, fp


def test_match_recovers_the_rotated_quad(paths):
    cp, fp = paths
    got = screenmatch.match_screen([fp], [cp])
    assert got is not None, "the content on the glass was not found"
    assert got["inliers"] >= screenmatch.MIN_INLIERS
    for i, (wx, wy) in enumerate(QUAD_PX):
        gx = got["corners"][2 * i] * FW
        gy = got["corners"][2 * i + 1] * FH
        assert abs(gx - wx) < 6 and abs(gy - wy) < 6, \
            f"corner {i}: got ({gx:.1f},{gy:.1f}) want ({wx:.1f},{wy:.1f})"
    # the recovered quad is genuinely rotated — the failure this exists to
    # prevent is a flat axis-aligned rectangle
    ys = [got["corners"][1], got["corners"][3]]
    assert abs(ys[0] - ys[1]) * FH > 20, "recovered quad lost its rotation"


def test_unrelated_content_is_an_honest_no(paths, workdir):
    _cp, fp = paths
    rng = np.random.default_rng(99)
    other = rng.integers(0, 255, (CH, CW, 3)).astype(np.uint8)
    op = os.path.join(workdir, "other.png")
    cv2.imwrite(op, other)
    assert screenmatch.match_screen([fp], [op]) is None


def test_a_glassless_room_is_an_honest_no(paths, workdir):
    cp, _fp = paths
    rng = np.random.default_rng(5)
    room = rng.integers(8, 46, (FH, FW, 3)).astype(np.uint8)
    rp = os.path.join(workdir, "plainroom.png")
    cv2.imwrite(rp, room)
    assert screenmatch.match_screen([rp], [cp]) is None


def test_agreement_counts_concurring_pairs(paths, workdir):
    cp, fp = paths
    # a second filmed frame of the same scene (fresh noise) must concur
    content = _content()
    f2 = _room_with_screen(content, QUAD_PX, dim=0.8)
    fp2 = os.path.join(workdir, "filmed2.png")
    cv2.imwrite(fp2, f2)
    got = screenmatch.match_screen([fp, fp2], [cp])
    assert got is not None
    assert got["agreement"] >= 2
    assert got["n_pairs"] == 2
