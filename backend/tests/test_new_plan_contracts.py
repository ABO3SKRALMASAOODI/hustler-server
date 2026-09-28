import pytest
from flask import Flask
from routes import paddle, mcp, video
from plan_catalog import available_plans, NEW_PLANS, PLAN_PRICES_USD, PLANS_LIVE

@pytest.mark.parametrize('plan', ['ai','ai_pro','ai_max','mcp','plus','pro','ultra','titan','ace'])
def test_current_subscribers_never_receive_new_offers(plan):
    assert not available_plans({'plan':plan,'is_subscribed':True}) & NEW_PLANS
    assert available_plans({'plan':plan,'is_subscribed':False}) == NEW_PLANS

@pytest.mark.parametrize('endpoint', ['/paddle/checkout-config','/paddle/create-checkout-session'])
@pytest.mark.parametrize('current,target,subscribed', [('ai','advanced',True),('ai_max','mcp_connect',True),('free','ai',False),('advanced','ai_max',True),('free','ultimate',False)])
def test_offer_boundary_fails_before_any_provider_mutation(monkeypatch,endpoint,current,target,subscribed):
    monkeypatch.setattr(paddle,'decode_token',lambda _: (7,'buyer@example.com'))
    monkeypatch.setattr(paddle,'_subscription_snapshot',lambda _: {'plan':current,'is_subscribed':subscribed,'subscription_id':'sub_legacy' if subscribed else None})
    monkeypatch.setattr(paddle.requests,'post',lambda *a,**k: pytest.fail('must not create provider transaction or discount'))
    app=Flask(__name__);app.register_blueprint(paddle.paddle_bp)
    result=app.test_client().post(endpoint,json={'plan':target},headers={'Authorization':'Bearer test'})
    assert result.status_code in (400,409)

@pytest.mark.parametrize('plan', ['mcp_connect','advanced'])
def test_new_plans_enable_verified_mcp_and_keep_annual_margin(monkeypatch,plan):
    monkeypatch.setattr(mcp,'ALLOWED_EMAILS',set())
    assert mcp.account_has_mcp_access({'plan':plan,'is_subscribed':True,'is_verified':True})
    assert not mcp.account_has_mcp_access({'plan':plan,'is_subscribed':False,'is_verified':True})
    annual_revenue=PLAN_PRICES_USD[plan]['yearly']/12
    full_month_cost=(PLANS_LIVE[plan]['monthly_credits'] + 31*20)*.005
    assert (annual_revenue-full_month_cost)/annual_revenue >= .4

@pytest.mark.parametrize('plan,blocked',[('mcp_connect',True),('advanced',False),('ai',False),('mcp',False)])
def test_mcp_only_blocks_hosted_ai_but_legacy_is_untouched(plan,blocked):
    class Cursor:
        def execute(self,*args):pass
        def fetchone(self):return {'plan':plan,'is_subscribed':True}
    app=Flask(__name__)
    with app.app_context():
        result=video._studio_ai_gate(Cursor(),1)
        assert bool(result) is blocked
        if blocked: assert result[0].get_json()['connect_url']=='/mcp/connect'
