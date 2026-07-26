"""
Valmera lifecycle / newsletter email content.

This module holds:
  • the branded email SKELETON (dark, red-accent, email-client-safe tables)
  • a set of DESIGN BLOCKS that build the body fragments
  • token substitution ({{CTA_URL}}, {{CREDITS}}, {{UNSUB_URL}})
  • the DEFAULT hand-crafted templates for every behavioral campaign

The templates here are the *code defaults*. The admin can override any of them
from the dashboard (stored in the `newsletter_templates` table); a "reset"
deletes the DB row and falls back to the default below. So editing copy is a
DB edit — no redeploy — while the defaults remain the honest, on-brand baseline.

WHY THE BODIES ARE GENERATED, NOT TYPED (round 49)
--------------------------------------------------
Every template used to be one enormous hand-written HTML string. Six of them,
each repeating the same table scaffolding inline, which meant a design change
was six careful find-and-replaces and a check-row in one email drifted from the
check-row in the next. The blocks below emit that scaffolding once. The stored
default is still a plain HTML string — `DEFAULT_TEMPLATES` is built at import
— so the admin editor keeps working exactly as before.

THE DESIGN IS THE PRICING CARDS
-------------------------------
Same language as /subscribe and the studio's ModelSelector: #0b0b0b surfaces,
hairline #1e1e1e borders, monospace micro-labels in wide uppercase tracking, a
red accent, and a WHITE pill as the primary button. The site's main CTA has been
white-on-black for a long time; the emails were using a red button, so arriving
on the pricing page from an email looked like arriving at a different product.

EMAIL-CLIENT RULES THESE BLOCKS FOLLOW
--------------------------------------
  * tables + inline styles only — no flex, no grid, no <style> block that
    Gmail can strip
  * `bgcolor` beside every background colour, for Outlook
  * hex colours, never rgba() — Outlook drops the whole declaration
  * no web fonts: Arial for copy, a monospace stack for micro-labels. The
    brand font cannot load in Gmail, so pretending otherwise just yields
    Times New Roman
  * border-radius degrades to square in Outlook, which is fine and expected

Honesty rule (see CLAUDE.md / memory): every claim below maps to a REAL shipped
Valmera capability. Do not add features that don't exist. Note in particular
that a free account now has NO credits (round 49) — nothing here may promise
"you've got N credits waiting", which is what these templates used to open with.
"""

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

def wrap_email(body_html: str, unsubscribe_url: str, preheader: str = "") -> str:
    """Wrap an inner body fragment in the branded Valmera email shell.

    The shell owns the header + the footer with the unsubscribe link, so
    individual templates only ever author the middle.
    """
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark only">
<meta name="supported-color-schemes" content="dark only">
<title>Valmera</title>
</head>
<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%;" bgcolor="{BG}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{BG};font-size:1px;line-height:1px;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BG};" bgcolor="{BG}">
<tr><td align="center" style="padding:30px 14px 40px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background:{CARD};border:1px solid {LINE};border-radius:20px;" bgcolor="{CARD}">

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
<p style="margin:0 0 6px;font:400 12px/1.55 {SANS};color:{MUTED};">You're receiving this because you have a Valmera account. We only send things worth your time.</p>
<p style="margin:0;font:400 12px/1.55 {SANS};color:{MUTED};"><a href="{unsubscribe_url}" style="color:#999999;text-decoration:underline;">Unsubscribe</a> &nbsp;&middot;&nbsp; <a href="https://valmera.io" style="color:#999999;text-decoration:underline;">valmera.io</a></p>
</td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def render_tokens(text: str, *, cta_url: str = DEFAULT_CTA_URL, credits=None,
                  unsub_url: str = "") -> str:
    """Substitute the small, fixed set of tokens allowed in subjects/bodies."""
    if text is None:
        return ""
    out = text.replace("{{CTA_URL}}", cta_url or DEFAULT_CTA_URL)
    out = out.replace("{{UNSUB_URL}}", unsub_url or "")
    try:
        credits_str = str(int(round(float(credits)))) if credits is not None else "0"
    except (TypeError, ValueError):
        credits_str = "0"
    out = out.replace("{{CREDITS}}", credits_str)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  DEFAULT TEMPLATES  (key -> subject / preheader / body_html / enabled)
