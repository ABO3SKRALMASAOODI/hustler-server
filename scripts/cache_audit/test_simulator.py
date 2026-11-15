from simulator import Prompt, Simulator, calibration


def test_legacy_blocks_key_and_expiry():
    sim=Simulator();seq=tuple(range(1300))
    assert sim.run(Prompt(0,'gpt-4.1','a',seq,key='a'))['cached']==0
    assert sim.run(Prompt(1,'gpt-4.1','a',seq,key='a'))['cached']==1280
    assert sim.run(Prompt(2,'gpt-4.1','a',seq,key='b'))['cached']==0
    assert sim.run(Prompt(1000,'gpt-4.1','a',seq,key='a'))['cause']=='expired'
    assert sim.run(Prompt(1001,'gpt-4.1','a',seq[:900],key='a'))['cached']==0


def test_modern_short_reads_slide_but_long_lifetime_does_not_restart():
    sim=Simulator();seq=tuple(range(2000))
    def p(at):return Prompt(at,'gpt-6-sol','a',seq,modern=True,boundaries={1100:43200,1500:1800})
    r=sim.run(p(0));assert r['write_12h']==1100 and r['write_30m']==400
    assert sim.run(p(1700))['cached']==1500
    assert sim.run(p(1801))['cached']==1500
    assert sim.run(p(4000))['cached']==1100
    # New schemas or prefix data prevent reuse.
    assert sim.run(Prompt(1802,'gpt-6-sol','a',(-1,)+seq[1:],modern=True,boundaries={1100:43200}))['cached']==0


def test_concurrent_miss_and_no_padding():
    sim=Simulator();seq=tuple(range(1200))
    p=Prompt(0,'gpt-6-sol','a',seq,modern=True,boundaries={1100:1800},ready_at=10)
    sim.run(p)
    assert sim.run(Prompt(2,'gpt-6-sol','a',seq,modern=True,boundaries={1100:1800},ready_at=12))['cause']=='concurrent_cold_write'
    assert sim.run(Prompt(11,'gpt-6-sol','a',seq,modern=True,boundaries={1100:1800}))['cached']==1100
    assert sim.run(Prompt(20,'gpt-6-sol','a',seq,modern=True,boundaries={500:43200}))['cached']==0


def test_calibration_cannot_pass_missing_data_or_offsetting_errors():
    p=Prompt(0,'gpt-6-sol','a',(),actual=1000,reported_input=2000,complete=False)
    assert not calibration([p],[{'cached':1000}])['passed']
    p.complete=True
    assert calibration([p],[{'cached':1000}])['passed']
    assert not calibration([p,p],[{'cached':500},{'cached':1500}])['passed']


def test_short_to_long_upgrade_rebills_at_long_write_rate():
    sim=Simulator();seq=tuple(range(2000))
    sim.run(Prompt(0,'gpt-6-sol','a',seq,modern=True,boundaries={1500:1800}))
    upgraded=sim.run(Prompt(1,'gpt-6-sol','a',seq,modern=True,boundaries={1500:43200}))
    assert upgraded['cached']==0 and upgraded['write_12h']==1500
    read=sim.run(Prompt(1900,'gpt-6-sol','a',seq,modern=True,boundaries={1500:1800}))
    assert read['cached']==1500 and read['write_12h']==0
