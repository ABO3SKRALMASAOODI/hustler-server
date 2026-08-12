"""Unit tests for stock.py (stock b-roll search + rendition picking).

Pure logic — the provider HTTP calls are stubbed, so this needs no API key and
makes no network requests. Run from worker/:
    python3 -m pytest tests/test_stock.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import stock                                                 # noqa: E402
import music_search                                          # noqa: E402


# ── orientation from the output frame ────────────────────────────────────

def test_orientation_for():
    assert stock.orientation_for(1920, 1080) == "landscape"
    assert stock.orientation_for(1080, 1920) == "portrait"
    assert stock.orientation_for(1080, 1080) == "square"
    assert stock.orientation_for(1080, 1350) == "portrait"   # 4:5
    # Garbage must not raise — it falls back to the default frame.
    assert stock.orientation_for(0, 0) == "landscape"
    assert stock.orientation_for(None, None) == "landscape"


# ── rendition picking: the quality decision ──────────────────────────────

def _files(*sizes):
    return [{"link": f"https://cdn/{w}.mp4", "width": w, "height": h,
             "file_type": "video/mp4"} for w, h in sizes]


def test_picks_smallest_rendition_that_covers_the_frame():
    """Not the biggest (wastes disk + minutes), not the default (upscales)."""
    files = _files((640, 360), (1280, 720), (1920, 1080), (3840, 2160))
    got = stock._pick_video_file(files, 1920, 1080)
    assert got["width"] == 1920, "should take exactly-covering, not 4K"


def test_picks_next_size_up_when_nothing_matches_exactly():
    files = _files((640, 360), (1280, 720), (2560, 1440))
    got = stock._pick_video_file(files, 1920, 1080)
    assert got["width"] == 2560


def test_falls_back_to_largest_when_nothing_covers():
    """Rare tiny sources: the closest we can get beats returning nothing."""
    files = _files((320, 180), (640, 360))
    got = stock._pick_video_file(files, 1920, 1080)
    assert got["width"] == 640


def test_ignores_non_video_and_linkless_renditions():
    files = [
        {"link": None, "width": 4096, "height": 2160, "file_type": "video/mp4"},
        {"link": "https://cdn/x.jpg", "width": 4096, "height": 2160,
         "file_type": "image/jpeg"},
        {"link": "https://cdn/ok.mp4", "width": 1920, "height": 1080,
         "file_type": "video/mp4"},
    ]
    got = stock._pick_video_file(files, 1280, 720)
    assert got["link"] == "https://cdn/ok.mp4"


def test_no_usable_rendition_returns_none():
    assert stock._pick_video_file([], 1920, 1080) is None


# ── search plumbing ──────────────────────────────────────────────────────

PEXELS_VIDEO_PAYLOAD = {
    "videos": [{
        "id": 123, "width": 3840, "height": 2160, "duration": 12,
        "alt": "aerial view of a busy city street at night",
        "url": "https://pexels.com/video/123",
        "user": {"name": "Jane Doe"},
        "video_files": [
            {"link": "https://cdn/hd.mp4", "width": 1920, "height": 1080,
             "file_type": "video/mp4"},
            {"link": "https://cdn/4k.mp4", "width": 3840, "height": 2160,
             "file_type": "video/mp4"},
        ],
    }]
}


def test_video_needs_a_keyed_library_but_photos_are_always_on(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    # Openverse's keyless photo lane keeps the capability alive with no
    # keys at all — that lane is where REAL subjects (a named person, a
    # company) come from, which pure stock libraries never carry.
    assert stock.available()
    assert not stock.video_available()
    with pytest.raises(stock.StockError) as e:
        stock.search("city")                       # kind defaults to video
    assert "find_footage" in str(e.value)          # the honest way forward


def test_search_normalises_pexels_results(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "k")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    monkeypatch.setattr(stock, "net_fetch",
                        type("N", (), {"get_json": staticmethod(
                            lambda *a, **k: PEXELS_VIDEO_PAYLOAD)}))
    hits = stock.search("busy city", kind="video", orientation="landscape")
    assert len(hits) == 1
    h = hits[0]
    assert h["id"] == "pexels:video:123"
    assert h["provider"] == "pexels"
    assert h["credit"] == "Jane Doe"
    assert h["duration_s"] == 12
    # resolve() must pick the 1080p file for a 1080p frame, not the 4K one.
    url, cap = stock.resolve(h, 1920, 1080)
    assert url == "https://cdn/hd.mp4"
    assert cap == stock.MAX_VIDEO_BYTES


def test_search_falls_through_to_pixabay_when_pexels_errors(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "k")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "k2")

    def boom(url, **kw):
        if "pexels" in url:
            raise RuntimeError("pexels down")
        return {"hits": [{
            "id": 7, "duration": 9, "tags": "ocean, waves",
            "user": "Bob", "pageURL": "https://pixabay.com/v/7",
            "videos": {"large": {"url": "https://cdn/l.mp4",
                                 "width": 1920, "height": 1080}},
        }]}

    monkeypatch.setattr(stock, "net_fetch",
                        type("N", (), {"get_json": staticmethod(boom)}))
    hits = stock.search("ocean", kind="video")
    assert len(hits) == 1
    assert hits[0]["provider"] == "pixabay"
    assert hits[0]["id"] == "pixabay:video:7"


def test_search_raises_only_when_every_provider_fails(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "k")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")

    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(stock, "net_fetch",
                        type("N", (), {"get_json": staticmethod(boom)}))
    with pytest.raises(stock.StockError):
        stock.search("anything")


def test_empty_results_are_not_an_error(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "k")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    monkeypatch.setattr(stock, "net_fetch",
                        type("N", (), {"get_json": staticmethod(
                            lambda *a, **k: {"videos": []})}))
    assert stock.search("zzzzz") == []


def test_summarize_is_one_line_per_hit(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "k")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    monkeypatch.setattr(stock, "net_fetch",
                        type("N", (), {"get_json": staticmethod(
                            lambda *a, **k: PEXELS_VIDEO_PAYLOAD)}))
    out = stock.summarize(stock.search("city"))
    assert out.count("\n") == 0
    assert "pexels:video:123" in out
    assert "by Jane Doe" in out


# ── the Openverse topical-photo lane (2026-08-08) ────────────────────────

_OPENVERSE_PAGE = {
    "results": [
        {"id": "abc-123", "title": "Elon Musk at TED",
         "creator": "Photog", "license": "by", "license_version": "2.0",
         "url": "https://upload.wikimedia.org/musk.jpg",
         "foreign_landing_url": "https://commons.wikimedia.org/x",
         "width": 2000, "height": 3000, "source": "wikimedia"},
        {"id": "nd-1", "title": "No derivatives", "creator": "X",
         "license": "by-nc-nd", "url": "https://y/z.jpg",
         "width": 100, "height": 100, "source": "flickr"},
    ]
}


def test_openverse_photos_carry_their_license_line(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    seen = {}

    def fake(url, params=None, **kw):
        seen.update(params or {})
        return _OPENVERSE_PAGE
    # Openverse auth/retry is shared by music, SFX and photos; stub that
    # shared request boundary rather than the unrelated Pexels/Pixabay client.
    monkeypatch.setattr(music_search, "_openverse_get_json", fake)
    hits = stock.search("elon musk", kind=stock.KIND_PHOTO,
                        orientation="portrait")
    # ND is unusable in an edit and filtered; the BY hit survives with the
    # obligation stated in the line the agent reads.
    assert [h["id"] for h in hits] == ["openverse:photo:abc-123"]
    assert "credit Photog" in hits[0]["license_note"]
    assert "credit Photog" in stock.summarize(hits)
    assert "wikimedia" in hits[0]["description"]
    # Orientation rode the request as Openverse's aspect_ratio vocabulary.
    assert seen.get("aspect_ratio") == "tall"
    assert seen.get("license_type") == "modification"
