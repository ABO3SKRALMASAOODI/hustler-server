import json
from types import SimpleNamespace as NS

import pytest
import required_broll
from score import asks


def test_rejected_generic_pictures_trigger_archive_retry_without_relaxing_subject(monkeypatch, tmp_path):
    import broll_source, agent_tools
    queries = []
    def search(subject, **kw):
        queries.append(subject)
        return [{'id': 'wrong' if len(queries) == 1 else 'mining-rig'}], []
    def check(subject, hits, **kw):
        assert subject['must_show'] == 'Real mining hardware'
        assert kw['frame_size'][0] < kw['frame_size'][1]
        if hits[0]['id'] == 'wrong':
            return [], [{'shows': 'coins and padlocks', 'ok': False}], 0
        return hits, [{'shows': 'operating mining rig', 'ok': True}], 0
    def retry(ctx, name, schema, system, payload, **kw):
        assert name == 'retry_required_picture_search'
        assert 'coins and padlocks' in payload
        assert kw['model'] == 'gpt-6-sol'
        assert 'archive_name' not in schema['properties']
        assert 'pictured subject' in schema['properties']['subject_name']['description']
        return {'subject_name': 'Cryptocurrency mining', 'queries': ['Bitcoin mining hardware']}, {}
    monkeypatch.setattr(broll_source, 'search_named', search)
    monkeypatch.setattr(broll_source, 'check_named', check)
    monkeypatch.setattr(asks, '_call', retry)
    monkeypatch.setattr(agent_tools, '_remember_search_hits', lambda *a, **kw: None)
    p = {'id': 'picture1', 'subject': 'crypto mining', 'query': 'long vague query',
         'must_show': 'Real mining hardware', 'source_words': []}
    ctx = NS(workdir=str(tmp_path), stock_results={}, index={'video': {'width': 1920, 'height': 1080}},
             latest_edl=lambda: {'json': {'frame': {'ratio': '16:9'}}})
    required_broll.supply(ctx, {'required_pictures': [p], 'picture_frame_ratio': '9:16'})
    assert len(queries) == 2
    assert queries[1]['name'] == 'Cryptocurrency mining'
    assert queries[1]['searches'] == ['Bitcoin mining hardware']
    assert p['subject'] == 'crypto mining'
    assert p['candidates'] == ['required:picture1:mining-rig']