# ─────────────────────────────────────────────────────────────────────────────
#
# NOTE ON CREDITS: these used to close with "you've got {{CREDITS}} credits
# waiting". A free account has none as of round 49, so that line would have
# been a lie told to precisely the people being asked to come back. What is
# free now — uploading, the full index, the transcript, asking the agent about
# it — is real and is what these say instead. {{CREDITS}} still renders for any
# admin-stored template that uses it.

DEFAULT_TEMPLATES = {
    # new signup, no project yet
    "welcome_activation": {
        "subject": "Your first edit takes a sentence, not an afternoon",
        "preheader": "Upload a clip, tell it what you want, and it's done — no timeline, no scrubbing.",
        "enabled": True,
        "body_html": (
            eyebrow("Start here")
            + h1("Hand Valmera your video.<br>It does the editing.")
            + p("No timeline. No scrubbing. No lost afternoon. You upload a clip, "
                "type what you want in plain English, and the agent actually does "
                "the work.")
            + card(
                f'<p style="margin:0 0 14px;font:700 13px/1.4 {MONO};color:{WHITE};'
                'letter-spacing:0.08em;text-transform:uppercase;">Just type it</p>'
                + feature("&ldquo;<strong style=\"color:#fff;\">Cut the boring parts</strong>&rdquo; &mdash; it trims silences and filler like <em>um</em> and <em>uh</em> in one message.")
                + feature("&ldquo;<strong style=\"color:#fff;\">Add captions</strong>&rdquo; &mdash; pick a premium animated preset: Podcast, Beast, Karaoke or Elegant.")
                + feature("&ldquo;<strong style=\"color:#fff;\">Make it vertical</strong>&rdquo; &mdash; reframe to 9:16 for Reels, TikTok and Shorts.")
            )
            + p("Uploading your video and having it analysed &mdash; transcript, "
                "shots, silences, the lot &mdash; is <strong style=\"color:#fff;\">"
                "free</strong>. You can look at everything it found before you "
                "decide anything.")
            + cta("Upload your first clip &rarr;")
            + small("One clip, one sentence. See how much time you just got back.")
        ),
    },

    # has a project, never exported
    "export_nudge": {
        "subject": "You're one click from a finished video",
        "preheader": "You did the hard part. Give it a last pass, hit export, and it's a branded MP4 on your drive.",
        "enabled": True,
        "body_html": (
            eyebrow("Almost there")
            + h1("You did the hard part.")
            + p("You started the project. You made the edits. The only thing between "
                "you and a finished, branded video is one click: <strong "
                "style=\"color:#fff;\">Export</strong>.")
            + p("Don't let this one die in your drafts. Give it a last pass, hit "
                "export, and the agent renders it, brands it, and hands it back "
                "downloaded.")
            + card(
                f'<p style="margin:0 0 14px;font:700 13px/1.4 {MONO};color:{WHITE};'
                'letter-spacing:0.08em;text-transform:uppercase;">Finish it in three asks</p>'
                + numbered(1, "<strong style=\"color:#fff;\">Add captions</strong> &mdash; Podcast, Beast, Karaoke or Elegant. One message.")
                + numbered(2, "<strong style=\"color:#fff;\">Drop in music</strong> &mdash; a track from the built-in library, or paste a link to a song. Ducked under your voice automatically.")
                + numbered(3, "<strong style=\"color:#fff;\">Export</strong> &mdash; one click, branded end card, downloaded to your device.")
            )
            + cta("Finish your video &rarr;")
            + small("Each of those is one message. The agent does the rest.")
        ),
    },

    # was active, went quiet
    "dormant": {
        "subject": "Still spending your night editing?",
        "preheader": "Hand it to the agent — one message cuts silences, filler, and adds captions.",
        "enabled": True,
        "body_html": (
            eyebrow("It got faster")
            + h1("Back to editing the slow way?")
            + p("Scrubbing the timeline. Hunting for dead air. Deleting every "
                "&ldquo;um&rdquo; one by one, then fighting with caption styles past "
                "midnight. You already know how that ends.")
            + p("There's a faster path. Hand the agent your video, describe the edit "
                "in one message, and go do literally anything else.")
            + card(
                f'<p style="margin:0 0 14px;font:700 13px/1.4 {MONO};color:{WHITE};'
                'letter-spacing:0.08em;text-transform:uppercase;">One message, the boring parts gone</p>'
                + feature("<strong style=\"color:#fff;\">Cut the silences</strong> &mdash; dead air trimmed automatically, no scrubbing.")
                + feature("<strong style=\"color:#fff;\">Kill the filler</strong> &mdash; every &ldquo;um&rdquo; and &ldquo;uh&rdquo; removed in one pass.")
                + feature("<strong style=\"color:#fff;\">Add captions</strong> &mdash; premium animated presets, styled word by word.")
            )
            + p("That's the tight, punchy cut of your video &mdash; the hours you'd "
                "normally burn, handed straight to the agent.")
            + cta("Hand one to the agent &rarr;")
            + small("Drop in one video, type one message. See what comes back.")
        ),
    },

    # long gone
    "winback": {
        "subject": "Make a cinematic cut just by describing it 🎬",
        "preheader": "Color grades, Ken Burns zooms, animated captions, sound effects — all from chat.",
        "enabled": True,
        "body_html": (
            eyebrow("What's new")
            + h1("Cinematic edits,<br>from one sentence.")
            + p("You remember a rougher version. It grew up. You still just describe "
                "the edit in plain English &mdash; but now it delivers the kind of cut "
                "that used to take a pro hours in a timeline.")
            + card(
                f'<p style="margin:0 0 14px;font:700 13px/1.4 {MONO};color:{WHITE};'
                'letter-spacing:0.08em;text-transform:uppercase;">Here&rsquo;s what it makes today</p>'
                + feature("<strong style=\"color:#fff;\">A cinematic look</strong> &mdash; color grades, Ken Burns zooms on your stills, smooth fades and dip-to-black transitions.")
                + feature("<strong style=\"color:#fff;\">Premium animated captions</strong> &mdash; Podcast, Beast, Karaoke word-pop, Elegant. Pick a preset, it styles every word.")
                + feature("<strong style=\"color:#fff;\">Sound design, handled</strong> &mdash; sound effects to punctuate a moment, scored from the built-in library or a song you paste in.")
                + feature("<strong style=\"color:#fff;\">Paste a URL, it lands in your edit</strong> &mdash; any clip, song or image pulled straight in. Then export in one click.")
            )
            + cta("See what it makes now &rarr;")
            + small("Same deal as before: hand it your video, describe the edit, it does it.")
        ),
    },

    # weekly value (active + dormant)
    "weekly_value": {
        "subject": "An hour of editing, done in 3 messages",
        "preheader": "The exact chat lines that turn a long take into a punchy vertical short.",
        "enabled": True,
        "body_html": (
            eyebrow("This week's recipe")
            + h1("An hour of editing.<br>Three messages.")
            + p("You shot a long talking-head take. Normally that's an hour of "
                "scrubbing, cutting and captioning. This week, hand it over instead "
                "&mdash; type these three messages, then export.")
            + card(
                numbered(1, "&ldquo;<strong style=\"color:#fff;\">Cut all the silences and filler words</strong>&rdquo; &mdash; dead air and every &ldquo;um&rdquo; gone. Your take gets tight in one pass.")
                + numbered(2, "&ldquo;<strong style=\"color:#fff;\">Add Beast-style captions</strong>&rdquo; &mdash; bold, animated word-pop captions that hold attention all the way through.")
                + numbered(3, "&ldquo;<strong style=\"color:#fff;\">Make it 9:16 and add a subtle zoom</strong>&rdquo; &mdash; reframed for Reels, TikTok and Shorts with a slow cinematic push.")
            )
            + p("Then one word: <strong style=\"color:#fff;\">&ldquo;export&rdquo;</strong> "
                "&mdash; and you download the finished vertical short, branded end "
                "card and all.")
            + cta("Open Valmera &rarr;")
            + small("Try it on your next long take. It's done before your coffee's cold.")
        ),
    },

    # ── the 50%-off intro offer ──────────────────────────────────────────
    # ONE send, at ONE moment (round 49): 24 hours after an account registers,
    # if it never started a trial. It used to also go out the instant an
    # account existed, which meant the discount arrived before the product had
    # done anything — see the docstring in backend/offers.py for why that was
    # the wrong trade. The segment is routes/newsletter._eligible('offer_50'),
    # and a live trial can never match it (a trialling user is is_subscribed).
    #
    # The struck-through prices below cover Creator and Pro only. Frontier is
    # deliberately not discountable (offers.DISCOUNTABLE_PLANS) — do not add it
    # to this copy, because Paddle's restrict_to would refuse the checkout the
    # email had just promised.
    #
    # {{OFFER_PERCENT}} and {{OFFER_HOURS}} are substituted by offers._fill,
    # from the offer ROW — so the number of hours in the email is the real time
    # left on the real discount, not a hardcoded "24" that keeps being true
    # for about a minute. If the copy is edited in the admin, keep both tokens:
    # dropping them turns a countdown into a claim nobody is checking.
    "offer_50": {
        "subject": "{{OFFER_PERCENT}}% off — yours for the next {{OFFER_HOURS}} hours",
        "preheader": "Start your 3-day trial in the next {{OFFER_HOURS}} hours and your first month is half price.",
        "enabled": True,
        "body_html": (
            eyebrow("{{OFFER_HOURS}} hours left")
            + h1("{{OFFER_PERCENT}}% off<br>your first month.")
            + p("Start your <strong style=\"color:#fff;\">3-day free trial</strong> in "
                "the next <strong style=\"color:#fff;\">{{OFFER_HOURS}} hours</strong> "
                "and the first month after it is half price. You're not charged "
                "during the trial at all, and cancelling inside it costs nothing.")
            + card(
                f'<p style="margin:0 0 14px;font:700 11px/1.4 {MONO};color:{MICRO};'
                'letter-spacing:0.16em;text-transform:uppercase;">First month</p>'
                + price_row("Creator", 30, 15)
                + price_row("Pro", 50, 25)
                + f'<p style="margin:12px 0 0;font:400 13px/1.5 {SANS};color:{MUTED};">'
                  "Then the usual price. Cancel any time.</p>",
                accent=True,
            )
            + h2("What you get for it")
            + feature("<strong style=\"color:#fff;\">An editor you talk to</strong> &mdash; cuts, silences, filler words, captions, music, b-roll. You describe it, the agent does it.")
            + feature("<strong style=\"color:#fff;\">The AI model included</strong> &mdash; no API key, no second bill.")
            + feature("<strong style=\"color:#fff;\">Clean exports</strong> &mdash; no watermark, and a priority place in the render queue.")
            + cta("Claim {{OFFER_PERCENT}}% off &rarr;", url="https://valmera.io/subscribe")
            + divider()
            + small("The discount applies to your first month only, and it's one per "
                    "account. After {{OFFER_HOURS}} hours it's gone and the plans go "
                    "back to full price.")
        ),
    },
}


# The lifecycle campaigns the daily engine evaluates, in PRIORITY order.
# (weekly_value is handled separately, only on its scheduled weekday.)
#
# offer_50 runs FIRST: it is the only campaign with an expiry attached, so if a
# user is eligible for it and for something else on the same tick, the one with
# a clock on it is the one that should land (NOT_TODAY caps them at one).
LIFECYCLE_ORDER = ["offer_50", "welcome_activation", "export_nudge", "dormant",
                   "winback"]

# Human labels for the admin UI.
CAMPAIGN_LABELS = {
    "offer_50": "50% intro offer (24h after signup, no trial started)",
    "welcome_activation": "Welcome / Activation",
    "export_nudge": "Export nudge",
    "dormant": "Dormant win-back",
    "winback": "Long-gone win-back",
    "weekly_value": "Weekly value",
}
