"""One-way notifications to the founder's inbox.

Round 46. Some events are worth knowing about the minute they happen rather
than the next time someone opens the admin — the first of them is a trial
starting. This module is the single place those go out, so the sender, the
recipient and the failure behaviour are decided once.

Two rules it exists to enforce:

  1. SENDING MUST NEVER COST A CUSTOMER ANYTHING. Every caller so far is
     inside the Paddle webhook, where an exception or a slow request means
     Paddle retries the event and a real activation is at risk. So the send
     happens on a daemon thread and every failure is swallowed after being
     logged. A dropped alert is an annoyance; a broken webhook is a refund.
  2. A FAILURE MUST BE VISIBLE. Brevo's classic outage here is HTTP 401
     "unrecognised IP address" — the Authorised-IPs wall blocking Render's
     egress, which once took email delivery down for weeks while every call
     looked fine. So the real status and body are printed, exactly like
     send_code_to_email does.

Recipient is `FOUNDER_ALERT_EMAIL`, defaulting to the address the admin
dashboard is already gated to. Sender is the authenticated valmera.io domain
(thehustlerbot.com is not DKIM/SPF authenticated in Brevo and gets
spam-foldered even when the API returns 201).
"""

import os
import threading

import requests

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


def founder_email():
    return os.getenv("FOUNDER_ALERT_EMAIL", "thevalmera@gmail.com")


def _send_now(subject, html):
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        print(f"⚠️ [founder_alert] BREVO_API_KEY unset — not sent: {subject}",
              flush=True)
        return False

    to = founder_email()
    payload = {
        "sender": {
            "name": os.getenv("FROM_NAME", "Valmera"),
            "email": os.getenv("FROM_EMAIL", "support@valmera.io"),
        },
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }
    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }
    try:
        res = requests.post(BREVO_SEND_URL, json=payload, headers=headers,
                            timeout=15)
    except requests.RequestException as e:
        print(f"⚠️ [founder_alert] send to {to} failed (network): {e}",
              flush=True)
        return False
    if res.status_code != 201:
        print(f"⚠️ [founder_alert] send to {to} failed: HTTP "
              f"{res.status_code} {(res.text or '')[:400]}", flush=True)
        return False
    print(f"📧 [founder_alert] sent to {to}: {subject}", flush=True)
    return True


def send_founder_alert(subject, html):
    """Fire and forget. Returns immediately; the send happens off-request.

    The thread is a daemon: if the gunicorn worker is recycled mid-send the
    alert is lost. That is the deliberate trade — the alternative is holding a
    payment webhook open on Brevo's latency.
    """
    try:
        threading.Thread(target=_send_now, args=(subject, html),
                         daemon=True, name="founder-alert").start()
    except Exception as e:                                  # pragma: no cover
        print(f"⚠️ [founder_alert] could not start sender thread: {e}",
              flush=True)


# ── Shared shell so every alert looks like it came from the same product ─────

def render_alert(title, accent, lines, footer=None):
    """A dark card with a title and a list of (label, value) rows."""
    rows = "".join(
        f'<tr>'
        f'<td style="padding:6px 14px 6px 0;color:#8b8b95;font-size:13px;'
        f'white-space:nowrap;vertical-align:top">{label}</td>'
        f'<td style="padding:6px 0;color:#f2f2f5;font-size:14px;'
        f'font-weight:600">{value}</td>'
        f'</tr>'
        for label, value in lines if value not in (None, "")
    )
    foot = (f'<p style="margin:18px 0 0;color:#6b6b75;font-size:12px;'
            f'line-height:1.6">{footer}</p>') if footer else ""
    return f"""\
<div style="background:#0b0b0f;padding:28px;font-family:-apple-system,
     BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
  <div style="max-width:520px;margin:0 auto;background:#141419;
       border:1px solid #26262e;border-radius:14px;padding:24px 26px">
    <div style="height:3px;width:46px;background:{accent};
         border-radius:3px;margin-bottom:16px"></div>
    <h1 style="margin:0 0 18px;color:#fff;font-size:19px;
        letter-spacing:-0.01em">{title}</h1>
    <table style="border-collapse:collapse;width:100%">{rows}</table>
    {foot}
  </div>
</div>"""
