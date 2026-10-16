"""New offers must never rewrite an older paid contract."""
import pytest
from flask import Flask
from plan_catalog import (PLANS_LIVE, PLAN_PRICES_USD, NEW_PLANS,
                          available_plans, daily_credits_for_plan)
from routes import paddle, paddle_webhook as webhook, mcp, video
import credits

@pytest.mark.parametrize('plan,price,allowance', [
    ('plus_2026',15,500),('pro_2026',50,1500),('max_2026',100,4000)])
def test_new_offer_checkout_grant_and_reporting_agree(monkeypatch,plan,price,allowance):
    assert PLAN_PRICES_USD[plan] == {'monthly':price,'yearly':price*10}
    assert PLANS_LIVE[plan]['monthly_credits'] == allowance
    assert daily_credits_for_plan(plan) == 0
    for key in ('price_id','yearly_price_id'):
        price_id=PLANS_LIVE[plan][key]
        assert webhook.PRICE_TO_PLAN[price_id] == plan
        assert webhook.credits_for_price(price_id, 'ace') == allowance
        data={'items':[{'price':{'id':price_id}}]}
        assert webhook._trial_aware_grant(7,plan,'sub_new','transaction.completed',data,payment_grant=True)[:3] == (allowance,0,False)
    monkeypatch.setattr(paddle,'decode_token',lambda _: (7,'buyer@example.com'))
    monkeypatch.setattr(paddle,'_subscription_snapshot',lambda _: {'is_subscribed':False,'plan':'free'})
    monkeypatch.setattr(paddle,'_new_plan_readiness',lambda _: None)
    app=Flask(__name__);app.register_blueprint(paddle.paddle_bp)
    for period,key in [('monthly','price_id'),('yearly','yearly_price_id')]:
        r=app.test_client().post('/paddle/checkout-config',json={'plan':plan,'billing':period})
        assert r.status_code==200
        assert r.json['price_id']==PLANS_LIVE[plan][key]
        assert r.json['price_usd']==PLAN_PRICES_USD[plan][period]
    assert mcp.account_has_mcp_access({'plan':plan,'is_subscribed':True,'is_verified':True})

@pytest.mark.parametrize('plan,allowance', [('mcp_connect',1500),('advanced',8000),('ai',1000),('ai_pro',2000),('ai_max',5000),('plus',800),('pro',2400)])
def test_legacy_prices_allowances_daily_grants_and_offer_cohort_are_unchanged(plan,allowance):
    assert PLANS_LIVE[plan]['monthly_credits']==allowance
    assert daily_credits_for_plan(plan)==20
    active={'plan':plan,'is_subscribed':True,'scheduled_cancel_at':'2026-11-13'}
    assert not available_plans(active) & NEW_PLANS
    assert available_plans({**active,'is_subscribed':False})==NEW_PLANS
    if plan in {'mcp_connect','advanced'}:
        assert available_plans(active)=={'mcp_connect','advanced'}

def test_upgrade_offer_includes_all_three_buyable_plans():
    offer=credits.subscribe_offer_body()
    assert [(p['id'],p['monthly'],p['credits']) for p in offer['plans']] == [
        ('plus_2026',15,500),('pro_2026',50,1500),('max_2026',100,4000)]
    assert not offer['comparison_plans']

@pytest.mark.parametrize('current',['mcp_connect','advanced','ai','ai_pro','ai_max'])
@pytest.mark.parametrize('target',sorted(NEW_PLANS))
def test_existing_subscribers_cannot_accidentally_buy_new_contract(monkeypatch,current,target):
    monkeypatch.setattr(paddle,'decode_token',lambda _: (7,'buyer@example.com'))
    monkeypatch.setattr(paddle,'_subscription_snapshot',lambda _: {'plan':current,'is_subscribed':True,'subscription_id':'sub_existing'})
    monkeypatch.setattr(paddle.requests,'post',lambda *a,**k: pytest.fail('must not create billing objects'))
    app=Flask(__name__);app.register_blueprint(paddle.paddle_bp)
    result=app.test_client().post('/paddle/checkout-config',json={'plan':target})
    assert result.status_code==409 and result.json['code']=='plan_not_available'


