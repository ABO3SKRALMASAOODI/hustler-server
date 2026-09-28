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


@pytest.mark.parametrize('endpoint', ['/paddle/checkout-config', '/paddle/create-checkout-session', '/paddle/change-plan'])
@pytest.mark.parametrize('current,target', [('mcp_connect','advanced'), ('advanced','mcp_connect')])
def test_new_annual_switch_preserves_prepaid_term_before_provider_mutation(monkeypatch, endpoint, current, target):
    monkeypatch.setattr(paddle,'paddle_headers',lambda: {})
    monkeypatch.setattr(paddle,'decode_token',lambda _: (7,'buyer@example.com'))
    monkeypatch.setattr(paddle,'_subscription_snapshot',lambda _: {'plan':current,'is_subscribed':True,'subscription_id':'sub_annual'})
    monkeypatch.setattr(paddle,'_new_plan_readiness',lambda _: None)
    class Response:
        status_code=200
        def json(self):
            return {'data': {'status':'active','billing_cycle':{'interval':'year'},
                'items':[{'price':{'id':paddle.PLANS[current]['yearly_price_id'], 'billing_cycle':{'interval':'year'}}}]}}
    monkeypatch.setattr(paddle.requests,'get',lambda *a,**k: Response())
    monkeypatch.setattr(paddle.requests,'post',lambda *a,**k: pytest.fail('must not create checkout or discount'))
    monkeypatch.setattr(paddle.requests,'patch',lambda *a,**k: pytest.fail('must not replace prepaid annual price'))
    app=Flask(__name__);app.register_blueprint(paddle.paddle_bp)
    result=app.test_client().post(endpoint,json={'plan':target,'billing':'yearly'},headers={'Authorization':'Bearer test'})
    assert result.status_code==409
    assert result.get_json()['code']=='annual_term_protected'


@pytest.mark.parametrize('plan,period', [('ai','yearly'),('ai_pro','yearly'),('ai_max','yearly'),('advanced','monthly'),('mcp_connect','monthly')])
def test_annual_guard_does_not_change_legacy_or_monthly_switches(plan,period):
    price=paddle.PLANS[plan]['yearly_price_id' if period=='yearly' else 'price_id']
    sub={'billing_cycle':{'interval':'year' if period=='yearly' else 'month'},'items':[{'price':{'id':price}}]}
    assert paddle._annual_contract_change_error({'plan':plan},sub) is None


@pytest.mark.parametrize("route,args", [
    (video.post_message, {"project_id":3}),
    (video.start_shorts, {"project_id":3}),
    (video.start_short_editor, {"project_id":3,"child_project_id":4}),
])
def test_mcp_hosted_ai_routes_refuse_before_project_or_queue_mutation(monkeypatch, route, args):
    from contextlib import contextmanager
    class Cursor:
        def execute(self, sql, *args):
            assert "SELECT plan, is_subscribed FROM users" in sql
        def fetchone(self): return {"plan":"mcp_connect","is_subscribed":True}
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def database(): yield Connection()
    monkeypatch.setattr(video, "vdb", database)
    monkeypatch.setattr(video, "_project_for_user", lambda *args: {"id":3})
    app=Flask(__name__)
    with app.test_request_context(json={"text":"Make an edit"}):
        response, status = route.__wrapped__(user_id=7, **args)
        assert status == 403
        assert response.get_json()["upgrade_url"] == "/subscribe"
        assert response.get_json()["code"] == "mcp_only"
