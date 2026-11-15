"""MCP finish boundary regressions: waiting, evidence, placement and real signals."""
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import agent_tools
import db
import llm
import mcp_craft
import mcp_exec
import mcp_search
import net_fetch


def test_pending_asset_analysis_is_successful_wait_not_failed_edit():
    class DB:
        def run(self, fn, *args):
            if fn is db.last_index_failure:
                return None
            if fn is db.enqueue_asset_index:
                return 912
            raise AssertionError(fn)
    ctx = SimpleNamespace(db=DB(), project_id=7, project={'user_id': 3}, job={})
    result = agent_tools.ToolContext.start_asset_index(ctx, {'id': 8, 'kind': 'music'})
    report = mcp_exec._tool_result_contract(ctx, result)
    assert report['status'] == 'pending' and report['is_error'] is False
    assert report['index_job_id'] == 912 and report['retry_after_s'] == 10


def test_failed_asset_analysis_is_still_an_error():
    ctx = SimpleNamespace(db=SimpleNamespace(run=lambda *a: 'decoder failed'), project_id=7)
    result = agent_tools.ToolContext.start_asset_index(ctx, {'id': 8, 'kind': 'music'})
    assert mcp_exec._tool_result_contract(ctx, result)['is_error'] is True


def test_missing_descriptions_use_pixels_and_remove_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(net_fetch, 'download', lambda url, path, **kw: open(path, 'wb').write(b'frame'))
    seen = []
    def vision(prompt, paths, **kw):
        seen.extend(paths)
        assert all(os.path.exists(p) for p in paths)
        return json.dumps([{'id': 'pexels:1', 'description': 'A red train beside a platform.'}])
    monkeypatch.setattr(llm, 'ask_vision', vision)
    hit = {'id': 'pexels:1', '_thumb': 'https://provider/thumb'}
    rows, missing = mcp_search.describe_missing(SimpleNamespace(workdir=str(tmp_path)), [hit])
    assert rows[0]['description'] == 'A red train beside a platform.' and missing == 0
    assert seen and not any(os.path.exists(p) for p in seen)
    assert not list(tmp_path.iterdir())


def test_failed_vision_never_invents_description(tmp_path, monkeypatch):
    """#1278: no model can look. Nothing is invented, and the real candidate is still returned,
    marked undescribed and unverified, with the library's own metadata intact."""
    monkeypatch.setattr(net_fetch, 'download', lambda *a, **kw: None)
    monkeypatch.setattr(llm, 'ask_vision', lambda *a, **kw: None)
    hit = {'id': '1', '_thumb': 'https://provider/thumb', 'page_url': 'https://provider/page',
           'duration_s': 12}
    rows, missing = mcp_search.describe_missing(SimpleNamespace(workdir=str(tmp_path)), [hit])
    assert missing == 1 and [r['id'] for r in rows] == ['1']          # candidates returned
    assert rows[0]['description'] is None                               # no invented description
    assert rows[0]['undescribed'] is True and rows[0]['unverified'] is True
    assert rows[0]['page_url'] == 'https://provider/page' and rows[0]['duration_s'] == 12
    assert not list(tmp_path.iterdir())


def test_search_stock_offers_undescribed_candidates_with_an_honest_note(tmp_path, monkeypatch):
    import stock
    monkeypatch.setattr(stock, 'available', lambda: True)
    monkeypatch.setattr(stock, 'search', lambda q, **kw: [
        {'id': 'pexels:video:7', 'provider': 'pexels', 'kind': 'video', 'duration_s': 9,
         'width': 1920, 'height': 1080, 'page_url': 'https://www.pexels.com/video/lab-7/',
         '_thumb': 'https://images.pexels.com/7.jpg'},
        {'id': 'pixabay:video:8', 'provider': 'pixabay', 'kind': 'video', 'duration_s': 5,
         'description': 'lab, microscope'}])
    monkeypatch.setattr(net_fetch, 'download', lambda *a, **kw: None)
    monkeypatch.setattr(llm, 'ask_vision', lambda *a, **kw: None)
    monkeypatch.setattr(llm, 'vision_available', lambda plan=None: False)
    ctx = SimpleNamespace(workdir=str(tmp_path), stock_results={}, db=None)
    answer = agent_tools.search_stock(ctx, 'medical laboratory', kind='video',
                                      orientation='landscape')
    assert answer.startswith('2 stock video(s)')
    assert 'pexels:video:7' in answer and 'pixabay:video:8' in answer
    assert 'could not be described' not in answer
    assert '1 of these 2 candidate(s) are UNVERIFIED' in answer
    assert 'page https://www.pexels.com/video/lab-7/' in answer
    assert 'thumbnail https://images.pexels.com/7.jpg' in answer
    assert ctx.stock_results['pexels:video:7']['description'] is None


