"""Repeated pixels remain a defect; rendering again is not a type repair."""
from types import SimpleNamespace
import agent_tools
import render_qc


def test_measured_type_remains_actionable_across_repeated_previews():
    ctx=SimpleNamespace(first_cut_q1=True)
    report={'reading_contrast':[
        {'time':98.96,'id':'tx.b01.big.0','contrast':2.92},
        {'time':103.94,'id':'tx.b02.big.0','contrast':2.88}],
        'ordinary_text_contrast':[
        {'t':t,'id':'tx.b01.big.0','lc':0.,'text':['WORK + STUDY']}
        for t in [98.,98.7,99.3]],
        'type':{'face':[['mg1',0.,2.9,'mouth',.7,False]],
                'small_html':[{'t':1.,'id':'small','cap':.01,'text':['label']}]}}
    measured=(render_qc.reading_contrast_findings(report)
              + render_qc.measured_findings(report))
    unrelated=['JUMP OFF A CUT at 4.20s (frame 126): the whole picture changes']
    for _ in range(agent_tools.PQ_PERSIST_RENDERS+2):
        keep,persisted=agent_tools._picture_qc_split(
            ctx, measured+unrelated, measured_type=measured)
        assert all(line in keep and line not in persisted for line in measured)
    assert persisted==unrelated
    assert agent_tools._picture_qc_split(ctx,[],measured_type=())==([],[])


def test_new_type_measurement_does_not_inherit_an_older_category_counter():
    ctx=SimpleNamespace(first_cut_q1=True)
    report={'ordinary_text_contrast':[{'t':98.,'id':'title','lc':12.,'text':['keep these words']}]}
    measured=render_qc.measured_findings(report)
    for _ in range(5):agent_tools._picture_qc_split(ctx,measured)
    assert agent_tools._picture_qc_split(ctx,measured,measured_type=measured)==(measured,[])
