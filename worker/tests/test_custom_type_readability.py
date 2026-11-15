"""Opaque custom graphics must not hide unreadable labels from type review."""
import time

import pytest

import motion_critic
import motion_engine
import render_qc


@pytest.mark.skipif(not motion_engine.available(), reason='headless Chromium unavailable')
@pytest.mark.parametrize('size', [13, 30])
def test_custom_card_secondary_type_reaches_the_rendered_critic(size):
    html = f'''<div style="position:absolute;inset:0;background:#eee;color:#111">
    <h1 style="font:80px Arial">A large headline</h1>
    <div style="font:{size}px Arial">Secondary context remains necessary</div>
    <div style="opacity:0;font:4px Arial">Hidden animation state</div>
    <div style="display:none;font:4px Arial">Not drawn</div></div>'''
    item = dict(id='card', template='html', start=0, end=4, params={}, html=html)
    plan = dict(W=1280, H=720, fps=30, captions=[], items=[dict(
        id='card', template='html', start=0, end=4, persistent=False,
        layer='above_captions', item=item)])
    measured = render_qc.type_pass(plan, time.monotonic() + 30)
    assert measured and 'card' in measured['items']
    runs = measured['items']['card']['type_runs']
    assert [r['text'] for r in runs] == ['A large headline', 'Secondary context remains necessary']
    report = render_qc.type_findings(measured, plan, [])
    assert bool(render_qc.measured_findings({'type': report})) == (size == 13)
    findings, _ = motion_critic.lens_type({'picture_qc': {'type': report}})
    small = [f for f in findings if f['code'] == 'html_type_too_small']
    assert bool(small) == (size == 13)
    if small:
        assert 'Secondary context' in str(small)
        assert 'Preserve their words' in str(small)
