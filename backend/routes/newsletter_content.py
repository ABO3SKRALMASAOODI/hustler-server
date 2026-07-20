"""
Valmera lifecycle / newsletter email content.

This module holds:
  • the branded email SKELETON (dark, red-accent, email-client-safe table layout)
  • token substitution (({{CTA_URL}}, {{CREDITS}}, {{UNSUB_URL}}))
  • the DEFAULT hand-crafted templates for every behavioral campaign

The templates here are the *code defaults*. The admin can override any of them
from the dashboard (stored in the `newsletter_templates` table); a "reset"
deletes the DB row and falls back to the default below. So editing copy is a
DB edit — no redeploy — while the defaults remain the honest, on-brand baseline.

Honesty rule (see CLAUDE.md / memory): every claim below maps to a REAL shipped
Valmera capability. Do not add features that don't exist.
"""

# Where the CTA buttons point by default (the studio, on the frontend).
DEFAULT_CTA_URL = "https://valmera.io/studio"

# The email header logo — the full-body Valmera hero robot (rendered from the
# landing page's Rive robot) + the "Valmera" wordmark (Plus Jakarta Sans ExtraBold),
# baked into one image so the exact brand font renders identically in every email
# client (live webfonts don't survive Gmail/Outlook). Hosted in the frontend's
# public/ dir → served at valmera.io/email-logo.png. Natural 439x136; shown 220x68.
EMAIL_LOGO_URL = "https://valmera.io/email-logo.png"


# ─────────────────────────────────────────────────────────────────────────────
#  SKELETON — wraps a body fragment into a full, client-safe HTML email
# ─────────────────────────────────────────────────────────────────────────────

def wrap_email(body_html: str, unsubscribe_url: str, preheader: str = "") -> str:
    """Wrap an inner body fragment in the branded Valmera email shell.

    The shell owns the logo header + the footer with the unsubscribe link, so
    individual templates only ever author the middle. Everything is table-based
    and inline-styled to survive Gmail / Apple Mail / Outlook.
    """
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>Valmera</title>
</head>
<body style="margin:0;padding:0;background:#0a0a0a;-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#0a0a0a;font-size:1px;line-height:1px;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#0a0a0a;">
<tr><td align="center" style="padding:32px 14px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background:#111111;border:1px solid #222222;border-radius:16px;">
<tr><td style="padding:28px 36px 8px;">
<img src="{EMAIL_LOGO_URL}" width="220" height="68" alt="Valmera" style="display:block;border:0;outline:none;text-decoration:none;height:68px;width:220px;">
</td></tr>
<tr><td style="padding:2px 36px 8px;">
{body_html}
</td></tr>
<tr><td style="padding:22px 36px 30px;border-top:1px solid #222222;">
<p style="margin:0 0 6px;font:400 12px/1.5 Arial,Helvetica,sans-serif;color:#777777;">You're receiving this because you have a Valmera account. We only send things worth your time.</p>
<p style="margin:0;font:400 12px/1.5 Arial,Helvetica,sans-serif;color:#777777;"><a href="{unsubscribe_url}" style="color:#999999;text-decoration:underline;">Unsubscribe</a> &nbsp;&middot;&nbsp; <a href="https://valmera.io" style="color:#999999;text-decoration:underline;">valmera.io</a></p>
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
#  Bodies use only the shared design blocks. Replaced/refined by the admin at will.
# ─────────────────────────────────────────────────────────────────────────────


