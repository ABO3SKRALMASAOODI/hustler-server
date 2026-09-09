"""Client-safe email presentation and the September 2026 editorial library.

Code defaults can be overridden per template in newsletter_templates. Marketing
and account-service messages share presentation, but only marketing includes
unsubscribe controls. Copy lives in newsletter_campaigns as escaped plain text.
"""

from html import escape
from html.parser import HTMLParser
import re

from routes.newsletter_campaigns import (
    LIFECYCLE_COPY, WEEKLY_COPY, LIFECYCLE_ORDER, CAMPAIGN_LABELS,
    LIFECYCLE_FAMILIES, CAMPAIGN_FAMILY, WEEKLY_ORDER,
)

CONTENT_VERSION = "2026-09-09"

# Where the CTA buttons point by default (the studio, on the frontend).
DEFAULT_CTA_URL = "https://valmera.io/studio"

# ── Header art ───────────────────────────────────────────────────────────────
#
# The wordmark is TEXT, not part of an image, and that is the whole point: the
# header used to be a single 220x68 PNG containing both robot and wordmark, so
# until it loaded the email opened with an empty box. Measured at ~1.1s for
# 15KB from valmera.io, and Gmail adds its own image proxy on top — roughly
# three seconds of blank branding on a cold open.
#
# Now the name renders instantly with the HTML, and the only fetch is a 5.9KB
# robot (rendered from the same Rive file the site uses, trimmed and sized for
# a 44px box at 2x). Its width/height are set so the space is reserved rather
# than reflowing, and `alt` is styled so even a blocked image reads as brand
# rather than as a broken tile.
EMAIL_ROBOT_URL = "https://valmera.io/email-robot.png"

# Kept for any stored template that still references the old combined asset.
EMAIL_LOGO_URL = "https://valmera.io/email-logo.png"

# ── Palette ──────────────────────────────────────────────────────────────────
BG        = "#000000"   # page
CARD      = "#0b0b0b"   # surfaces, as on the pricing cards
CARD_ALT  = "#0e0e0e"   # nested cells (stat grid)
LINE      = "#1e1e1e"   # hairline borders
LINE_SOFT = "#161616"   # internal dividers
WHITE     = "#ffffff"
TEXT      = "#b4b4b4"   # body copy
MUTED     = "#7a7a7a"   # footnotes
MICRO     = "#5a5a5a"   # mono micro-labels
ACCENT    = "#cc0000"

MONO = "'JetBrains Mono',Menlo,Consolas,'Courier New',monospace"
SANS = "Arial,Helvetica,sans-serif"


# ─────────────────────────────────────────────────────────────────────────────
#  DESIGN BLOCKS — each returns a self-contained, inline-styled HTML fragment
# ─────────────────────────────────────────────────────────────────────────────

def eyebrow(text):
    """The wide-tracked monospace micro-label above a heading, straight off the
    pricing cards."""
    return (f'<p style="margin:0 0 10px;font:700 11px/1.4 {MONO};'
            f'color:{MICRO};letter-spacing:0.16em;text-transform:uppercase;">'
            f'{text}</p>')


def h1(text):
    return (f'<h1 style="margin:0 0 14px;font:800 27px/1.22 {SANS};'
            f'color:{WHITE};letter-spacing:-0.02em;">{text}</h1>')


def h2(text):
    return (f'<h2 style="margin:26px 0 12px;font:800 18px/1.3 {SANS};'
            f'color:{WHITE};letter-spacing:-0.01em;">{text}</h2>')


def p(text, color=None, size=16, bottom=16):
    return (f'<p style="margin:0 0 {bottom}px;font:400 {size}px/1.62 {SANS};'
            f'color:{color or TEXT};">{text}</p>')


def small(text):
    return p(text, color=MUTED, size=14, bottom=0)


def cta(label, url="{{CTA_URL}}"):
    """The primary button: a WHITE pill with black text, like every CTA on the
    site. bgcolor is set on the cell as well as in CSS because Outlook ignores
    the CSS background on a table cell."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:22px 0 10px;"><tr>'
        f'<td align="center" bgcolor="{WHITE}" '
        f'style="background:{WHITE};border-radius:999px;">'
        f'<a href="{url}" style="display:inline-block;padding:15px 34px;'
        f'font:800 15px/1 {SANS};color:#000000;text-decoration:none;'
        'border-radius:999px;letter-spacing:-0.01em;">'
        f'{label}</a></td></tr></table>')


def feature(text):
    """One red-check row. A text glyph rather than an image: an <img> icon per
    bullet is six more blocked-image placeholders in Outlook."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 10px;"><tr>'
        f'<td valign="top" style="width:24px;font:700 15px/1.5 {SANS};'
        f'color:{ACCENT};">&#10003;</td>'
        f'<td style="font:400 15px/1.55 {SANS};color:{TEXT};">{text}</td>'
        '</tr></table>')


