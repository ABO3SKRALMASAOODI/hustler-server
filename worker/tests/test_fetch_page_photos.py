"""A linked web page (a news article) yields its own photos (ticket 1015,
11 Nov 2026): the customer asked for the article's photos, yt-dlp's generic
extractor met a 403 on the page, and the request ended with nothing."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import url_media  # noqa: E402

PAGE = "https://news.example.com/estado/2026/11/7/detenidos-536442.html"
ARTICLE = """<!doctype html><html><head>
<meta property="og:image" content="https://assets.example.com/social/536442.jpg"/>
<meta name="twitter:image" content="https://assets.example.com/social/536442.jpg"/>
<link rel="icon" href="/favicon.ico">
</head><body>
<img src="/img/logo.svg" width="120">
<img src="/img/pixel.gif" width="1" height="1">
<img data-src="/fotos/clinica-cordoba.jpg" width="900">
<picture><source srcset="/fotos/orizaba-480.jpg 480w, /fotos/orizaba-1200.jpg 1200w"></picture>
<img src="data:image/png;base64,AAAA">
</body></html>"""


def _jpeg(path):
    from PIL import Image
    Image.new("RGB", (64, 48), (120, 30, 30)).save(path, "JPEG")


def test_page_images_reads_lead_and_article_photos(tmp_path):
    html = tmp_path / "p.html"
    html.write_text(ARTICLE)
    got = url_media.page_images(PAGE, html_path=str(html))
    assert got[0] == "https://assets.example.com/social/536442.jpg"
    assert "https://news.example.com/fotos/clinica-cordoba.jpg" in got
    assert "https://news.example.com/fotos/orizaba-1200.jpg" in got
    assert not any(u.endswith((".svg", ".gif", ".ico")) for u in got)
    assert not any(u.startswith("data:") for u in got)
    assert len(got) == len(set(got))


def _wire(monkeypatch, tmp_path, *, extractor_error):
    """Production's shape of the failure: the page is HTML, yt-dlp fails."""
    monkeypatch.setattr(url_media.net_fetch, "check_url", lambda u, *a: None)
    monkeypatch.setattr(url_media.net_fetch, "head_kind",
                        lambda u, **k: ("text/html", None))
    calls = {"extract": 0}

    def fake_extract(url, workdir, prefer=None):
        calls["extract"] += 1
        raise url_media.FetchMediaError(extractor_error)

    def fake_download(url, out, **k):
        if url == PAGE:
            with open(out, "w") as fh:
                fh.write(ARTICLE)
        else:
            _jpeg(out)
        return None, url

    monkeypatch.setattr(url_media, "_extract_with_fallback", fake_extract)
    monkeypatch.setattr(url_media.net_fetch, "download", fake_download)
    return calls


FORBIDDEN = ("[generic] detenidos-536442: Unable to download webpage: "
             "HTTP Error 403: Forbidden")


def test_photo_asked_of_an_article_downloads_its_lead_photo(monkeypatch,
                                                            tmp_path):
    calls = _wire(monkeypatch, tmp_path, extractor_error=FORBIDDEN)
    got = url_media.fetch(PAGE, str(tmp_path), prefer=url_media.KIND_IMAGE)
    assert got["kind"] == url_media.KIND_IMAGE
    assert got["extractor"] == "page_image"
    assert got["page_url"] == PAGE
    assert "https://news.example.com/fotos/clinica-cordoba.jpg" in \
        got["page_images"]
    # The page's photos come first; no video extractor is run for a photo.
    assert calls["extract"] == 0


def test_unhinted_link_to_an_article_falls_back_to_its_photo(monkeypatch,
                                                             tmp_path):
    calls = _wire(monkeypatch, tmp_path, extractor_error=FORBIDDEN)
    got = url_media.fetch(PAGE, str(tmp_path))
    assert calls["extract"] == 1
    assert got["kind"] == url_media.KIND_IMAGE


def test_clip_asked_of_an_article_is_not_answered_with_a_photo(monkeypatch,
                                                               tmp_path):
    _wire(monkeypatch, tmp_path, extractor_error=FORBIDDEN)
    with pytest.raises(url_media.FetchMediaError, match="403"):
        url_media.fetch(PAGE, str(tmp_path), prefer=url_media.KIND_VIDEO)

