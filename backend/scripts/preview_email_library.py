"""Build a local, self-contained review gallery. Does not send email."""
import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes.newsletter_content import (
    DEFAULT_TEMPLATES, CAMPAIGN_LABELS, CONTENT_VERSION, wrap_email,
    render_tokens, plain_text, verification_email, payment_email,
)


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    messages = []
    for key, template in DEFAULT_TEMPLATES.items():
        if not template["enabled"]:
            continue
        body = render_tokens(template["body_html"], unsub_url="#preview-only", html=True)
        rendered = wrap_email(body, "#preview-only", template["preheader"])
        messages.append({"key": key, "label": CAMPAIGN_LABELS[key],
                         "subject": template["subject"], "preheader": template["preheader"],
                         "html": rendered, "text": plain_text(rendered)})
    for key, label, template in (
        ("verification", "Account · Verification", verification_email("123456")),
        ("payment", "Account · Payment needs attention", payment_email(
            "Creator", "The bank declined the payment.",
            "https://valmera.io/account", "https://valmera.io/account")),
    ):
        messages.append({"key": key, "label": label, "subject": template["subject"],
                         "preheader": "Account-service message", "html": template["htmlContent"],
                         "text": template["textContent"]})
    options = "".join(f'<option value="{index}">{html.escape(message["label"])}</option>'
                      for index, message in enumerate(messages))
    data = json.dumps(messages).replace("</", "<\\/")
    gallery = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Valmera email library</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f2f1ed;color:#171714;font:16px/1.5 Arial,sans-serif}
header{max-width:1040px;margin:auto;padding:28px 20px 16px}h1{font-size:30px;margin:0 0 8px}p{margin:6px 0 14px}
.controls{display:flex;gap:10px;flex-wrap:wrap}select,button{font:inherit;border:1px solid #ccc;border-radius:8px;padding:10px;background:white;color:#171714}
select{max-width:100%;flex:1;min-width:220px}button{cursor:pointer}main{max-width:1040px;margin:auto;padding:0 20px 30px}
.inbox{border:1px solid #ddd;background:white;border-radius:12px;padding:18px;margin:8px 0 20px}#subject{font-size:20px;font-weight:700}#preheader{font-size:14px;color:#555;margin-top:5px}
#frame{display:block;margin:0 auto;border:0;width:100%;max-width:680px;background:#000;min-height:900px;border-radius:12px}
small{color:#666}details{margin-top:20px}pre{white-space:pre-wrap;background:white;padding:20px;border-radius:12px;font:15px/1.6 Arial,sans-serif}
@media(max-width:500px){header,main{padding-left:10px;padding-right:10px}h1{font-size:25px}}</style>
<header><small>VALMERA / EMAIL LIBRARY / VERSION ''' + CONTENT_VERSION + '''</small>
<h1>Good footage deserves a next step.</h1><p>27 distinct marketing emails and 2 account messages. Preview only; nothing is sent.</p>
<div class="controls"><select id="message" aria-label="Choose an email">''' + options + '''</select>
<button id="previous">Previous</button><button id="next">Next</button><button id="width">Phone width</button></div></header>
<main><div class="inbox"><small>From: Valmera &lt;support@valmera.io&gt;</small><div id="subject"></div><div id="preheader"></div></div>
<iframe id="frame" title="Email preview" sandbox="allow-same-origin"></iframe>
<details><summary>Read the plain-text version</summary><pre id="text"></pre></details></main>
<script>const messages=''' + data + ''';const select=document.querySelector('#message'),frame=document.querySelector('#frame');
function resize(){frame.style.height=Math.max(700,frame.contentDocument?.documentElement.scrollHeight||900)+'px'}
function show(){const m=messages[Number(select.value)];document.querySelector('#subject').textContent=m.subject;document.querySelector('#preheader').textContent=m.preheader;document.querySelector('#text').textContent=m.text;frame.srcdoc=m.html}
frame.addEventListener('load',resize);select.addEventListener('change',show);window.addEventListener('resize',resize);
document.querySelector('#next').onclick=()=>{select.value=(Number(select.value)+1)%messages.length;show()};
document.querySelector('#previous').onclick=()=>{select.value=(Number(select.value)+messages.length-1)%messages.length;show()};
document.querySelector('#width').onclick=()=>{const phone=frame.style.maxWidth!=='390px';frame.style.maxWidth=phone?'390px':'680px';document.querySelector('#width').textContent=phone?'Desktop width':'Phone width';requestAnimationFrame(resize)};show();</script></html>'''
    (output / "email-library.html").write_text(gallery)
    lines = ["# Valmera email library", "", "27 marketing messages and two account-service messages. No discount campaign. Templates are editable in Admin → Email.", ""]
    for message in messages:
        lines += [f'## {message["label"]}', "", f'**Subject:** {message["subject"]}', "",
                  f'**Preview:** {message["preheader"]}', "", message["text"], ""]
    (output / "EMAIL_LIBRARY.md").write_text("\n".join(lines))
    print(f"Generated {len(messages)} previews in {output.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