DEFAULT_TEMPLATES = {
    # new signup, no project yet
    "welcome_activation": {
        "subject": "Your first edit takes a sentence, not an afternoon",
        "preheader": "Upload a clip, tell it what you want, and it's done — no timeline, no scrubbing.",
        "enabled": True,
        "body_html": "<h1 style=\"margin:14px 0 10px;font:800 26px/1.25 Arial,Helvetica,sans-serif;color:#ffffff;\">Hand Valmera your video. It does the editing.</h1>\n\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">No timeline. No scrubbing. No lost afternoon. You upload a clip, <strong style=\"color:#fff;\">type what you want in plain English</strong>, and the agent actually does the work.</p>\n\n<h2 style=\"margin:22px 0 8px;font:700 18px/1.3 Arial,Helvetica,sans-serif;color:#ffffff;\">Just type it. For example:</h2>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">&ldquo;Cut the boring parts&rdquo;</strong> &mdash; it trims silences and filler like <em>um</em> and <em>uh</em> in one message.</td></tr></table>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">&ldquo;Add captions&rdquo;</strong> &mdash; pick a premium animated preset: Podcast, Beast, Karaoke, or Elegant.</td></tr></table>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">&ldquo;Make it vertical&rdquo;</strong> &mdash; reframe to 9:16 for Reels, TikTok, and Shorts.</td></tr></table>\n\n<p style=\"margin:16px 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Then hit export and download the finished video. That's the whole workflow.</p>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;\"><tr><td style=\"padding:16px 20px;font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\">You've got <strong style=\"color:#fff;\">{{CREDITS}} credits</strong> waiting, and each edit costs about 1&ndash;2. Plenty to build your first video today.</td></tr></table>\n\n<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;\"><tr><td align=\"center\" bgcolor=\"#dc2626\" style=\"border-radius:10px;\"><a href=\"{{CTA_URL}}\" style=\"display:inline-block;padding:14px 32px;font:700 15px Arial,Helvetica,sans-serif;color:#ffffff;text-decoration:none;border-radius:10px;\">Make your first edit &rarr;</a></td></tr></table>\n\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#8a8a8a;\">One clip, one sentence. See how much time you just got back.</p>",
    },
    # made edits, never exported (the churn cliff)
    "export_nudge": {
        "subject": "You're one click from a finished video",
        "preheader": "You did the hard part. Give it a last pass, hit export, and it's a branded MP4 on your drive.",
        "enabled": True,
        "body_html": "<h1 style=\"margin:14px 0 10px;font:800 26px/1.25 Arial,Helvetica,sans-serif;color:#ffffff;\">You did the hard part.</h1>\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">You started the project. You made the edits. The only thing between you and a <strong style=\"color:#fff;\">finished, branded video</strong> is one click: <strong style=\"color:#fff;\">Export</strong>.</p>\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Don't let this one die in your drafts. Give it a last pass, hit export, and the agent renders it, brands it, and hands it back downloaded.</p>\n<h2 style=\"margin:22px 0 8px;font:700 18px/1.3 Arial,Helvetica,sans-serif;color:#ffffff;\">Finish it in three quick asks</h2>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Add captions</strong> &mdash; pick Podcast, Beast, Karaoke or Elegant. One message.</td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Drop in music</strong> &mdash; generate a track or fetch a song by name, ducked under your voice.</td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Export</strong> &mdash; one click, branded end card, downloaded to your device.</td></tr></table>\n<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;\"><tr><td align=\"center\" bgcolor=\"#dc2626\" style=\"border-radius:10px;\"><a href=\"{{CTA_URL}}\" style=\"display:inline-block;padding:14px 32px;font:700 15px Arial,Helvetica,sans-serif;color:#ffffff;text-decoration:none;border-radius:10px;\">Export your video &rarr;</a></td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;\"><tr><td style=\"padding:16px 20px;font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Each edit runs about 1&ndash;2 credits, and you've got <strong style=\"color:#fff;\">{{CREDITS}}</strong> &mdash; more than enough to caption it, score it, and ship it.</td></tr></table>",
    },
    # used it, quiet 1-3 weeks
    "dormant": {
        "subject": "Still spending your night editing?",
        "preheader": "Hand it to the agent — one message cuts silences, filler, and adds captions.",
        "enabled": True,
        "body_html": "<h1 style=\"margin:14px 0 10px;font:800 26px/1.25 Arial,Helvetica,sans-serif;color:#ffffff;\">Back to editing the slow way?</h1>\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Scrubbing the timeline. Hunting for dead air. Deleting every <strong style=\"color:#fff;\">&ldquo;um&rdquo;</strong> one by one, then fighting with caption styles past midnight. You already know how that ends.</p>\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">There&rsquo;s a faster path now. <strong style=\"color:#fff;\">Hand the agent your video and describe the edit in one message.</strong> It does the work &mdash; while you go do literally anything else.</p>\n<h2 style=\"margin:22px 0 8px;font:700 18px/1.3 Arial,Helvetica,sans-serif;color:#ffffff;\">One message. The boring parts gone.</h2>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Cut the silences</strong> &mdash; dead air trimmed automatically, no scrubbing.</td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Kill the filler</strong> &mdash; every &ldquo;um&rdquo; and &ldquo;uh&rdquo; removed in one pass.</td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Add captions</strong> &mdash; premium animated presets: Podcast, Beast, Karaoke, Elegant.</td></tr></table>\n<p style=\"margin:16px 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">That&rsquo;s the tight, punchy cut of your video &mdash; the hours you&rsquo;d normally burn, handed straight to the agent.</p>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;\"><tr><td style=\"padding:16px 20px;font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\">You&rsquo;ve still got <strong style=\"color:#ffffff;\">{{CREDITS}} credits</strong> sitting in your account. Each edit runs about 1&ndash;2 &mdash; that&rsquo;s a lot of videos you never have to hand-edit again.</td></tr></table>\n<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;\"><tr><td align=\"center\" bgcolor=\"#dc2626\" style=\"border-radius:10px;\"><a href=\"{{CTA_URL}}\" style=\"display:inline-block;padding:14px 32px;font:700 15px Arial,Helvetica,sans-serif;color:#ffffff;text-decoration:none;border-radius:10px;\">Edit one in minutes &rarr;</a></td></tr></table>\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#8a8a8a;\">Drop in one video, type one message. See how fast done actually feels.</p>",
    },
    # gone 30+ days
    "winback": {
        "subject": "Make a cinematic cut just by describing it 🎬",
        "preheader": "Color grades, Ken Burns zooms, animated captions, sound effects — all from chat.",
        "enabled": True,
        "body_html": "<h1 style=\"margin:14px 0 10px;font:800 26px/1.25 Arial,Helvetica,sans-serif;color:#ffffff;\">Cinematic edits, from one sentence.</h1>\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">You remember a rougher version. It grew up. You still just <strong style=\"color:#fff;\">describe the edit in plain English</strong> and the agent does it &mdash; but now it delivers the kind of cut that used to take a pro hours in a timeline.</p>\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Here's what it can make for you today:</p>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">A cinematic look</strong> &mdash; color grades, Ken Burns zooms on your stills, smooth fades and dip-to-black transitions.</td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Premium animated captions</strong> &mdash; Podcast, Beast, Karaoke word-pop, Elegant. Pick a preset, it styles every word.</td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Sound design, handled</strong> &mdash; the agent drops in sound effects to punctuate a moment, and can generate a music track or fetch a song by name.</td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">&#10003;</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">Paste a URL, it lands in your edit</strong> &mdash; any clip, song, or image pulled straight in. Then export and download in one click.</td></tr></table>\n<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;\"><tr><td align=\"center\" bgcolor=\"#dc2626\" style=\"border-radius:10px;\"><a href=\"{{CTA_URL}}\" style=\"display:inline-block;padding:14px 32px;font:700 15px Arial,Helvetica,sans-serif;color:#ffffff;text-decoration:none;border-radius:10px;\">See what it makes now &rarr;</a></td></tr></table>\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;\"><tr><td style=\"padding:16px 20px;font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Same deal as before: hand it your video, describe the edit, the agent does it. Each edit runs about 1&ndash;2 credits &mdash; and you've still got <strong style=\"color:#fff;\">{{CREDITS}}</strong> to spend.</td></tr></table>",
    },
    # weekly value (active + dormant)
    "weekly_value": {
        "subject": "An hour of editing, done in 3 messages",
        "preheader": "The exact chat lines that turn a long take into a punchy vertical short.",
        "enabled": True,
        "body_html": "<h1 style=\"margin:14px 0 10px;font:800 26px/1.25 Arial,Helvetica,sans-serif;color:#ffffff;\">An hour of editing. Three messages.</h1>\n\n<p style=\"margin:0 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">You shot a long talking-head take. Normally that's an hour of scrubbing, cutting, and captioning. This week, hand it to the agent instead &mdash; type these three messages, then export. Here's the exact recipe.</p>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">1</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">&ldquo;Cut all the silences and filler words&rdquo;</strong> &mdash; dead air and every &ldquo;um&rdquo; gone. Your take gets tight in one pass.</td></tr></table>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">2</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">&ldquo;Add Beast-style captions&rdquo;</strong> &mdash; bold, animated word-pop captions that hold attention all the way through.</td></tr></table>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:0 0 11px;\"><tr><td valign=\"top\" style=\"width:22px;font:700 16px Arial,Helvetica,sans-serif;color:#dc2626;\">3</td><td style=\"font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\"><strong style=\"color:#ffffff;\">&ldquo;Make it 9:16 and add a subtle zoom&rdquo;</strong> &mdash; reframed for Reels, TikTok, and Shorts with a slow cinematic push.</td></tr></table>\n\n<p style=\"margin:16px 0 16px;font:400 16px/1.62 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Then one word: <strong style=\"color:#fff;\">&ldquo;export&rdquo;</strong> &mdash; and you download the finished vertical short, branded end card and all.</p>\n\n<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;\"><tr><td align=\"center\" bgcolor=\"#dc2626\" style=\"border-radius:10px;\"><a href=\"{{CTA_URL}}\" style=\"display:inline-block;padding:14px 32px;font:700 15px Arial,Helvetica,sans-serif;color:#ffffff;text-decoration:none;border-radius:10px;\">Open Valmera &rarr;</a></td></tr></table>\n\n<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin:6px 0 22px;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;\"><tr><td style=\"padding:16px 20px;font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#c9c9c9;\">Each edit costs about <strong style=\"color:#fff;\">1&ndash;2 credits</strong>, so the whole recipe barely dents your balance of <strong style=\"color:#fff;\">{{CREDITS}}</strong>. No timeline, no scrubbing &mdash; just the messages.</td></tr></table>\n\n<p style=\"margin:0 0 8px;font:400 15px/1.55 Arial,Helvetica,sans-serif;color:#8a8a8a;\">Try it on your next long take. It's done before your coffee's cold.</p>",
    },
}


# The lifecycle campaigns the daily engine evaluates, in PRIORITY order.
# (weekly_value is handled separately, only on its scheduled weekday.)
LIFECYCLE_ORDER = ["welcome_activation", "export_nudge", "dormant", "winback"]

# Human labels for the admin UI.
CAMPAIGN_LABELS = {
    "welcome_activation": "Welcome / Activation",
    "export_nudge": "Export nudge",
    "dormant": "Dormant win-back",
    "winback": "Long-gone win-back",
    "weekly_value": "Weekly value",
}