def test_picture_candidate_is_checked_in_its_actual_cover_shape(monkeypatch, tmp_path):
    import broll_source, net_fetch, base64, io, shutil
    from PIL import Image
    source = tmp_path / 'source.jpg'
    im = Image.new('RGB', (400, 200), 'red')
    im.paste('blue', (0, 0, 100, 200))  # named mark lies outside the centre cover crop
    im.save(source)
    monkeypatch.setattr(net_fetch, 'download', lambda url, path, **kw: shutil.copyfile(source, path))
    def check(system, content, *args, **kw):
        pic = next(p for p in content if p['type'] == 'image')
        shown = Image.open(io.BytesIO(base64.b64decode(pic['source']['data'])))
        assert shown.width < shown.height
        assert shown.getpixel((shown.width // 2, shown.height // 2))[0] > 200
        assert shown.getpixel((0, shown.height // 2))[2] < 50
        return '[{"n":0,"ok":false,"grade":1,"shows":"the requested blue mark was cropped away"}]', 0
    monkeypatch.setattr(broll_source, '_ask', check)
    hits, report, _ = broll_source.check_named({'name': 'blue mark', 'must_show': 'blue mark'},
        [{'id': 'x', '_thumb': 'https://example.test/picture.jpg'}],
        workdir=str(tmp_path), frame_size=(720, 1280))
    assert hits == [] and report[0]['ok'] is False


def test_every_named_subject_becomes_durable_ask_with_word_anchor(monkeypatch):
    words = [{'w': x, 't0': float(i), 't1': i + .7} for i, x in enumerate(['Nike', 'Coke', 'Disney'])]
    def record(ctx, name, schema, system, content, **kw):
        if name == 'identify_picture_subjects':
            assert list(json.loads(content)) == ['request']
            return {'pictures': [{'subject': w['w'], 'must_show': w['w'], 'query': w['w']} for w in words], 'spoken_categories': []}, {}
        assert json.loads(content)['words'][2]['id'] == 2
        return {'pictures': [{'subject': w['w'], 'must_show': w['w'], 'query': w['w'],
                              'word_ids': [i, 999], 'picture_id': i} for i, w in enumerate(words)]}, {}
    monkeypatch.setattr(asks, '_call', record)
    ledger = {'asks': []}
    required_broll.record(NS(index={'words': words}), ledger, 'Show Nike, Coke and Disney pictures')
    assert [p['subject'] for p in ledger['required_pictures']] == ['Nike', 'Coke', 'Disney']
    assert len(ledger['asks']) == 3
    assert ledger['required_pictures'][2]['source_words'] == [words[2]]


def test_cut_and_insert_remap_picture_to_its_spoken_word():
    p = {'source_words': [{'w': 'Photoshop', 't0': 12, 't1': 12.6}]}
    edl = {'keep': [[10, 15]], 'inserts': [], 'speed': []}
    assert required_broll.anchors(p, edl) == [2.3000000000000007]
    # Dropping or clipping the named word is not silently reassigned to another word.
    assert required_broll.anchors(p, dict(edl, keep=[[12.3, 15]])) == []


def test_wrong_brand_in_render_overrides_general_ask_checker(monkeypatch, tmp_path):
    import db
    import media
    import llm
    import agent_tools
    p = {'id': 'picture1', 'subject': 'Nike', 'must_show': 'Nike product or mark',
         'source_words': [{'w': 'Nike', 't0': 2, 't1': 2.4}]}
    ledger = {'required_pictures': [p], 'lang': 'en'}
    ctx = NS(workdir=str(tmp_path), db=NS(run=lambda *a: {'kind': 'render'}), project_id=1)
    monkeypatch.setattr(agent_tools, '_asset_local_path', lambda *a: 'actual-render.mp4')
    sampled = []
    monkeypatch.setattr(media, 'frame_at', lambda path, t, out, **k: sampled.append((path, t)))
    monkeypatch.setattr(llm, 'image_part', lambda p: {'type': 'image', 'path': p})
    def check(ctx, name, schema, system, content, **kw):
        assert kw['model'] == 'gpt-6-sol'
        return {'shown': False, 'seen': 'Apple logo appears instead', 'line': 'Nike imagery was not delivered.'}, {}
    monkeypatch.setattr(asks, '_call', check)
    result = required_broll.check(ctx, ledger, {'keep': [[1, 5]]}, {'edl_version': 3})
    assert sampled == [('actual-render.mp4', 1.2000000000000002)]
    assert result['picture1']['state'] == 'not_done'
    assert 'Nike' in result['picture1']['line']


def test_missing_render_cannot_claim_picture_done(tmp_path):
    p = {'id': 'picture1', 'subject': 'crypto mining', 'source_words': [{'t0': 1, 't1': 2}]}
    ctx = NS(workdir=str(tmp_path), db=NS(run=lambda *a: None), project_id=1)
    result = required_broll.check(ctx, {'required_pictures': [p]}, {'keep': [[0, 5]]}, {})
    assert result['picture1']['state'] == 'cannot_tell'
    assert 'crypto mining' in result['picture1']['line']


def test_illustration_does_not_extend_the_narration_clock():
    ctx = NS(ask_ledger={'required_pictures': [{'subject': 'Disney'}],
                        'pictures_preserve_speech_clock': True})
    before = {'inserts': []}
    after = {'inserts': [{'id': 'image1', 'duration_s': 3}]}
    assert 'add_overlay' in required_broll.write_refusal(ctx, before, after)
    assert required_broll.write_refusal(ctx, before, {'overlays': [{'duration_s': 3}]}) is None
    ctx.ask_ledger['pictures_preserve_speech_clock'] = False
    assert required_broll.write_refusal(ctx, before, after) is None


def test_subject_enumeration_outage_cannot_be_certified_by_a_generic_check(monkeypatch):
    import agent_loop
    ctx = NS(ask_ledger={'asks': [{'id': 'a1'}], 'picture_manifest_error': 'TimeoutError'},
             ask_verdict={'asks': [{'id': 'a1', 'state': 'done'}]})
    monkeypatch.setattr(asks, 'check', lambda *a, **kw: pytest.fail('No complete subject list'))
    assert agent_loop._asks_check(ctx, 'All pictures added.') is None
    assert ctx.ask_verdict is None


def test_requested_short_has_a_complete_story_end_and_continuation_is_not_done(monkeypatch, tmp_path):
    import agent_tools
    words = [{'w': 'Disney', 't0': 1., 't1': 1.5},
             {'w': 'list.', 't0': 1.5, 't1': 2.},
             {'w': 'Next', 't0': 3., 't1': 3.5}]
    def record(*a, **kw):
        return {'pictures': [{'subject': 'Disney', 'must_show': 'Disney picture', 'query': 'Disney', 'word_ids': [0], 'picture_id': 0}],
                'preserve_speech_clock': True, 'short_story_end_word': 1,
                'short_story_end_reason': 'The examples finish here; the next line changes topic.'}, {}
    monkeypatch.setattr(asks, '_call', record)
    ledger = {'asks': []}
    required_broll.record(NS(index={'words': words}), ledger, 'Make a short with Disney pictures')
    assert ledger['asks'][-1]['id'] == 'picture_story_end'
    assert ledger['picture_story_end']['word'] == words[1]
    ctx = NS(workdir=str(tmp_path), db=NS(run=lambda *a: {'kind': 'render'}), project_id=1)
    monkeypatch.setattr(agent_tools, '_asset_local_path', lambda *a: 'render.mp4')
    # The picture check has no anchor in this test; the ending is measured independently.
    ledger['required_pictures'][0]['source_words'] = []
    assert required_broll.check(ctx, ledger, {'keep': [[0, 3.7]]}, {})['picture_story_end']['state'] == 'not_done'
    assert required_broll.check(ctx, ledger, {'keep': [[0, 2.1]]}, {})['picture_story_end']['state'] == 'done'
    assert required_broll.check(ctx, ledger, {'keep': [[0, 1.8]]}, {})['picture_story_end']['state'] == 'not_done'


def test_model_declared_names_align_to_every_repeated_transcript_mention():
    words = [{'w': w} for w in ['Zorba', 'Studio', 'and', 'Zorba?', 'Other', 'Studio', 'ZORBA.']]
    assert required_broll.repeated_anchors(words, ['Zorba', 'Zorba Studio']) == {0, 1, 3, 6}
    assert required_broll.repeated_anchors(words, []) == set()
    assert required_broll.repeated_anchors(words, ['Different name']) == set()


def test_transcript_cannot_add_subjects_to_an_explicit_request(monkeypatch):
    def check(ctx, name, schema, system, content, **kw):
        data = json.loads(content)
        if name == 'identify_picture_subjects':
            assert data == {'request': 'Show Zorba pictures as named'}
            return {'pictures': [{'subject': 'Zorba', 'must_show': 'Recognizable Zorba', 'query': 'Zorba'}],
                    'spoken_categories': []}, {}
        assert 'subject' not in schema['properties']['pictures']['items']['properties']
        return {'pictures': [{'picture_id': 0, 'word_ids': [0], 'spoken_forms': ['Zorba']},
                             {'picture_id': 99, 'subject': 'Unrequested company', 'word_ids': [1]}]}, {}
    monkeypatch.setattr(asks, '_call', check)
    ledger = {'asks': []}
    pictures = required_broll.record(NS(index={'words': [
        {'w': 'Zorba', 't0': 0, 't1': 1}, {'w': 'Other', 't0': 1, 't1': 2},
        {'w': 'Zorba?', 't0': 2, 't1': 3}]}), ledger, 'Show Zorba pictures as named')
    assert [p['subject'] for p in pictures] == ['Zorba']
    assert pictures[0]['word_ids'] == [0, 2]


@pytest.mark.parametrize('cut_away', [False, True])
def test_unspoken_picture_can_be_seen_but_cutting_its_words_cannot_evade_check(monkeypatch, tmp_path, cut_away):
    import agent_tools, media, llm
    picture = {'id': 'picture1', 'subject': 'Zorba', 'must_show': 'Recognizable Zorba',
               'source_words': [{'w': 'Zorba', 't0': 0, 't1': 1}] if cut_away else []}
    ctx = NS(workdir=str(tmp_path), db=NS(run=lambda *a: {'kind': 'render'}), project_id=1)
    monkeypatch.setattr(agent_tools, '_asset_local_path', lambda *a: 'render.mp4')
    samples = []
    monkeypatch.setattr(media, 'frame_at', lambda path, t, out, **kw: samples.append(t))
    monkeypatch.setattr(llm, 'image_part', lambda p: {'type': 'image', 'path': p})
    def check(ctx, name, schema, system, content, **kw):
        assert json.loads(content[0]['text'])['spoken_anchor_check'] is False
        return {'shown': True, 'seen': 'Zorba is visible.', 'line': 'Zorba is shown.'}, {}
    monkeypatch.setattr(asks, '_call', check)
    result = required_broll.check(ctx, {'required_pictures': [picture]},
        {'keep': [[2, 5]], 'overlays': [{'start': .1, 'duration_s': .4}]}, {'edl_version': 3})
    assert result['picture1']['state'] == ('cannot_tell' if cut_away else 'done')
    assert (len(samples) == 0) is cut_away
    if not cut_away:
        assert .1 + .4 / 2 in samples
