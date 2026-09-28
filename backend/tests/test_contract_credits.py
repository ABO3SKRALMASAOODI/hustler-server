from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import pytest
import contract_credits as credits
from routes import paddle_webhook as webhook


def date(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class DB:
    def __init__(self, **user):
        self.user = dict(id=1, plan='advanced', is_subscribed=1, billing_status='active',
            billing_period='yearly', subscription_id='sub_paid', credits_daily_reset=date('2026-10-28').date(),
            credits_monthly=100, credits_daily=5, credits_bonus=3, **user)
        self.cycle = dict(subscription_id='sub_paid', plan='advanced', period_start=date('2026-09-28'),
            period_end=date('2027-09-28'), monthly_credits=Decimal('8000'), last_cycle=0)
        self.queries=[]
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args):
        self.queries.append(sql)
        if sql.startswith('UPDATE users SET credits_monthly'):
            self.user['credits_monthly']=args[0]
        elif sql.startswith('UPDATE users SET credits_daily'):
            self.user.update(credits_daily=20, credits_daily_reset=args[0])
        elif sql.startswith('UPDATE subscription_credit_cycles SET last_cycle'):
            self.cycle['last_cycle']=args[0]
    def fetchone(self):
        return self.cycle if 'FROM subscription_credit_cycles' in self.queries[-1] else self.user


def test_annual_pool_refills_once_without_rolling_over_or_regranting_spend():
    db=DB()
    assert credits.refresh(db,1,date('2026-10-28'))
    assert db.user['credits_monthly']==8000 and db.cycle['last_cycle']==1
    db.user['credits_monthly']=7300
    assert not credits.refresh(db,1,date('2026-10-28T02:00:00'))
    assert db.user['credits_monthly']==7300
    assert credits.refresh(db,1,date('2027-02-28'))
    assert db.user['credits_monthly']==8000 and db.cycle['last_cycle']==5
    assert 'FOR UPDATE' in db.queries[0]


@pytest.mark.parametrize('change', [dict(plan='ai_max'),dict(is_subscribed=0),dict(billing_status='past_due'),dict(billing_status='paused'),dict(subscription_id='sub_replaced'),dict(billing_period='monthly')])
def test_no_annual_grant_for_legacy_canceled_unpaid_or_replaced_contract(change):
    db=DB();db.user.update(change)
    credits.refresh(db,1,date('2026-10-28'))
    assert db.user['credits_monthly']==100


def test_annual_period_end_does_not_create_a_thirteenth_pool():
    db=DB();db.user['credits_daily_reset']=date('2027-09-28').date()
    credits.refresh(db,1,date('2027-09-28'))
    assert db.user['credits_monthly']==100


def test_calendar_months_recover_day_31_after_february_and_respect_leap_year():
    anchor=date('2028-01-31T12:34:56')
    assert credits.month_at(anchor,1)==date('2028-02-29T12:34:56')
    assert credits.month_at(anchor,2)==date('2028-03-31T12:34:56')
    assert credits.cycle_at(anchor,credits.month_at(anchor,12),date('2028-02-29T12:34:55'))==0


def test_webhook_uses_paid_price_not_client_billing_label(monkeypatch):
    calls=[]
    class Conn:
        def commit(self):pass
    monkeypatch.setattr(webhook,'get_db',lambda:Conn())
    monkeypatch.setattr(credits,'record_paid_year',lambda *a:calls.append(a))
    data={'id':'txn_paid','billing_period':{'starts_at':'2026-09-28T00:00:00Z','ends_at':'2027-09-28T00:00:00Z'},
          'custom_data':{'billing':'monthly'}, 'items':[{'price':{'id':webhook._PADDLE_PLANS['advanced']['yearly_price_id']}}]}
    webhook._record_new_annual_period(1,'advanced','sub_paid',data)
    assert calls[0][1:5]==(1,'sub_paid','txn_paid','advanced')
    assert calls[0][-1]==8000
    data['items'][0]['price']['id']=webhook._PADDLE_PLANS['advanced']['price_id']
    webhook._record_new_annual_period(1,'advanced','sub_paid',data)
    assert len(calls)==1


def test_backend_and_worker_contract_accounting_stay_identical():
    root=Path(__file__).resolve().parents[2]
    assert (root/'backend/contract_credits.py').read_bytes()==(root/'worker/contract_credits.py').read_bytes()