def numbered(n, text):
    """A numbered step row — same geometry as `feature`, red index instead of a
    check."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 10px;"><tr>'
        f'<td valign="top" style="width:24px;font:700 15px/1.5 {SANS};'
        f'color:{ACCENT};">{n}</td>'
        f'<td style="font:400 15px/1.55 {SANS};color:{TEXT};">{text}</td>'
        '</tr></table>')


def card(inner, accent=False):
    """A surface in the pricing-card idiom. `accent` gives it the red hairline
    used for the one block that carries the offer."""
    border = f"{ACCENT}" if accent else LINE
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:6px 0 20px;background:{CARD};'
        f'border:1px solid {border};border-radius:16px;" bgcolor="{CARD}">'
        f'<tr><td style="padding:20px 22px;">{inner}</td></tr></table>')


def stats(pairs):
    """A row of value/label cells — the "3m 54s · 8 shots · 1,553 words" grid
    from the studio's video-ready card. Kept to <=3 cells: four columns at
    600px wide collapse badly on a phone."""
    cells = ""
    for value, label in pairs[:3]:
        cells += (
            f'<td align="center" valign="middle" bgcolor="{CARD_ALT}" '
            f'style="background:{CARD_ALT};padding:16px 8px;'
            f'border:1px solid {LINE};border-radius:12px;">'
            f'<div style="font:800 20px/1.15 {SANS};color:{WHITE};'
            'letter-spacing:-0.02em;">' + str(value) + '</div>'
            f'<div style="margin-top:5px;font:700 10px/1.3 {MONO};'
            f'color:{MICRO};letter-spacing:0.14em;text-transform:uppercase;">'
            f'{label}</div></td>')
        cells += '<td style="width:8px;font-size:0;line-height:0;">&nbsp;</td>'
    cells = cells.rsplit('<td style="width:8px', 1)[0]  # drop trailing spacer
    return ('<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0" style="margin:4px 0 20px;"><tr>'
            + cells + '</tr></table>')


def price_row(plan, was, now):
    """Struck-through price line for the offer email."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 8px;"><tr>'
        f'<td style="font:600 15px/1.5 {SANS};color:{TEXT};">{plan}</td>'
        f'<td align="right" style="font:400 15px/1.5 {SANS};color:{MUTED};">'
        f'<span style="text-decoration:line-through;">${was}</span>'
        f'<span style="color:{WHITE};font-weight:800;padding-left:10px;">'
        f'${now}</span></td></tr></table>')


def divider():
    return (f'<div style="height:1px;line-height:1px;font-size:0;'
            f'background:{LINE_SOFT};margin:22px 0;">&nbsp;</div>')


# ─────────────────────────────────────────────────────────────────────────────
#  SKELETON — wraps a body fragment into a full, client-safe HTML email
# ─────────────────────────────────────────────────────────────────────────────

