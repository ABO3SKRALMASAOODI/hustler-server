"""Real progress, varied content and scarce credits determine who gets mail."""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
os.environ.setdefault("SKIP_DB_INIT", "1")

from routes import newsletter as n
from routes import newsletter_content as content

NOW = datetime(2026, 9, 9, 15)


def person(uid=1, age=5, idle=1, **fields):
    return {"id": uid, "email": f"user{uid}@example.com", "credits_balance": 0,
            "created_at": NOW - timedelta(days=age),
            "last_active": NOW - timedelta(days=idle),
            "has_project": False, "has_edit": False, "has_export": False,
            "has_chat": False, "first_export_at": None, "sent_history": {},
            "last_contact_at": None, **fields}


def planned(row):
    return {"recipient": row, "topic": "welcome_activation", "campaign": "welcome_activation",
            "template": {"key": "welcome_activation", **content.DEFAULT_TEMPLATES["welcome_activation"]}}


def test_library_has_distinct_complete_messages_and_no_active_discount():
    active = [t for t in content.DEFAULT_TEMPLATES.values() if t["enabled"]]
    assert len(active) == 27
    assert len({t["subject"] for t in active}) == 27
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "preview-only-secret"
    with app.app_context():
        for key, template in content.DEFAULT_TEMPLATES.items():
            if not template["enabled"]:
                continue
            subject, html, unsub = n._render_for({"key": key, **template}, "reader+test@example.com", 0)
            text = content.plain_text(html)
            assert "{{" not in html
            assert "utm_campaign=" + key in html
            assert unsub in text
            assert "Contact support" in text
            assert "half price" not in text.lower()
            assert "free trial" not in text.lower()
            assert len(subject) <= 70
            assert len(html.encode()) < 50000


def test_mime_text_preserves_links_without_html_or_hidden_preview():
    html = content.wrap_email(content.p("Keep the useful point.") + content.cta("Open studio", "https://valmera.io/studio?a=1&amp;b=2"),
                              "https://example.com/unsubscribe?a=1&b=2", "Hidden inbox preview")
    text = content.plain_text(html)
    assert "Hidden inbox preview" not in text
    assert "Open studio (https://valmera.io/studio?a=1&b=2)" in text
    assert "https://example.com/unsubscribe?a=1&b=2" in text
    assert "<table" not in text


def test_service_messages_are_clear_and_do_not_opt_out_of_account_mail():
    verification = content.verification_email("123456")
    assert "123456" in verification["textContent"]
    assert "5 minutes" in verification["textContent"]
    payment = content.payment_email("Creator", "The bank declined the payment.", "https://example.com/pay?a=1&b=2", "https://valmera.io/account")
    assert "Payment retries may still occur" in payment["textContent"]
    assert "you won't be charged" not in payment["textContent"]
    for message in (verification, payment):
        assert "Unsubscribe" not in message["textContent"]
        assert "account-service email" in message["textContent"]


def test_dynamic_service_values_cannot_inject_html():
    mail = content.payment_email('<img src=x onerror="bad">', '<script>bad</script>', 'https://example.com/"x', "https://valmera.io/account")
    assert '<img src=x' not in mail["htmlContent"]
    assert '<script>' not in mail["htmlContent"]


def test_project_creation_is_not_mistaken_for_completed_editing():
    row = person(has_project=True)
    assert n._matches_campaign(row, "first_cut", NOW)
    assert not n._matches_campaign(row, "export_nudge", NOW)
    row["has_edit"] = True
    assert not n._matches_campaign(row, "first_cut", NOW)
    assert n._matches_campaign(row, "export_nudge", NOW)
    row["has_export"] = True
    assert not n._matches_campaign(row, "export_nudge", NOW)


def test_welcome_stops_once_the_user_starts_a_project():
    assert n._matches_campaign(person(), "welcome_activation", NOW)
    assert not n._matches_campaign(person(has_project=True), "welcome_activation", NOW)
    assert not n._matches_campaign(person(age=20), "welcome_activation", NOW)


def test_family_cooldown_honors_old_sends_after_copy_expands():
    row = person(sent_history={"welcome_activation": NOW - timedelta(days=1)})
    assert not n._matches_campaign(row, "first_prompt", NOW)
    row["sent_history"]["welcome_activation"] = NOW - timedelta(days=4)
    assert n._matches_campaign(row, "first_prompt", NOW)
    assert not n._matches_campaign(row, "welcome_activation", NOW)


