import io

from PIL import Image
from playwright.sync_api import sync_playwright
import pytest

import motion_engine
import motion_templates


@pytest.mark.parametrize("stage_h", [1920, None])
def test_custom_cta_draws_inside_landscape_viewport(stage_h):
    height = f"{stage_h}px" if stage_h else "100%"
    body = f"""<div style="position:absolute;inset:0;width:1080px;height:{height};
                display:grid;place-items:center">
                <div style="width:240px;height:80px;background:red"></div></div>"""
    job = motion_templates.build_job(dict(id="cta", template="html", html=body,
                                          start=240, end=242.6), 780, 360, 30)
    dw, dh = motion_engine.design_size(780, 360)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": dw, "height": dh})
        page.set_content(job.html)
        page.evaluate("window.__mgSeek(1.5)")
        image = Image.open(io.BytesIO(page.screenshot(omit_background=True)))
        browser.close()
    x0, y0, x1, y1 = image.getchannel("A").getbbox()
    assert abs(x0+x1-dw) <= 2 and abs(y0+y1-dh) <= 2
    assert abs((x1-x0) - 240 * min(1, dh/(stage_h or dh))) <= 2
