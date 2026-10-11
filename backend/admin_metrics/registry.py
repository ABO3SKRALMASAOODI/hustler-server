"""The metric registry (plan §1.2): one label and one "how" line per key.

Every Metric an endpoint returns takes its label, how, unit and polarity from
here, so the frontend never hard-codes metric copy and two pages can never
describe the same number differently. `how` is shown inline under the value
and is kept to one plain sentence (at most 160 characters, enforced by tests).
"""

UP, DOWN, NEUTRAL = "up_good", "down_good", "neutral"


def _m(label, how, unit="count", polarity=NEUTRAL, section="traffic"):
    return {"label": label, "how": how, "unit": unit, "polarity": polarity,
            "section": section}


REGISTRY = {
    # ── A. Traffic ───────────────────────────────────────────────────────
    "people": _m(
        "People who visited",
        "Browsers that scrolled, clicked, opened a 2nd page or stayed 30+ "
        "seconds. Robots, link previews and your own devices are left out.",
        polarity=UP),
    "link_previews": _m(
        "Link previews",
        "Outreach links opened automatically by Instagram/Facebook when a "
        "message was sent. Not people."),
    "no_signal_loads": _m(
        "Loads with no activity",
        "Browsers that opened one page and did nothing: quick exits, "
        "background tabs or unlabelled robots. Not counted as people."),
    "robots": _m(
        "Robots",
        "Known robots and tools (search engines, page checkers, our own AI "
        "agents), recognised by their browser name."),
    "internal": _m(
        "Your devices",
        "Browsers that opened the admin or were marked as yours. Never "
        "counted as people."),
    "returning_customers": _m(
        "Already customers",
        "People who visited while signed in to Valmera."),
    "people_now": _m(
        "On the site now",
        "Browsers active on a page in the last 5 minutes. Robots and your "
        "devices left out."),
    "signed_in_now": _m(
        "Signed-in customers now",
        "Customers whose signed-in app was open in the last 5 minutes."),

    # ── B. Signups and sources ───────────────────────────────────────────
    "signups": _m(
        "New signups",
        "Accounts created and verified (email code or Google). Your account "
        "and test accounts excluded.", polarity=UP, section="signups"),
    "signup_rate": _m(
        "Signup rate",
        "New signups ÷ people who visited, in the same period. A same-period "
        "ratio, not each person's path.",
        unit="percent", polarity=UP, section="signups"),
    "channel_people": _m(
        "People", "People in the period whose browser first came from this "
        "channel (or latest, if you switch). Robots, link previews and your "
        "devices left out.", polarity=UP, section="signups"),
    "channel_signups": _m(
        "Signups", "Signups in the period whose first recorded source (or "
        "latest, if you switch) is this channel.", polarity=UP,
        section="signups"),
    "channel_paying": _m(
        "Paying", "Of those signups, how many have made a successful payment "
        "since.", polarity=UP, section="signups"),
    "channel_collected": _m(
        "Collected so far", "Everything those signups have paid up to today.",
        unit="usd", polarity=UP, section="signups"),
    "source_coverage": _m(
        "Source recorded", "Signups in the period that have a recorded "
        "source, out of all signups in the period.", unit="percent",
        polarity=UP, section="signups"),
    "not_recorded": _m(
        "Not recorded", "Signups with no recorded source, split by reason: "
        "before tracking, privacy browser, page not loaded yet, nothing sent.",
        polarity=DOWN, section="signups"),
    "told_us": _m(
        "Told us", "Their answer to \"Where did you hear about us?\" at "
        "signup. Self-reported, not measured.", section="signups"),
    "outreach_links_opened": _m(
        "Links opened", "Different outreach links (one per recipient) opened "
        "at least once, by anything.", section="signups"),
    "outreach_previews": _m(
        "Link previews", "Opens of outreach links by Meta's link-preview "
        "robots.", section="signups"),
    "outreach_people": _m(
        "Opened by a person", "Outreach links opened by a browser that did "
        "something a person does.", polarity=UP, section="signups"),
    "outreach_signups": _m(
        "Signed up", "Signups whose browser carried an outreach link before "
        "signing up.", polarity=UP, section="signups"),
    "outreach_paying": _m(
        "Paying", "Of those, customers with a successful payment.",
        polarity=UP, section="signups"),
    "page_people": _m(
        "People", "People whose first page in the period was this page.",
        section="signups"),
    "page_active_median": _m(
        "Typical active time", "Median seconds people were active on the "
        "page. The clock stops 30 s after the last scroll, click or key.",
        unit="seconds", section="signups"),
    "page_signups": _m(
        "Signed up later", "People who landed here and later signed up on "
        "the same browser.", section="signups"),

    # ── C. Funnel and product use ────────────────────────────────────────
    "funnel_signed_up": _m(
        "Signed up", "Accounts created and verified in the period. Your "
        "account and test accounts excluded.", polarity=UP, section="funnel"),
    "funnel_uploaded": _m(
        "Uploaded a video", "Signups from the period who have uploaded at "
        "least one video.", polarity=UP, section="funnel"),
    "funnel_asked_edit": _m(
        "Asked for an edit", "Signups from the period who sent a message in "
        "the editor or made an editing call from an AI app (MCP).",
        polarity=UP, section="funnel"),
    "funnel_exported": _m(
        "Exported a video", "Signups from the period with at least one "
        "finished export.", polarity=UP, section="funnel"),
    "funnel_paid": _m(
        "Paid", "Signups from the period with at least one successful "
        "payment.", polarity=UP, section="funnel"),
    "blocker_upload_failed": _m(
        "Upload failed or refused", "People in the group with at least one "
        "failed or refused upload.", polarity=DOWN, section="funnel"),
    "blocker_paywall_upload": _m(
        "Saw \"subscribe to upload\"", "People in the group shown the upload "
        "lock.", section="funnel"),
    "blocker_paywall_chat": _m(
        "Saw \"subscribe to edit\"", "People in the group whose editor "
        "message was answered with a subscribe or out-of-credits reply.",
        section="funnel"),
    "blocker_plans_seen": _m(
        "Saw the plans", "People in the group shown the plan cards.",
        section="funnel"),
    "checkout_steps": _m(
        "Checkout", "Opened checkout, checkout loaded, chose a payment "
        "method, tried to pay, payment recorded within 7 days.",
        section="funnel"),
    "cohort_rows": _m(
        "Weekly signup groups", "Signups per Dubai week (Mon–Sun) and how "
        "many reached each stage. Groups younger than 14 days are still "
        "maturing.", section="funnel"),
    "active_customers": _m(
        "Active customers", "Customers who uploaded a video, sent an editor "
        "message, made an AI-app edit or exported, in the period.",
        polarity=UP, section="funnel"),
    "signed_in_customers": _m(
        "Signed in", "Customers who opened Valmera while signed in during "
        "the period. Only for periods that end now.", polarity=UP,
        section="funnel"),
    "exports": _m(
        "Videos exported", "Finished exports of customers' videos. A shorts "
        "set counts each video.", polarity=UP, section="funnel"),
    "exporters": _m(
        "People who exported", "Customers with at least one finished export "
        "in the period.", polarity=UP, section="funnel"),

    # ── D. Money ─────────────────────────────────────────────────────────
    "mrr": _m(
        "Monthly recurring revenue", "Monthly value of subscriptions Paddle "
        "says are active, at list price (yearly plans ÷ 12). Discounts and "
        "tax ignored.", unit="usd", polarity=UP, section="money"),
    "paying_now": _m(
        "Paying customers", "Customers whose subscription Paddle reports as "
        "active: the same people MRR counts.", polarity=UP, section="money"),
    "payment_failing": _m(
        "Payment failing", "Customers whose last charge failed while Paddle "
        "retries (past due or paused).", polarity=DOWN, section="money"),
    "cash": _m(
        "Money collected", "Successful payments in the period, in USD, "
        "including tax and before Paddle's fees.", unit="usd", polarity=UP,
        section="money"),
    "payments": _m(
        "Payments", "Number of successful payments in the period.",
        section="money"),
    "non_usd_payments": _m(
        "Payments in other currencies", "Successful payments in a currency "
        "other than USD. Not included in the dollar total.", section="money"),
    "new_paying": _m(
        "Started paying", "Customers whose first successful payment happened "
        "in the period.", polarity=UP, section="money"),
    "stopped_paying": _m(
        "Stopped paying", "Customers who were paying at the start of a day "
        "and not at its end, from daily billing snapshots.", polarity=DOWN,
        section="money"),
    "canceled_ever": _m(
        "Have cancelled", "Customers who paid at least once and whose "
        "subscription is not paying now.", polarity=DOWN, section="money"),
    "ever_paid": _m(
        "Ever paid", "Customers with at least one successful payment.",
        polarity=UP, section="money"),
    "avg_plan_value": _m(
        "Average plan value", "Monthly recurring revenue ÷ paying customers.",
        unit="usd", section="money"),
    "plan_rows": _m(
        "By plan", "Paying customers, monthly and yearly counts and monthly "
        "value per plan.", section="money"),
    "ledger_total": _m(
        "All money Paddle reported", "Every successful payment Paddle has "
        "reported, ever.", unit="usd", section="money"),
    "ledger_customers": _m(
        "From current customers", "Successful payments by customers (accounts "
        "since 6 Jul, not yours, not test accounts).", unit="usd",
        section="money"),
    "ledger_pre_relaunch": _m(
        "From accounts before 6 Jul", "Successful payments by accounts "
        "created before the relaunch.", unit="usd", section="money"),
    "ledger_unlinked": _m(
        "From deleted accounts", "Payments whose account no longer exists, "
        "most likely subscriptions to the previous product.", unit="usd",
        section="money"),
    "failed_payments": _m(
        "Failed payments", "Charges Paddle reported as failed in the period.",
        polarity=DOWN, section="money"),
    "billing_problems": _m(
        "Billing problems", "Accounts where our records and Paddle disagree, "
        "from the hourly check.", polarity=DOWN, section="money"),
    "mrr_history": _m(
        "MRR over time", "One point per day from the daily billing "
        "snapshots. Never rebuilt from today's state.", unit="usd",
        section="money"),
    "cash_series": _m(
        "Money collected per day or week", "Successful payments grouped by "
        "Dubai day, or Monday week for long periods.", unit="usd",
        section="money"),
    "model_cost": _m(
        "AI model cost", "What the AI models cost us in the period, priced "
        "per call.", unit="usd", polarity=DOWN, section="money"),
    "compute_cost": _m(
        "Editing servers (upper bound)", "The most the editing servers could "
        "have cost for the period's work.", unit="usd", polarity=DOWN,
        section="money"),
    "storage_cost": _m(
        "Storage per month", "Stored media × $0.015 per GB, split into "
        "customers' files and yours.", unit="usd", polarity=DOWN,
        section="money"),
    "database_cost": _m(
        "Database per month", "Fixed assumption: 5 GB plan × $0.30.",
        unit="usd", section="money"),
    "gross_margin": _m(
        "Gross margin", "(Money collected − costs) ÷ money collected, last 30 "
        "days.", unit="percent", polarity=UP, section="money"),

    # ── F. Product health ────────────────────────────────────────────────
    "customer_failures": _m(
        "Customer failures", "Customers' analyses, previews, exports, edit "
        "requests and shorts plans that failed with no later successful "
        "attempt.", polarity=DOWN, section="health"),
    "people_affected": _m(
        "People affected", "Different customers with at least one such "
        "failure.", polarity=DOWN, section="health"),
    "failed_uploads": _m(
        "Failed uploads", "People whose upload was refused or failed in the "
        "period, each counted once.", polarity=DOWN, section="health"),
    "payment_problems": _m(
        "Payment problems", "Customers whose payment is failing now, plus "
        "charges that failed in the period.", polarity=DOWN, section="health"),
    "messages_without_edit": _m(
        "Messages that didn't start an edit", "Customers' editor messages "
        "with no editing work started within 15 minutes.", polarity=DOWN,
        section="health"),
    "editing_engine": _m(
        "Editing engine", "Working: something finished or is running in the "
        "last 30 min. Waiting: work queued over 10 min. Idle: nothing to do.",
        section="health"),
    "stuck_jobs": _m(
        "Stuck work", "Customers' analyses, previews, exports or edits "
        "queued or running for more than 10 minutes.", polarity=DOWN,
        section="health"),
    "export_steps": _m(
        "Edit → download", "Clicked export, export started, render done, "
        "download link ready, download started.", section="health"),
    "upload_wait": _m(
        "Upload wait", "From pressing upload to the file being ready. "
        "Median for customers.", unit="seconds", polarity=DOWN,
        section="health"),
    "analysis_wait": _m(
        "Analysis wait", "Time to analyse the video, median. Reused analyses "
        "are left out.", unit="seconds", polarity=DOWN, section="health"),
    "edit_wait": _m(
        "Edit wait", "Median time to render a preview of a change.",
        unit="seconds", polarity=DOWN, section="health"),
    "projects_exported_share": _m(
        "Projects that ended with an export", "Customer projects created in "
        "the last 7 days with a finished export, out of all of them.",
        unit="percent", polarity=UP, section="health"),
}

HOW_MAX = 160


def definition(key):
    return REGISTRY[key]


def metric(key, value, previous=None, *, status="ok", note=None,
           breakdown=None, spark=None, href=None, comparable=True):
    """A Metric object (plan §6.4) carrying the registry copy for `key`."""
    d = REGISTRY[key]
    if value is None and status == "ok":
        status = "unavailable"
    return {"key": key, "label": d["label"], "how": d["how"],
            "unit": d["unit"], "polarity": d["polarity"],
            "value": value, "previous": previous if comparable else None,
            "status": status, "note": note,
            "breakdown": breakdown or [], "spark": spark or [],
            "href": href}


def error_metric(key, note="Couldn't load this number. Try again."):
    return metric(key, None, None, status="error", note=note)


def item(key, value):
    """A breakdown entry: {key, label, value}."""
    return {"key": key, "label": REGISTRY[key]["label"], "value": value}


def definitions():
    return {k: dict(v) for k, v in REGISTRY.items()}
