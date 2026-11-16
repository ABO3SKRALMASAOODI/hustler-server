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


# ---------------------------------------------- narration to a first cut (#208, #1278)

def test_pexels_page_address_gives_the_creators_title():
    import stock
    assert stock._page_title(
        'https://www.pexels.com/video/a-boy-sitting-while-in-the-classroom-5897642/') == \
        'A boy sitting while in the classroom'
    assert stock._page_title('https://www.pexels.com/video/1093662/') is None
    assert stock._page_title('https://www.pexels.com/video/video-1093662/') is None
    assert stock._page_title(None) is None


def test_video_result_no_model_could_see_carries_the_librarys_title(tmp_path, monkeypatch):
    """p5673/p5735: every stock video came back '(no description)'. With no
    model to look, the clip's own library title is its description, said as
    the library's words, still unverified, and add_stock_media can take it."""
    monkeypatch.setattr(net_fetch, 'download', lambda *a, **kw: None)
    monkeypatch.setattr(llm, 'ask_vision', lambda *a, **kw: None)
    hit = {'id': 'pexels:video:4121725', 'title': 'Scientist using a pipette in a lab',
           '_thumb': 'https://images.pexels.com/1.jpg'}
    rows, missing = mcp_search.describe_missing(SimpleNamespace(workdir=str(tmp_path)), [hit])
    assert missing == 0
    assert rows[0]['description'] == 'Scientist using a pipette in a lab'
    assert rows[0]['unverified'] is True and not rows[0].get('undescribed')
    assert "library's own title" in rows[0]['description_basis']


class _Canvas:
    """A no-main-video project whose writers append inserts, overlays and voiceovers."""
    def __init__(self, main=False):
        self.has_main_video = main
        self.stock_added = []
        self.version = 1
        self.edl = {'keep': [[0, 100.0]] if main else [], 'inserts': [], 'voiceover': []}

    def latest_edl(self):
        return {'version': self.version, 'json': self.edl}

    def write(self, **change):
        self.edl = dict(self.edl, **change)
        self.version += 1
        return f'EDL v{self.version - 1} -> v{self.version}'


def _wire_canvas(monkeypatch, ctx, refuse=()):
    calls = []

    def acquire(c, id):
        if id in refuse:
            return 'REJECTED: that result could not be downloaded.'
        c.stock_added.append({'storage_key': f'stock/{id}.mp4'})
        return 'Acquired'

    def start(c, key, duration_s=None, clip_start_s=None, **kw):
        calls.append(('start', key, 0.0, duration_s))
        return c.write(inserts=[{'asset_key': key, 'duration_s': duration_s}], voiceover=[])

    def insert(c, key, at, duration_s=None, clip_start_s=None, **kw):
        calls.append(('insert', key, at, duration_s))
        return c.write(inserts=c.edl['inserts'] + [{'asset_key': key, 'duration_s': duration_s}])

    def overlay(c, key, start, duration_s=None, **kw):
        calls.append(('overlay', key, start, duration_s))
        return c.write(overlays=(c.edl.get('overlays') or []) + [key])

    def voice(c, key, start_output_s=0):
        calls.append(('voice', key, start_output_s, None))
        return c.write(voiceover=[{'asset_key': key}])

    monkeypatch.setattr(agent_tools, 'add_stock_media', acquire)
    monkeypatch.setattr(agent_tools, 'start_media_sequence', start)
    monkeypatch.setattr(agent_tools, 'insert_media', insert)
    monkeypatch.setattr(agent_tools, 'add_overlay', overlay)
    monkeypatch.setattr(agent_tools, 'add_voiceover', voice)
    monkeypatch.setattr(agent_tools, '_resolve_media_asset',
                        lambda c, key, kinds: ({'id': 1, 'storage_key': key}, None))
    monkeypatch.setattr(agent_tools, '_asset_media_duration', lambda c, a: 20.0)
    return calls


def test_place_scenes_builds_the_narrations_picture_in_one_call(monkeypatch):
    ctx = _Canvas()
    calls = _wire_canvas(monkeypatch, ctx)
    out = mcp_search.place_scenes(ctx, [
        {'id': 'b', 'start_s': 6, 'end_s': 14},
        {'id': 'a', 'start_s': 0, 'end_s': 6},
        {'asset_key': 'fetched/1/trial.mp4', 'start_s': 14, 'end_s': 20, 'source_start_s': 30}],
        narration_asset_key='music/1/nar.mp3')
    assert calls == [('start', 'stock/a.mp4', 0.0, 6.0), ('insert', 'stock/b.mp4', 6.0, 8.0),
                     ('insert', 'fetched/1/trial.mp4', 14.0, 6.0),
                     ('voice', 'music/1/nar.mp3', 0, None)]
    assert out.startswith('PLACED 3 of 3 scene(s); EDL v1 -> v5; the programme is now 20.0s.')
    assert 'Narration music/1/nar.mp3 laid under the picture' in out
    assert 'export_final(project_id, edl_version=5)' in out


