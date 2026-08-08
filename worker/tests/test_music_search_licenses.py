"""Round 98.2 — the license is a label, not a wall.

The bundled library is deleted and search is the music surface, so what
search may SHOW decides what the product can do. The old filters silently
dropped every non-commercial track — meaning a hobbyist scoring a personal
video lost tracks they could lawfully use, and never knew. Now the terms
travel with the hit and the sentence the agent reads says them outright;
the only family still refused is no-derivatives, because syncing music in
timed relation with picture is itself an adaptation — an ND track cannot
be used in ANY edit, so offering one would only manufacture a violation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import music_search                                            # noqa: E402


def test_nc_is_allowed_and_labeled_loudly():
    assert music_search._license_ok("by-nc")
    assert music_search._license_ok(
        "https://creativecommons.org/licenses/by-nc-sa/3.0/")
    note = music_search._license_note("by-nc", "Artist A")
    assert "NON-COMMERCIAL" in note
    assert "Artist A" in note          # the credit obligation rides along


def test_nd_is_still_refused():
    # by-nc-nd and by-nd both carry no-derivatives.
    assert not music_search._license_ok("by-nd")
    assert not music_search._license_ok(
        "https://creativecommons.org/licenses/by-nc-nd/4.0/")


def test_open_licenses_read_as_before():
    assert "public domain" in music_search._license_note("cc0-1.0", None)
    by = music_search._license_note("by-4.0", "Artist B")
    assert "commercial use" in by and "Artist B" in by
    assert "NON-COMMERCIAL" not in by


def test_jamendo_positive_gate_admits_nc_ccurls():
    # The catalog lane's sanity check must accept the licenses the policy
    # now allows — a stale gate here would silently reintroduce the filter.
    assert music_search._CC_LICENSE.search(
        "https://creativecommons.org/licenses/by-nc-sa/3.0/")
    assert music_search._CC_LICENSE.search(
        "https://creativecommons.org/licenses/by/3.0/")


def test_openverse_is_not_commercial_gated(monkeypatch):
    seen = {}

    def fake_get_json(url, params=None, **kw):
        seen.update(params or {})
        return {"results": []}

    monkeypatch.setattr(music_search.net_fetch, "get_json", fake_get_json)
    music_search._openverse_search("lofi", None, None, 5)
    # modification (no ND) is required; commercial must NOT be.
    assert seen.get("license_type") == "modification"