def test_long_absence_sequence_ends_after_each_distinct_message():
    row = person(age=100, idle=60)
    for key in content.LIFECYCLE_FAMILIES["winback"]:
        row["sent_history"][key] = NOW - timedelta(days=30)
    assert not any(n._matches_campaign(row, key, NOW) for key in content.LIFECYCLE_FAMILIES["winback"])


def test_recent_export_message_requires_a_real_completed_export_time():
    assert not n._matches_campaign(person(has_project=True), "first_export", NOW)
    assert n._matches_campaign(person(first_export_at=NOW - timedelta(days=1)), "first_export", NOW)
    assert not n._matches_campaign(person(first_export_at=NOW - timedelta(days=10)), "first_export", NOW)


def test_recent_priority_still_guarantees_older_customers_a_share():
    rows = [planned(person(uid=i, age=5 + i / 1000)) for i in range(400)]
    rows += [planned(person(uid=i, age=60)) for i in range(400, 700)]
    selected = n._prioritize_recipients(rows, 280, NOW)
    assert len(selected) == 280
    assert sum(item["recipient"]["id"] < 400 for item in selected) == 210
    assert sum(item["recipient"]["id"] >= 400 for item in selected) == 70
    assert selected[0]["recipient"]["id"] == 0


@pytest.mark.parametrize("recent,older,expected_recent,expected_older", [(88, 300, 88, 192), (400, 10, 270, 10), (0, 400, 0, 280), (400, 0, 280, 0)])
def test_unused_quota_flows_to_the_other_cohort(recent, older, expected_recent, expected_older):
    rows = [planned(person(i, age=5)) for i in range(recent)]
    rows += [planned(person(1000 + i, age=60)) for i in range(older)]
    selected = n._prioritize_recipients(rows, 280, NOW)
    assert sum(x["recipient"]["id"] < 1000 for x in selected) == expected_recent
    assert sum(x["recipient"]["id"] >= 1000 for x in selected) == expected_older


def test_older_share_serves_the_least_recently_contacted_first():
    rows = [planned(person(1, age=60, last_contact_at=NOW - timedelta(days=8))),
            planned(person(2, age=60, last_contact_at=None)),
            planned(person(3, age=60, last_contact_at=NOW - timedelta(days=20)))]
    assert [x["recipient"]["id"] for x in n._prioritize_recipients(rows, 3, NOW)] == [2, 3, 1]


def test_weekly_topics_follow_each_person_and_ignore_old_date_only_copy():
    topics = {key: True for key in content.WEEKLY_ORDER}
    first = person(sent_history={"weekly-2026-W35": NOW - timedelta(days=14)})
    second = person(2, sent_history={"weekly-2026-W36:weekly_value": NOW - timedelta(days=7)})
    choices = n._weekly_choices(None, [first, second], topics, NOW)
    assert [key for _, key in choices] == ["weekly_value", "tip_pacing"]


def test_weekly_slot_dedupe_covers_both_legacy_and_topic_keys():
    for key in ("weekly-2026-W37", "weekly-2026-W37:tip_captions"):
        row = person(sent_history={key: NOW - timedelta(days=3)})
        assert not n._matches_campaign(row, "weekly_value", NOW, "weekly-2026-W37")
        assert n._matches_campaign(row, "weekly_value", NOW, "weekly-2026-W37-b")


def test_weekly_rotation_skips_paused_topics_and_does_not_repeat_too_soon():
    topics = {key: True for key in content.WEEKLY_ORDER if key != "weekly_value"}
    assert n._weekly_choices(None, [person()], topics, NOW)[0][1] == "tip_pacing"
    row = person(sent_history={f"weekly-2026-W37:{key}": NOW - timedelta(days=7) for key in topics})
    assert n._weekly_choices(None, [row], topics, NOW) == []


def test_planner_assigns_only_one_relevant_message_per_person(monkeypatch):
    monkeypatch.setattr(n, "get_all_templates", lambda _: {key: {"key": key, **t} for key, t in content.DEFAULT_TEMPLATES.items()})
    monkeypatch.setattr(n, "_campaign_audience", lambda _: [person(1), person(2, has_project=True), person(3, has_project=True, has_edit=True)])
    plan, _, _ = n._campaign_plan(None, NOW, {"weekly_enabled": True, "weekly_weekday": NOW.weekday()})
    assert [item["topic"] for item in plan] == ["welcome_activation", "first_cut", "export_nudge"]
    assert len({item["recipient"]["id"] for item in plan}) == len(plan)


def test_stale_database_override_cannot_revive_discount():
    assert not n._resolved_template("offer_50", {"enabled": True, "body_html": "old offer"})["enabled"]