def test_a_scene_that_cannot_land_is_reported_and_covered_by_the_next(monkeypatch):
    ctx = _Canvas()
    calls = _wire_canvas(monkeypatch, ctx, refuse=('b',))
    out = mcp_search.place_scenes(ctx, [
        {'id': 'a', 'start_s': 0, 'end_s': 5}, {'id': 'b', 'start_s': 5, 'end_s': 9},
        {'id': 'c', 'start_s': 9, 'end_s': 12}, {'start_s': 'x', 'end_s': 3}],
        narration_asset_key='music/1/nar.mp3')
    assert ('insert', 'stock/c.mp4', 5.0, 7.0) in calls       # c stretches back over b's time
    assert 'PLACED 2 of 3 scene(s)' in out
    assert 'not placed: scene 2 (5-9s, b): REJECTED: that result could not be downloaded.' in out
    assert 'skipped: scene 4: needs numeric start_s < end_s' in out
    assert '8.0s from 12.0s still has no picture' in out


def test_place_scenes_over_a_filmed_video_cuts_away_at_the_scene_times(monkeypatch):
    ctx = _Canvas(main=True)
    calls = _wire_canvas(monkeypatch, ctx)
    out = mcp_search.place_scenes(ctx, [{'id': 'a', 'start_s': 3, 'end_s': 7.5}])
    assert calls == [('overlay', 'stock/a.mp4', 3.0, 4.5)]
    assert out.startswith('PLACED 1 of 1 scene(s)')


def test_place_scenes_stops_at_its_budget_and_names_the_rest(monkeypatch):
    ctx = _Canvas()
    _wire_canvas(monkeypatch, ctx)
    monkeypatch.setattr(mcp_search, 'PLACE_SCENES_BUDGET_S', -1)
    out = mcp_search.place_scenes(ctx, [{'id': 'a', 'start_s': 0, 'end_s': 5}])
    assert 'PLACED 0 of 1' in out
    assert '1 scene(s) from 0s were not reached in this call' in out


def test_place_scenes_is_in_the_mcp_catalog():
    assert 'place_scenes' in agent_tools.TOOLS
    import tools_ext
    assert 'place_scenes' in tools_ext.EXT_TOOL_NAMES


def test_place_scenes_through_the_real_writers_makes_a_valid_narrated_canvas(monkeypatch, tmp_path):
    """The same call through the real start_media_sequence / insert_media /
    add_voiceover: the narration's picture is one valid canvas EDL whose
    pictures end where their words end, with the narration under them."""
    import schemas
    assets = {'music/1/nar.mp3': {'id': 3, 'kind': 'music', 'storage_key': 'music/1/nar.mp3',
                                  'duration_s': 20.0, 'meta': {'filename': 'nar.mp3'}}}
    for name in ('a', 'b', 'c'):
        assets[f'stock/{name}.mp4'] = {'id': 10 + len(assets), 'kind': 'video_clip',
                                       'storage_key': f'stock/{name}.mp4', 'duration_s': 30.0,
                                       'width': 1920, 'height': 1080,
                                       'meta': {'filename': f'{name}.mp4'}}

    class Ctx:
        has_main_video = False
        canvas_ratio = '16:9'
        project_id = 1
        index = None
        workdir = str(tmp_path)
        sight_out = True

        def __init__(self):
            self.stock_added = []
            self.row = {'version': 1, 'json': schemas.canvas_edl('16:9')}
            self.db = self

        def latest_edl(self):
            return self.row

        def write_edl(self, edl, desc):
            schemas.validate_edl(edl)
            v = self.row['version']
            self.row = {'version': v + 1, 'json': edl}
            return f'EDL v{v} -> v{v + 1}: {desc}'

        def run(self, fn, project_id, key, *a):
            return assets.get(key)

    ctx = Ctx()

    def acquire(c, id):
        c.stock_added.append({'storage_key': f'stock/{id}.mp4'})
        return 'Acquired'

    monkeypatch.setattr(agent_tools, 'add_stock_media', acquire)
    out = mcp_search.place_scenes(ctx, [
        {'id': 'a', 'start_s': 0, 'end_s': 6.5}, {'id': 'b', 'start_s': 6.5, 'end_s': 13},
        {'id': 'c', 'start_s': 13, 'end_s': 20, 'source_start_s': 4}],
        narration_asset_key='music/1/nar.mp3')
    edl = ctx.row['json']
    assert [i['asset_key'] for i in edl['inserts']] == ['stock/a.mp4', 'stock/b.mp4', 'stock/c.mp4']
    assert [i['duration_s'] for i in edl['inserts']] == [6.5, 6.5, 7.0]
    assert edl['inserts'][2]['source_start_s'] == 4
    assert schemas.program_duration(edl) == 20.0
    assert [v['asset_key'] for v in edl['voiceover']] == ['music/1/nar.mp3']
    assert out.startswith('PLACED 3 of 3 scene(s); EDL v1 -> v5; the programme is now 20.0s.')
    assert 'still has no picture' not in out
