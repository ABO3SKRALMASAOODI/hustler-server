"""The visitor classes, the funnel group and every "not recorded" reason, run
on real PostgreSQL with temp tables (CI only: needs QUEUE_TEST_DATABASE_URL).
"""
import os
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import pytest
import psycopg2
from psycopg2.extras import RealDictCursor

from admin_metrics import channels_report, db, funnel, ranges, visitors

URL = os.getenv("QUEUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated CI PostgreSQL")

SCHEMA = """
CREATE TEMP TABLE users(id int PRIMARY KEY, email text, is_verified int,
  created_at timestamp, is_subscribed int DEFAULT 0, billing_status text,
  trial_status text, plan text, billing_plan text, billing_period text,
  last_seen_at timestamptz);
CREATE TEMP TABLE page_visits(id serial, device_id text, analytics_id uuid,
  page text, visited_at timestamp, referrer text, attribution jsonb,
  user_agent text, time_on_page int, scroll_depth int,
  last_seen_at timestamp);
CREATE TEMP TABLE website_events(id uuid, visit_id uuid, kind text,
  created_at timestamp);
CREATE TEMP TABLE projects(id int, user_id int, chat_session_id int,
  parent_project_id int, created_at timestamptz, kind text, title text);
CREATE TEMP TABLE assets(id int, project_id int, kind text,
  created_at timestamptz);
CREATE TEMP TABLE chat_messages(id int, session_id int, role text,
  meta jsonb, created_at timestamp);
CREATE TEMP TABLE video_jobs(id int, user_id int, project_id int, type text,
  state text, payload jsonb, created_at timestamptz, updated_at timestamptz);
CREATE TEMP TABLE payments(id int, user_id int, status text,
  amount_cents bigint, currency text, occurred_at timestamp,
  created_at timestamp);
CREATE TEMP TABLE client_events(id int, user_id int, project_id int,
  kind text, detail jsonb, created_at timestamptz);
CREATE TEMP TABLE website_signups(user_id int, device_id text,
  session_id text, attribution jsonb, tracking text, completed_at timestamp);
CREATE TEMP TABLE onboarding_responses(user_id int, channel text,
  skipped boolean DEFAULT false);
"""

CODE = "AbcdEFgh1234_567"
DAY = "2026-10-09 10:00:00"          # Dubai day 9 Oct


@pytest.fixture
def cur():
    assert urlparse(URL).hostname in {"localhost", "127.0.0.1", "postgres"}
    conn = psycopg2.connect(URL, cursor_factory=RealDictCursor)
    db.reset_features()
    try:
        with conn.cursor() as c:
            c.execute(SCHEMA)
            yield c
    finally:
        conn.rollback()
        conn.close()
        db.reset_features()


def visit(c, device, page="/", ua="Mozilla/5.0 (iPhone)", ref="", code=None,
          active=0, scroll=0, at=DAY):
    att = None
    if code:
        att = ('{"first":{"source":"instagram","medium":"outreach",'
               f'"code":"{code}"}},"last":null}}')
    c.execute("""INSERT INTO page_visits(device_id, analytics_id, page,
                   visited_at, referrer, attribution, user_agent, time_on_page,
                   scroll_depth)
                 VALUES (%s, gen_random_uuid(), %s, %s, %s, %s::jsonb, %s, %s, %s)
                 RETURNING analytics_id""",
              (device, page, at, ref, att, ua, active, scroll))
    return c.fetchone()["analytics_id"]