def test_staged_rollout_keeps_previous_frontend_offers_usable(monkeypatch):
    import catalog_rollout
    monkeypatch.setattr(catalog_rollout,'shopfront_held',lambda: True)
    assert available_plans({'is_subscribed':False})==NEW_PLANS | {'mcp_connect','advanced'}
    assert [p['id'] for p in credits.subscribe_offer_body()['plans']]==['mcp_connect','advanced']
    assert available_plans({'is_subscribed':True,'plan':'advanced'})=={'mcp_connect','advanced'}
    monkeypatch.setattr(catalog_rollout,'shopfront_held',lambda: False)
    assert available_plans({'is_subscribed':False})==NEW_PLANS
    assert [p['id'] for p in credits.subscribe_offer_body()['plans']]==['plus_2026','pro_2026','max_2026']


# 16 Oct 2026: Plus moved from $25/$250 to $15/$150 on new Paddle prices.
PLUS_LAUNCH_MONTHLY = 'pri_01m4tq08e68mg9t3n4f86qwhb3'
PLUS_LAUNCH_YEARLY = 'pri_01m4tq08na6vh0x92pfk573f9e'


def test_plus_sells_only_the_new_price_and_never_the_launch_price():
    from plan_catalog import is_yearly_price
    plus = PLANS_LIVE['plus_2026']
    assert plus['price_id'] == 'pri_01m531e69sp4td4sh65gabj3ng'
    assert plus['yearly_price_id'] == 'pri_01m531e6r0cyx7v9eap1shk6v0'
    assert PLUS_LAUNCH_MONTHLY not in (plus['price_id'], plus['yearly_price_id'])
    assert PLAN_PRICES_USD['plus_2026'] == {'monthly': 15, 'yearly': 150}
    assert is_yearly_price('plus_2026', plus['yearly_price_id'])
    assert not is_yearly_price('plus_2026', plus['price_id'])


@pytest.mark.parametrize('price_id,yearly', [(PLUS_LAUNCH_MONTHLY, False), (PLUS_LAUNCH_YEARLY, True)])
def test_launch_price_plus_subscriptions_keep_their_contract(price_id, yearly):
    """A subscription bought at $25/$250 keeps resolving to the same Plus
    contract, allowance, annual refill schedule and billing period."""
    from plan_catalog import is_yearly_price
    assert webhook.PRICE_TO_PLAN[price_id] == 'plus_2026'
    assert webhook.credits_for_price(price_id, 'ace') == 500
    assert paddle._plan_from_price(price_id) == 'plus_2026'
    assert is_yearly_price('plus_2026', price_id) is yearly
    data = {'items': [{'price': {'id': price_id}}]}
    assert webhook._trial_aware_grant(7, 'plus_2026', 'sub_launch', 'transaction.completed',
                                      data, payment_grant=True)[:3] == (500, 0, False)


def test_launch_annual_plus_payment_seeds_the_monthly_refill_schedule(monkeypatch):
    recorded = []
    import contract_credits
    monkeypatch.setattr(contract_credits, 'record_paid_year', lambda *a: recorded.append(a))
    monkeypatch.setattr(webhook, 'get_db', lambda: type('C', (), {'commit': lambda self: None})())
    data = {'id': 'txn_launch', 'items': [{'price': {'id': PLUS_LAUNCH_YEARLY}}],
            'billing_period': {'starts_at': '2026-10-15T00:00:00Z', 'ends_at': '2027-10-15T00:00:00Z'}}
    webhook._record_new_annual_period(7, 'plus_2026', 'sub_launch', data)
    assert len(recorded) == 1 and recorded[0][4] == 'plus_2026' and recorded[0][-1] == 500
    recorded.clear()
    webhook._record_new_annual_period(7, 'plus_2026', 'sub_launch',
                                      {**data, 'items': [{'price': {'id': PLUS_LAUNCH_MONTHLY}}]})
    assert recorded == []


@pytest.mark.parametrize('plan', sorted(NEW_PLANS))
def test_fixed_allowance_offers_keep_their_margin_at_full_usage(plan):
    """Worst case: every credit spent, plus a 5 GB R2 reserve. Plus at $15
    ($12.50/month on annual) still clears the 40% floor the other plans keep."""
    import model_prices
    storage_reserve = 5 * 0.015
    metered = PLANS_LIVE[plan]['monthly_credits'] * model_prices.USD_PER_CREDIT
    assert daily_credits_for_plan(plan) == 0
    for revenue in (PLAN_PRICES_USD[plan]['monthly'], PLAN_PRICES_USD[plan]['yearly'] / 12):
        assert (revenue - metered - storage_reserve) / revenue >= .4, (plan, revenue)
