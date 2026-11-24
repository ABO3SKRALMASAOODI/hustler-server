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