def test_each_browser_gets_exactly_one_class(cur):
    visit(cur, "preview_00001", ref="www.facebook.com", code=CODE)
    visit(cur, "preview_00031", ref="m.facebook.com", code=CODE, active=31)
    visit(cur, "scroller_0001", scroll=40)
    visit(cur, "twopage_00001")
    visit(cur, "twopage_00001", page="/pricing")
    visit(cur, "reader_000001", active=45)
    visit(cur, "robot_0000001", ua="Mozilla/5.0 (compatible; Googlebot/2.1)")
    visit(cur, "tool_00000001", ua="Mozilla/5.0 GoogleOther", scroll=10)
    visit(cur, "owner_0000001", page="/admin", scroll=50)
    clicked = visit(cur, "clicker_00001")
    cur.execute("""INSERT INTO website_events VALUES (gen_random_uuid(), %s,
                   'signup_cta', %s)""", (clicked, DAY))
    visit(cur, "idle_00000001", active=5)
    # A proof section scrolled into view on load fires by itself: not a click.
    viewer = visit(cur, "viewer_000001")
    cur.execute("""INSERT INTO website_events VALUES (gen_random_uuid(), %s,
                   'proof_view', %s)""", (viewer, DAY))
    visit(cur, "codedhuman001", ref="www.facebook.com", code=CODE, scroll=20)
    period = ranges.make_period("custom", date(2026, 10, 9), date(2026, 10, 9),
                                now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    counts = visitors.classify(cur, period)
    assert {k: counts[k] for k in visitors.CLASSES} == {
        "person": 5, "link_preview": 2, "no_signal": 2, "robot": 2,
        "internal": 1}
    assert counts["browsers"] == 12
    by_day = visitors.classify_by_day(cur, period)
    assert by_day[date(2026, 10, 9)]["person"] == 5
    people = {r["device_id"] for r in visitors.people_rows(cur, period)}
    assert people == {"scroller_0001", "twopage_00001", "reader_000001",
                      "clicker_00001", "codedhuman001"}


def test_stopped_paying_bridges_a_day_without_snapshots(cur, monkeypatch):
    from admin_metrics import money
    cur.execute("""
      CREATE TEMP TABLE billing_daily_status(day date, user_id int,
        paying boolean, monthly_value_cents int DEFAULT 0,
        PRIMARY KEY (day, user_id));
      -- Three paying on 1 Oct; the billing tick never ran on 2 Oct; on 3 Oct
      -- user 2 cancelled and user 3's account is gone; 4 Oct (today) has no
      -- snapshot yet.
      INSERT INTO billing_daily_status(day, user_id, paying) VALUES
        ('2026-10-01', 1, true), ('2026-10-01', 2, true),
        ('2026-10-01', 3, true), ('2026-10-03', 1, true),
        ('2026-10-03', 2, false);""")
    monkeypatch.setattr(money, "snapshots_since",
                        lambda c: date(2026, 9, 30))
    now = datetime(2026, 10, 4, 6, tzinfo=timezone.utc)

    def stopped(first, last):
        return money.stopped_paying(
            cur, ranges.make_period("custom", first, last, now=now))
    assert stopped(date(2026, 10, 2), date(2026, 10, 3)) == 2
    assert stopped(date(2026, 10, 2), date(2026, 10, 2)) == 0
    assert stopped(date(2026, 10, 4), date(2026, 10, 4)) == 0


def test_funnel_counts_the_group_that_signed_up_in_the_period(cur):
    cur.execute("""
      INSERT INTO users(id, email, is_verified, created_at) VALUES
        (1, 'a@example.com', 1, '2026-10-02'), (2, 'b@example.com', 1, '2026-10-03'),
        (3, 'c@example.com', 1, '2026-10-04'), (4, 'd@example.com', 1, '2026-10-05'),
        (5, 'thevalmera@gmail.com', 1, '2026-10-05'),
        (6, 'old@example.com', 1, '2026-06-01'),
        (7, 'unverified@example.com', 0, '2026-10-05');
      INSERT INTO projects VALUES (10, 1, 100, NULL, now(), 'edit', 'a'),
                                  (20, 2, 200, NULL, now(), 'edit', 'b'),
                                  (50, 5, 500, NULL, now(), 'edit', 'o');
      INSERT INTO assets VALUES (1, 10, 'original', now()), (2, 20, 'original', now()),
                                (5, 50, 'original', now());
      INSERT INTO chat_messages VALUES (1, 100, 'user', NULL, now()),
                                       (5, 500, 'user', NULL, now());
      INSERT INTO video_jobs VALUES
        (1, 1, 10, 'final', 'done', '{}', now(), now()),
        (2, 3, 30, 'mcp_tool', 'done', '{"mutation":"true"}', now(), now()),
        (3, 4, 40, 'mcp_tool', 'done', '{"mutation":"false"}', now(), now()),
        (5, 5, 50, 'final', 'done', '{}', now(), now());
      INSERT INTO payments VALUES
        (1, 1, 'completed', 1500, 'USD', '2026-10-06', '2026-10-06'),
        (2, 2, 'completed', 0, 'USD', '2026-10-06', '2026-10-06'),
        (5, 5, 'paid', 5000, 'USD', '2026-10-06', '2026-10-06');
    """)
    period = ranges.make_period("custom", date(2026, 10, 1), date(2026, 10, 9),
                                now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    # User 2 uploaded but never asked; user 3 asked from an AI app without
    # uploading: "lost" is who stopped, not uploaded − asked_edit (0).
    assert funnel.stage_counts(cur, period) == {
        "signed_up": 4, "uploaded": 2, "asked_edit": 2, "exported": 1,
        "paid": 1, "lost": {"uploaded": 2, "asked_edit": 1, "exported": 1,
                            "paid": 0}}


def test_every_signup_without_a_source_has_a_reason(cur, monkeypatch):
    monkeypatch.setattr(db, "has_column", lambda c, t, col:
                        (t, col) == ("website_signups", "tracking"))
    cur.execute("""
      INSERT INTO users(id, email, is_verified, created_at) VALUES
        (1, 's1@example.com', 1, '2026-10-08'), (2, 's2@example.com', 1, '2026-10-08'),
        (3, 's3@example.com', 1, '2026-10-08'), (4, 's4@example.com', 1, '2026-09-15'),
        (5, 's5@example.com', 1, '2026-10-08'), (6, 's6@example.com', 1, '2026-10-08'),
        (7, 's7@example.com', 1, '2026-10-08');
      INSERT INTO website_signups(user_id, device_id, session_id, attribution, tracking) VALUES
        (1, 'dev_000000001', 'ses_00000001',
         '{"first":{"source":"www.google.com","medium":"organic","at":1},"last":null}', 'tracked'),
        (2, 'dev_000000002', 'ses_00000002', '{"first":null,"last":null}', 'tracked'),
        (5, NULL, NULL, NULL, 'privacy_signal'),
        (6, NULL, NULL, NULL, 'no_identity'),
        (7, 'dev_000000007', 'ses_00000007',
         '{"first":{"source":"chatgpt.com","medium":"estimated","at":1},"last":null}',
         'estimated_from_referrer');
      INSERT INTO onboarding_responses VALUES (4, 'ai_chatbot', false);
    """)
    period = ranges.make_period("custom", date(2026, 9, 1), date(2026, 10, 9),
                                now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    rows = channels_report.classify_signups(cur, period)
    cov = channels_report.coverage(rows)
    assert (cov["signups"], cov["tracked"], cov["estimated"]) == (7, 1, 1)
    assert {r["reason"]: r["signups"] for r in cov["not_recorded"]} == {
        "before_tracking": 1, "privacy_browser": 1, "nothing_sent": 1,
        "landing_lost": 1, "no_row_unknown": 1}
    by_id = {r["id"]: r["touch"] for r in rows}
    assert by_id[1]["channel"] == "search"
    assert by_id[7]["channel"] == "ai_assistant" and by_id[7]["estimated"]
    before = channels_report.before_tracking(rows)
    assert before["told"][0]["told"] == "ai_chatbot"
