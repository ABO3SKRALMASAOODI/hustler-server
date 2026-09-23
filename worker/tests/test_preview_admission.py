import pytest
import config
import remote


def job(kind='preview'):
    return dict(id=39852,total_claims=1,project_id=2463,type=kind,payload={})


def test_busy_preview_uses_another_bounded_slot_before_waiting(monkeypatch):
    seen=[]
    def run(j):
        seen.append(remote._cloudflare_call_id(j))
        if len(seen)<3: raise remote.CloudflareCapacityBusy('shard is busy')
        return {'done':True}
    monkeypatch.setattr(remote,'_run_cloudflare',run)
    monkeypatch.setattr(remote.time,'sleep',lambda _: pytest.fail('should try free slots'))
    assert remote._run_cloudflare_with_capacity_wait(job()) == {'done':True}
    assert len(set(seen)) == 3
    assert seen[0].startswith('cf-preview-p2463-')
    assert seen[1].startswith('cf-alt1-preview-p2463-')
    assert seen[2].startswith('cf-alt2-preview-p2463-')


@pytest.mark.parametrize('error',[remote.RemoteExecutorError('ambiguous'),
    remote.CloudflareLaunchUnavailable('not proven busy')])
def test_ambiguous_call_never_moves_to_another_slot(monkeypatch,error):
    j=job()
    def run(_): raise error
    monkeypatch.setattr(remote,'_run_cloudflare',run)
    with pytest.raises(type(error)):
        remote._run_cloudflare_with_capacity_wait(j)
    assert '_cloudflare_admission_slot' not in j


def test_busy_admission_is_finite_and_mcp_keeps_affinity(monkeypatch):
    monkeypatch.setattr(config,'CLOUDFLARE_BUSY_WAIT_S',0)
    seen=[]
    def run(j):
        seen.append(remote._cloudflare_call_id(j))
        raise remote.CloudflareCapacityBusy('busy')
    monkeypatch.setattr(remote,'_run_cloudflare',run)
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote._run_cloudflare_with_capacity_wait(job())
    assert len(seen)==3
    seen.clear()
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote._run_cloudflare_with_capacity_wait(job('mcp_tool'))
    assert len(seen)==1
