from playwright.sync_api import sync_playwright
import webrecord


def test_sentence_in_a_paragraph_retains_exact_words_and_publisher_type(tmp_path):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content('<p style="font:20px Georgia">Earlier financing. Capital is <b>expected</b> to provide liquidity. Later forecast.</p>')
        hit = page.locator("p")
        original = hit.inner_html()
        words = "Capital is expected to provide liquidity."
        shot = webrecord.passage_image(hit, words)
        assert shot.startswith(b"\x89PNG")
        assert hit.inner_html() == original
        assert webrecord.passage_image(hit, "The entire budget was used.") is None
        browser.close()


def test_publisher_mark_pixels_are_identical_with_consent_banner():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content('<p>Records include shareholder agreements and the distribution schedule.</p>')
        words = "shareholder agreements"
        expected = webrecord.passage_image(page.locator("p"), words)
        page.evaluate("""() => {
            const banner = document.createElement('aside');
            banner.id = 'consent';
            banner.style.cssText = 'position:fixed; inset:0; background:navy; z-index:999999';
            banner.textContent = 'Cookie preferences';
            document.body.appendChild(banner);
        }""")
        assert webrecord.passage_image(page.locator("p"), words) == expected
        assert page.locator("#consent").is_visible()
        browser.close()
