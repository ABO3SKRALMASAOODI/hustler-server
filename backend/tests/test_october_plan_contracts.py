"""New offers must never rewrite an older paid contract."""
import pytest
from flask import Flask
from plan_catalog import (PLANS_LIVE, PLAN_PRICES_USD, NEW_PLANS,
                          available_plans, daily_credits_for_plan)
from routes import paddle, paddle_webhook as webhook, mcp, video
import credits

@pytest.mark.parametrize('plan,price,allowance', [
    ('plus_2026',25,500),('pro_2026',50,1500),('max_2026',100,4000)])
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
        ('plus_2026',25,500),('pro_2026',50,1500),('max_2026',100,4000)]
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