def test_description_provider_outage_keeps_known_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(net_fetch, 'download', lambda *a, **kw: None)
    def unavailable(*a, **kw):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(llm, 'ask_vision', unavailable)
    known = {'id': 'known', 'description': 'A train at a platform.'}
    rows, missing = mcp_search.describe_missing(SimpleNamespace(workdir=str(tmp_path)),
        [known, {'id': 'unknown', '_thumb': 'https://provider/thumb'}])
    assert [r['id'] for r in rows] == ['known', 'unknown'] and missing == 1
    assert rows[0] == known and 'undescribed' not in rows[0]
    assert rows[1]['description'] is None and rows[1]['unverified'] is True
    assert not list(tmp_path.iterdir())


def test_place_uses_acquired_key_and_source_window(monkeypatch):
    ctx = SimpleNamespace(stock_added=[])
    def acquire(ctx, id):
        ctx.stock_added.append({'storage_key': 'exact/provider.mp4', 'id': id})
        return 'Acquired'
    placed = []
    monkeypatch.setattr(agent_tools, 'add_stock_media', acquire)
    monkeypatch.setattr(agent_tools, 'add_overlay', lambda *a, **kw: placed.append((a, kw)) or 'EDL v2')
    out = mcp_search.place(ctx, 'saved-result', 12, 3, source_start_s=4)
    assert 'EDL v2' in out
    assert placed[0][0][1:] == ('exact/provider.mp4', 12)
    assert placed[0][1] == {'duration_s': 3, 'source_start_s': 4, 'fit': 'cover'}


def test_failed_acquisition_never_places(monkeypatch):
    monkeypatch.setattr(agent_tools, 'add_stock_media', lambda *a: 'UNAVAILABLE')
    monkeypatch.setattr(agent_tools, 'add_overlay', lambda *a, **kw: pytest.fail('placed failed acquisition'))
    assert mcp_search.place(SimpleNamespace(stock_added=[]), 'missing', 0, 2) == 'UNAVAILABLE'


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg unavailable')
@pytest.mark.parametrize('moving,audible', [(False, False), (True, True)])
def test_craft_report_reads_encoded_pixels_and_sound(tmp_path, moving, audible):
    path = str(tmp_path / 'film.mp4')
    visual = 'testsrc2=size=192x108:rate=12' if moving else 'color=c=blue:s=192x108:r=12'
    sound = 'sine=frequency=600:sample_rate=48000' if audible else 'anullsrc=r=48000:cl=mono'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', visual,
                    '-f', 'lavfi', '-i', sound, '-t', '3', '-c:v', 'libx264',
                    '-c:a', 'aac', '-y', path], check=True)
    report = mcp_craft.measure(path, {'keep': [[0, 3]], 'effects': {}, 'captions': {'enabled': False}}, duration_s=3)
    assert report['silent'] is (not audible)
    assert report['no_motion_detected'] is (not moving)
    assert report['no_authored_text'] is True
    assert report['motion_pairs'] >= 4
    json.dumps(report, allow_nan=False)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg unavailable')
def test_sound_only_on_automatic_tail_cannot_hide_silent_program(tmp_path):
    path = str(tmp_path / 'tail.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=blue:s=192x108:r=12', '-f', 'lavfi', '-i',
                    'sine=frequency=600:sample_rate=48000', '-af', "volume=0:enable='lt(t,3)'",
                    '-t', '4', '-c:v', 'libx264', '-c:a', 'aac', '-y', path], check=True)
    report = mcp_craft.measure(path, {'keep': [[0, 3]], 'effects': {}},
                              audio={'i': -20}, duration_s=4)
    assert report['program_s'] == 3
    assert report['silent'] is True


@pytest.mark.parametrize('extra', [{'motion': [{'kind': 'shape'}]}, {'captions': {'enabled': True}}])
def test_graphics_or_caption_switch_do_not_prove_visible_text(monkeypatch, extra):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=bytes(96*54*3)))
    report = mcp_craft.measure('unused', {'keep': [[0, 3]], **extra}, audio={'i': -20}, duration_s=3)
    assert report['no_authored_text'] is None
    assert any('TEXT UNKNOWN' in w for w in report['warnings'])