def wrap_email(body_html: str, unsubscribe_url: str = "", preheader: str = "") -> str:
    """Wrap an inner body fragment in the branded Valmera email shell.

    The shell owns the header + the footer with the unsubscribe link, so
    individual templates only ever author the middle.
    """
    footer = (
        "You're receiving Valmera editing ideas and product emails because you have an account."
        if unsubscribe_url else
        "This is an account-service email from Valmera."
    )
    unsubscribe = (
        f'<a href="{escape(unsubscribe_url, quote=True)}" style="color:#b4b4b4;text-decoration:underline;">'
        'Unsubscribe from product emails</a> &nbsp;&middot;&nbsp; '
        if unsubscribe_url else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark only">
<meta name="supported-color-schemes" content="dark only">
<meta name="valmera-email-version" content="{CONTENT_VERSION}">
<title>Valmera</title>
</head>
<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%;" bgcolor="{BG}">
<div data-email-preheader="true" style="display:none;max-height:0;overflow:hidden;opacity:0;color:{BG};font-size:1px;line-height:1px;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BG};" bgcolor="{BG}">
<tr><td align="center" style="padding:30px 14px 40px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:{CARD};border:1px solid {LINE};border-radius:20px;" bgcolor="{CARD}">

<tr><td style="padding:26px 32px 4px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
    <td valign="middle" style="padding-right:11px;">
      <img src="{EMAIL_ROBOT_URL}" width="30" height="44" alt="" style="display:block;border:0;outline:none;text-decoration:none;width:30px;height:44px;">
    </td>
    <td valign="middle">
      <span style="font:800 21px/1 {SANS};color:{WHITE};letter-spacing:-0.02em;">Valmera</span>
      <div style="margin-top:4px;font:700 9px/1.2 {MONO};color:{MICRO};letter-spacing:0.18em;text-transform:uppercase;">Agentic video editor</div>
    </td>
  </tr></table>
</td></tr>

<tr><td style="padding:18px 32px 6px;">
{body_html}
</td></tr>

<tr><td style="padding:20px 32px 28px;border-top:1px solid {LINE};">
<p style="margin:0 0 8px;font:400 12px/1.55 {SANS};color:{MUTED};">{footer}</p>
<p style="margin:0;font:400 12px/1.55 {SANS};color:{MUTED};">{unsubscribe}<a href="mailto:support@valmera.io" style="color:#b4b4b4;text-decoration:underline;">Contact support</a> &nbsp;&middot;&nbsp; <a href="https://valmera.io" style="color:#b4b4b4;text-decoration:underline;">valmera.io</a></p>
</td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def render_tokens(text: str, *, cta_url: str = DEFAULT_CTA_URL, credits=None,
                  unsub_url: str = "", html: bool = False) -> str:
    """Substitute the small, fixed set of tokens allowed in subjects/bodies."""
    if text is None:
        return ""
    cta_value = cta_url or DEFAULT_CTA_URL
    unsub_value = unsub_url or ""
    if html:
        cta_value = escape(cta_value, quote=True)
        unsub_value = escape(unsub_value, quote=True)
    out = text.replace("{{CTA_URL}}", cta_value)
    out = out.replace("{{UNSUB_URL}}", unsub_value)
    try:
        credits_str = str(int(round(float(credits)))) if credits is not None else "0"
    except (TypeError, ValueError):
        credits_str = "0"
    out = out.replace("{{CREDITS}}", credits_str)
    return out


def _campaign_template(item):
    key, label, subject, preheader, heading, opening, prompt, closing, button = item
    body = eyebrow("An editing idea from Valmera") + h1(escape(heading))
    body += "".join(p(escape(paragraph)) for paragraph in opening)
    if prompt:
        body += card(eyebrow("Try this in chat") + p(escape(prompt), color=WHITE, bottom=0))
    body += "".join(p(escape(paragraph)) for paragraph in closing)
    body += cta(escape(button))
    return {"subject": subject, "preheader": preheader, "body_html": body, "enabled": True}


DEFAULT_TEMPLATES = {
    item[0]: _campaign_template(item) for item in LIFECYCLE_COPY + WEEKLY_COPY
}
# Preserve the historical key without retaining a stale offer or trial promise.
DEFAULT_TEMPLATES["offer_50"] = {
    "subject": "Retired introductory offer",
    "preheader": "This campaign is no longer sent.",
    "body_html": p("The introductory discount campaign has been retired."),
    "enabled": False,
}


class _EmailText(HTMLParser):
    """Readable MIME alternative, including working links and opt-out URL."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("head", "style", "script") or attrs.get("data-email-preheader"):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("p", "h1", "h2", "tr", "br", "div"):
            self.parts.append("\n")
        if tag == "a":
            self.link = attrs.get("href", "")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag in ("head", "style", "script", "div"):
                self.hidden -= 1
            return
        if tag == "a" and self.link:
            self.parts.append(f" ({self.link})")
            self.link = None
        if tag in ("p", "h1", "h2", "tr", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html):
    parser = _EmailText()
    parser.feed(html or "")
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip()
             for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def verification_email(code):
    """Keep the requested code prominent; authentication is not an upsell."""
    safe_code = escape(str(code))
    body = (eyebrow("Confirm your email") + h1("Your next step is six digits.")
            + p("Enter this code in the Valmera tab where you requested it.")
            + card(f'<p style="margin:0;font:700 36px/1.4 {MONO};letter-spacing:0.12em;color:{WHITE};">{safe_code}</p>')
            + p("The code expires in 5 minutes. Keep it private; Valmera support will never ask you to share it.")
            + small("If you didn't request this code, you can ignore this email."))
    html = wrap_email(body, preheader="Your verification code expires in 5 minutes.")
    return {"subject": "Your Valmera verification code", "htmlContent": html,
            "textContent": plain_text(html)}


def payment_email(plan_label, decline_text, payment_url, account_url):
    body = (eyebrow("Payment update") + h1("Let's get your payment sorted.")
            + p(f"We couldn't complete the payment for your {escape(plan_label)} plan. {escape(decline_text)}")
            + p("Open the secure billing page to review the payment and update your details if needed. Your account shows the current status of your subscription and editing access.")
            + cta("Review my payment", escape(payment_url or account_url, quote=True))
            + p("Payment retries may still occur while the subscription is active. If you want to stop future renewals, manage or cancel your subscription from your account.")
            + p(f'<a href="{escape(account_url, quote=True)}" style="color:{WHITE};text-decoration:underline;">Manage my subscription</a>')
            + small("Need help understanding the payment? Reply to this email or contact support@valmera.io."))
    html = wrap_email(body, preheader="Review your payment details and subscription status securely.")
    return {"subject": "Action needed: your Valmera payment", "htmlContent": html,
            "textContent": plain_text(html)}
