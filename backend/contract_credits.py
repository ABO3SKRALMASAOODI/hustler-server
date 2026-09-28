"""Monthly allowances for new contracts. Mirrored in worker/contract_credits.py.

Callers own the transaction. Lock the user before the cycle ledger, matching
credit debits, so concurrent polls/worker calls can never regrant spent credits.
"""
import calendar
from datetime import datetime, timezone

NEW_CONTRACTS = frozenset({'mcp_connect', 'advanced'})


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def month_at(anchor, count):
    number = anchor.year * 12 + anchor.month - 1 + count
    year, month = divmod(number, 12)
    month += 1
    return anchor.replace(year=year, month=month,
        day=min(anchor.day, calendar.monthrange(year, month)[1]))


def cycle_at(start, end, now):
    start, end, now = map(utc, (start, end, now))
    if not start <= now < end:
        return None
    return max(i for i in range(12) if month_at(start, i) <= now)


def record_paid_year(conn, user_id, subscription_id, transaction_id, plan,
                     start, end, allowance):
    if plan not in NEW_CONTRACTS:
        return
    start, end = utc(start), utc(end)
    if not subscription_id or not transaction_id or not 330 <= (end-start).days <= 367:
        raise ValueError('A complete paid annual billing period is required')
    with conn.cursor() as cur:
        cur.execute('SELECT id FROM users WHERE id = %s FOR UPDATE', (user_id,))
        cur.execute('''INSERT INTO subscription_credit_cycles
            (user_id, subscription_id, transaction_id, plan, period_start,
             period_end, monthly_credits, last_cycle)
            VALUES (%s,%s,%s,%s,%s,%s,%s,0)
            ON CONFLICT (user_id) DO UPDATE SET
              subscription_id=EXCLUDED.subscription_id,
              transaction_id=EXCLUDED.transaction_id, plan=EXCLUDED.plan,
              period_start=EXCLUDED.period_start, period_end=EXCLUDED.period_end,
              monthly_credits=EXCLUDED.monthly_credits, last_cycle=0
            WHERE EXCLUDED.period_start > subscription_credit_cycles.period_start''',
            (user_id, subscription_id, transaction_id, plan, start, end, allowance))


def refresh(conn, user_id, now=None):
    """Refresh due new-contract pools, never accumulate missed months."""
    now = utc(now or datetime.now(timezone.utc))
    with conn.cursor() as cur:
        cur.execute('''SELECT id, plan, is_subscribed, billing_status, billing_period,
            subscription_id, credits_daily_reset FROM users
            WHERE id=%s AND plan IN ('mcp_connect','advanced') FOR UPDATE''', (user_id,))
        user = cur.fetchone()
        if not user or user.get('plan') not in NEW_CONTRACTS or not user.get('is_subscribed'):
            return False
        changed = False
        # Keep the existing grace policy, but never refill a failed annual
        # renewal or pause merely because a scheduled monthly date passed.
        if user.get('billing_status') != 'active':
            return False
        daily_date = user.get('credits_daily_reset')
        if daily_date is None or str(daily_date)[:10] < now.date().isoformat():
            cur.execute('''UPDATE users SET credits_daily=20, credits_daily_reset=%s,
                credits_balance=20+COALESCE(credits_bonus,0)+COALESCE(credits_monthly,0)
                WHERE id=%s''', (now.date(), user_id))
            changed = True
        if user.get('billing_period') != 'yearly':
            return changed
        cur.execute('''SELECT * FROM subscription_credit_cycles
                       WHERE user_id=%s FOR UPDATE''', (user_id,))
        cycle = cur.fetchone()
        if not cycle or cycle['subscription_id'] != user['subscription_id'] or cycle['plan'] != user['plan']:
            return changed
        due = cycle_at(cycle['period_start'], cycle['period_end'], now)
        if due is None or due <= cycle['last_cycle']:
            return changed
        cur.execute('''UPDATE users SET credits_monthly=%s,
            credits_balance=COALESCE(credits_daily,0)+COALESCE(credits_bonus,0)+%s
            WHERE id=%s''', (cycle['monthly_credits'], cycle['monthly_credits'], user_id))
        cur.execute('UPDATE subscription_credit_cycles SET last_cycle=%s WHERE user_id=%s', (due,user_id))
        return True
