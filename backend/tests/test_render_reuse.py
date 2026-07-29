"""Two EDL versions that render the same video share one encode.

Every manual cut in the studio is split-then-delete, and the split's render is
frame-for-frame the render it replaces — [[0, 354.6]] split at 18.54 is still
[[0, 354.6]] once the renderer concatenates it. On project 246 that cost three
full 4K-sourced encodes in four minutes (36s, 32s, 39s of pure ffmpeg) for
pictures already on the user's screen, and the no-op's asset id sometimes
landed HIGHER than the real cut's, so the studio attached the version where the
cut had not happened.

    cd backend && python -m pytest tests/test_render_reuse.py -q
"""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402


def edl(keep, **rest):
    base = {"keep": keep, "inserts": [], "speed": [], "effects": {}}
    base.update(rest)
    return base


# ── the signature itself ────────────────────────────────────────────────────

def test_split_renders_the_same_programme():
    assert video._program_signature(edl([[0.0, 354.61]])) == \
           video._program_signature(edl([[0.0, 18.54], [18.54, 354.61]]))


def test_split_three_ways_still_the_same_programme():
    assert video._program_signature(edl([[0.0, 100.0]])) == \
           video._program_signature(
               edl([[0.0, 20.0], [20.0, 55.5], [55.5, 100.0]]))


def test_deleting_a_segment_is_a_different_programme():
    assert video._program_signature(edl([[0.0, 354.61]])) != \
           video._program_signature(edl([[18.54, 354.61]]))


def test_a_gap_is_not_contiguous():
    """The join must be real. [[0,10],[10.5,20]] drops half a second and is a
    different video from [[0,20]] — merging on a loose epsilon would hand the
    user a render that silently keeps footage they cut."""
    assert video._program_signature(edl([[0.0, 20.0]])) != \
           video._program_signature(edl([[0.0, 10.0], [10.5, 20.0]]))


def test_a_transition_makes_the_split_visible():
    """`timeline.transition_junctions` counts one junction per keep boundary,
    so with a transition configured a split genuinely adds an effect to the
    render. Equivalence must not be claimed there."""
    fx = {"transition": {"kind": "dip_to_black", "scope": "every_cut"}}
    assert video._program_signature(edl([[0.0, 100.0]], effects=fx)) != \
           video._program_signature(
               edl([[0.0, 40.0], [40.0, 100.0]], effects=fx))


def test_everything_else_still_separates_versions():
    a = edl([[0.0, 100.0]])
    for change in ({"effects": {"grade": "cinematic"}},
                   {"inserts": [{"id": "i1", "at_output_s": 3.0,
                                 "storage_key": "k", "duration_s": 2.0}]},
                   {"captions": {"style": {"preset": "bold"}}},
                   {"frame": {"ratio": "9:16", "mode": "crop"}}):
        b = edl([[0.0, 100.0]], **change)
        assert video._program_signature(a) != video._program_signature(b), \
            f"{change} was treated as the same render"


def test_malformed_keep_never_claims_equivalence():
    assert video._program_signature({"keep": [["x", "y"]]}) is None
    assert video._program_signature({"keep": [[1.0]]}) is None


def test_no_signature_means_no_reuse():
    """A None signature must not match another None. Two EDLs we cannot read
    are not two EDLs we know are identical."""
    cur = _Cur(renders=[(1, 4, "preview")], edls={4: {"keep": [["x", "y"]]}})
    assert video._preview_twin(cur, 1, {"keep": [["x", "y"]]}) is None


# ── finding the twin ────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, renders, edls):
        # renders: [(asset_id, edl_version, variant)]
        self.renders = renders
        self.edls = edls
        self._rows, self._one = [], None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = params or ()
        if "FROM assets" in s and "kind = 'render'" in s:
            self._rows = [{"id": i, "meta": {"edl_version": v,
                                             "variant": var}}
                          for i, v, var in sorted(self.renders, reverse=True)]
        elif "FROM edls" in s and "version = ANY" in s:
            self._rows = [{"version": v, "json": self.edls[v]}
                          for v in p[1] if v in self.edls]
        else:                                   # pragma: no cover
            raise AssertionError(f"unexpected query: {s[:100]}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


def test_twin_found_for_a_split():
    cur = _Cur(renders=[(50, 1, "preview")],
               edls={1: edl([[0.0, 354.61]])})
    got = video._preview_twin(cur, 1, edl([[0.0, 18.54], [18.54, 354.61]]))
    assert got == 50


def test_no_twin_for_a_real_cut():
    cur = _Cur(renders=[(50, 1, "preview")],
               edls={1: edl([[0.0, 354.61]])})
    assert video._preview_twin(cur, 1, edl([[18.54, 354.61]])) is None


def test_a_final_render_is_never_adopted_as_a_preview():
    """Finals carry the end card and (on free plans) the watermark. Handing one
    back as a preview would show the user an export they did not ask for."""
    cur = _Cur(renders=[(50, 1, "final")], edls={1: edl([[0.0, 100.0]])})
    assert video._preview_twin(cur, 1, edl([[0.0, 100.0]])) is None


def test_newest_render_of_a_version_wins():
    """A version can be re-rendered (the forced-rebuild path). Adopting the
    dead first encode is exactly the asset the user told us will not play."""
    cur = _Cur(renders=[(50, 1, "preview"), (77, 1, "preview")],
               edls={1: edl([[0.0, 100.0]])})
    assert video._preview_twin(cur, 1, edl([[0.0, 100.0]])) == 77


def test_the_version_being_written_is_excluded():
    """Otherwise a re-render request for v3 adopts v3's own existing asset and
    the user's Retry changes nothing — the dead end this codebase already
    learned about once."""
    cur = _Cur(renders=[(50, 3, "preview")], edls={3: edl([[0.0, 100.0]])})
    assert video._preview_twin(cur, 1, edl([[0.0, 100.0]]),
                               exclude_version=3) is None


def test_undo_back_to_a_rendered_version_reuses_it():
    """v1 full, v2 head trimmed, v3 back to full: v3 adopts v1's encode."""
    cur = _Cur(renders=[(50, 1, "preview"), (61, 2, "preview")],
               edls={1: edl([[0.0, 100.0]]), 2: edl([[18.0, 100.0]])})
    assert video._preview_twin(cur, 1, edl([[0.0, 100.0]]),
                               exclude_version=3) == 50


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
