import pytest

import motion_engine
import motion_layer
import motion_templates


@pytest.mark.parametrize('start,phase,full', [(0., 0., 141.), (13.25, 7.5, 180.)])
def test_long_html_reaches_render_inputs_in_bounded_chunks_without_resetting_its_clock(
        tmp_path, monkeypatch, start, phase, full):
    item = dict(id='support', template='html', start=start, end=start+141,
                phase_s=phase, full_duration_s=full, params={},
                html='<div>Whole-film visual support</div>', layer='above_captions')
    jobs = []
    def render(batch, out, **kwargs):
        jobs.extend(batch)
        return [motion_engine.RenderedClip(str(tmp_path/f'{i}.mov'), 0, 0, 100, 100,
                    round(j.duration*30), 1, False, 0.) for i,j in enumerate(batch)]
    monkeypatch.setattr(motion_engine, 'render_jobs', render)
    inputs, _ = motion_layer.prepare_inputs({'motion':[item]}, str(tmp_path),
                                            720, 1280, 30., start+141, [], 1)
    assert len(jobs) == len(inputs) == 2
    assert all(0 < j.duration <= motion_engine.MAX_DURATION_S for j in jobs)
    assert sum(j.duration for j in jobs) == pytest.approx(141)
    assert jobs[0].t0 == phase
    assert jobs[1].t0 == pytest.approx(phase + jobs[0].duration)
    # Both browser documents retain the same total duration and animation;
    # only the rendered time window changes.
    assert jobs[0].html == jobs[1].html == motion_templates.build_job(item,720,1280,30).html
    spans = [(part['start'],part['end']) for _,part,_ in inputs]
    assert spans[0][0] == start and spans[-1][1] == start+141
    assert spans[0][1] == spans[1][0]
