"""Photograph the cited primary source at the passage the narrator quotes."""

from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import tempfile
from contextlib import closing

from PIL import Image


def capture(fact, path):
    import net_fetch

    url, quote = fact["url"], fact["quote"].strip()
    net_fetch.check_url(url)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    parts = urlsplit(url)
    if parts.path.lower().endswith(".pdf") or parts.path.startswith("/pdf/"):
        import pypdfium2 as pdfium

        with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
            pdf = str(Path(tmp) / "original.pdf")
            net_fetch.download(url, pdf, max_bytes=50_000_000, timeout_s=45)
            with pdfium.PdfDocument(pdf) as doc:
                page_no = parse_qs(parts.fragment).get("page")
                pages = [int(page_no[0]) - 1] if page_no else range(len(doc))
                for i in pages:
                    page = doc[i]
                    text = page.get_textpage()
                    try:
                        excerpt = " ".join(quote.casefold().split())
                        if excerpt in " ".join(
                            text.get_text_range().casefold().split()
                        ):
                            with closing(text.search(quote)) as search:
                                match = search.get_next()
                            crop = (0, 0, 0, 0)
                            if match:
                                start, count = match
                                boxes = [
                                    text.get_charbox(n)
                                    for n in range(start, start + count)
                                ]
                                width, height = page.get_size()
                                # Keep the paragraph and its page context legible in the film.
                                pad = 22
                                crop = (
                                    max(0, min(b[0] for b in boxes) - pad),
                                    max(0, min(b[1] for b in boxes) - pad),
                                    max(0, width - max(b[2] for b in boxes) - pad),
                                    max(0, height - max(b[3] for b in boxes) - pad),
                                )
                            page.render(scale=3, crop=crop).to_pil().convert(
                                "RGB"
                            ).save(out)
                            break
                    finally:
                        text.close()
                        page.close()
                else:
                    raise ValueError("quoted passage absent from the cited paper")
    else:
        from playwright.sync_api import sync_playwright
        import webrecord

        parts_path = parts.path.strip("/").split("/")
        post = (
            parts.hostname in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}
            and len(parts_path) >= 3
            and parts_path[1] == "status"
            and parts_path[2].isdigit()
        )
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": 900, "height": 1050},
                    device_scale_factor=1.5,
                    service_workers="block",
                    accept_downloads=False,
                )
                context.route("**/*", webrecord._guard_router({}))
                page = context.new_page()
                page.goto(
                    "https://platform.twitter.com/embed/Tweet.html?id=" + parts_path[2]
                    if post
                    else url,
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                hit = page.get_by_text(quote, exact=False).last
                hit.wait_for(timeout=15000)
                if post:
                    # The official embed holds the author's name, avatar and timestamp.
                    article = page.locator("article").filter(has=hit).first
                    if not article.count():
                        article = hit.locator("xpath=ancestor::*[descendant::time][1]")
                    article.locator("time").first.wait_for(timeout=5000)
                    article.screenshot(path=str(out))
                else:
                    hit.scroll_into_view_if_needed()
                    hit.screenshot(path=str(out))
            finally:
                browser.close()
    with Image.open(out) as im:
        w, h = im.size
    return dict(
        path=str(out),
        w=w,
        h=h,
        source_url=url,
        attribution=" · ".join(
            str(v) for v in [fact.get("author"), fact.get("date"), parts.hostname] if v
        ),
    )
