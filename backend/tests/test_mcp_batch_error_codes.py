import pytest
from routes import mcp


@pytest.mark.parametrize('status,body,expected', [
    (400, {'error':'Unknown operation banana'}, 'invalid_request'),
    (409, {'error':'The edit changed.'}, 'version_conflict'),
    (409, {'error':'Another editor is working', 'blocking_job_id':42}, 'prerequisite'),
    (402, {'error':'A subscription is required'}, 'plan_gate'),
    (500, {'error':'The edit could not be checked', 'code':'batch_validation_fault'}, 'internal_error'),
])
def test_batch_errors_keep_their_class_without_customer_text(monkeypatch,status,body,expected):
    monkeypatch.setattr(mcp,'apply_edit_batch_core',lambda *a,**kw:(dict(body),status))
    out=mcp._run_tool_job({'user_id':1},'apply_edit_batch',{},raw=True,project_id=2)
    public={'isError':True,'content':[{'type':'text','text':out['text']}],
            'structuredContent':mcp._editor_structured_content(out)}
    detail=mcp._public_error_detail('apply_edit_batch',public)
    assert detail['category']==expected
    if status < 500:
        assert 'message' not in detail
    else:
        assert 'message' in detail
    assert out['edl_changed'] is False
