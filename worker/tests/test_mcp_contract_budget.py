from types import SimpleNamespace
import pytest
import mcp_exec, db, llm

@pytest.mark.parametrize('plan',['mcp_connect','advanced'])
def test_new_mcp_call_uses_fresh_balance_not_previous_calls_usage(plan):
    ctx=SimpleNamespace(plan=plan,model_usage={'old':{}},tokens_in=1000,tokens_out=200,
        tokens_cached_in=500,images_generated=['old'],gen_extra_cost_usd=3,credit_budget=500)
    class DB:
        def run(self,fn,*a):
            if fn is db.user_billing:return True,plan,False
            if fn is db.user_credits_balance:return 12
            raise AssertionError(fn)
    mcp_exec._refresh_contract_budget(ctx,DB(),{'user_id':1})
    assert ctx.credit_budget==12
    assert ctx.model_usage=={} and ctx.tokens_in==0 and ctx.tokens_out==0
    assert ctx.images_generated==[] and ctx.gen_extra_cost_usd==0


def test_legacy_mcp_does_not_gain_a_new_credit_charge_or_budget():
    ctx=SimpleNamespace(plan='ai_pro',credit_budget=None)
    class DB:
        def run(self,fn,*a):
            assert fn is db.user_billing
            return True,'ai_pro',False
    mcp_exec._refresh_contract_budget(ctx,DB(),{'user_id':1})
    assert ctx.credit_budget is None


def test_cached_new_contract_loses_generation_budget_after_cancellation(monkeypatch):
    ctx=SimpleNamespace(plan='advanced',credit_budget=8000)
    class DB:
        def run(self,fn,*a):return False,'free',False
    monkeypatch.setattr(llm,'agent_client_for',lambda *a:(None,'free'))
    mcp_exec._refresh_contract_budget(ctx,DB(),{'user_id':1})
    assert ctx.credit_budget==0
