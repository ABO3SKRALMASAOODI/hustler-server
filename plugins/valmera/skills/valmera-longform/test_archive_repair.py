"""Regressions from the captured archive-screenplay repair, November 26."""
from copy import deepcopy

from archive_repair import apply_edits, exhibit_facts, scope


def exhibit(title, fact=None, quote=None):
    source = dict(credit='Source', title=title, url='https://example.org/verification',
                  primary_context=[dict(quote=quote)] if quote else [])
    return dict(clip=dict(source=source, fact=fact), form='marked excerpt', visible=title)


def test_sparse_repair_retains_sources_on_their_words_and_recovers_an_exact_passage():
    release, other = '8b1e6eacfc869b74', '41fb4b1ca6c1ac2d'
    first, second = 'a22a922fec18e066', '4e262f7c5005cbab'
    catalogue = {release:exhibit('remark.pdf'), other:exhibit('remark.pdf'),
                 first:exhibit('Sutherland module replay', 'f32'),
                 second:exhibit('Sutherland full-export run', quote='exit 0 at 19:05:45 UTC on 20 November')}
    old = dict(chapters=[], segments=[
        dict(say='Alpöge puts the proof online.', facts=[], visual_beats=[
            dict(word_start=0, archive_ids=[release], label='Original illustration')]),
        dict(say='A module replay. A separate full-export check.', facts=['f32', 'f33'], visual_beats=[
            dict(word_start=0, archive_ids=[first]), dict(word_start=3, archive_ids=[other])]),
        dict(say='The ending stays.', facts=[], visual_beats=[dict(word_start=0, archive_ids=[release])])])
    pristine = deepcopy(old)
    draft = [dict(say='Lean’s kernel replays the modules. A separate full-export check.', facts=['f32','f33'])]
    facts = [dict(id='f33',url='https://example.org/verification#nanoda',quote='exit 0 at 19:05:45 UTC on 20 November'),
             dict(id='unrelated',url='https://example.org/other',quote='exit 0 at 19:05:45 UTC on 20 November')]
    evidence = exhibit_facts(catalogue, facts)
    assert evidence[second] == {'f33'}
    revised = apply_edits(old, dict(segments=[
        dict(index=0, changes=dict(visual_beats=[dict(word_start=0, archive_ids=[])])),
        dict(index=1, draft_index=0, beats=[dict(index=1,source_fact='f33')])]), catalogue, draft, evidence)
    assert revised['segments'][0]['visual_beats'] == [
        dict(word_start=0, archive_ids=[release], label='Source · remark.pdf · marked excerpt')]
    line = revised['segments'][1]
    assert (line['say'],line['facts']) == (draft[0]['say'],draft[0]['facts'])
    assert [(b['word_start'],b['archive_ids']) for b in line['visual_beats']] == [(0,[first]),(5,[second])]
    assert line['say'].split()[5:7] == ['A','separate']
    assert old == pristine and revised['segments'][2] == old['segments'][2]
    delta = scope(revised, ['legacy unindexed finding'], old, unindexed=False)
    assert [s['index'] for s in delta['segments']] == [0,1]


def test_explicit_selection_beats_inherited_source_and_omitted_slot_survives():
    keys = ['e5f3f48695170807', 'a6d4e9cbd82e0630', 'e7f480803ddc0f5f']
    catalogue = {k:exhibit('Signal paper') for k in keys}
    sc = dict(chapters=[], segments=[dict(say='The gatekeeper checks the proof and compares the specification.', facts=[],
              visual_beats=[dict(word_start=0,archive_ids=keys[:1]),dict(word_start=5,archive_ids=keys[1:2])])])
    revised = apply_edits(sc, dict(segments=[dict(index=0,beats=[
        dict(index=1,archive_ids=keys[2:],label='Held-out specification')])]), catalogue, [])
    assert revised['segments'][0]['visual_beats'] == [
        dict(word_start=0,archive_ids=keys[:1]), dict(word_start=5,archive_ids=keys[2:],label='Held-out specification')]


def test_borrowed_draft_sentence_does_not_truncate_the_cited_october_result():
    march = 'In March, the estimated success rate for agents working autonomously was twenty three percent.'
    october = 'By October, the estimate for that group had reached forty six percent.'
    sc = dict(chapters=[], segments=[dict(say='The estimates changed.', facts=['f11','f15'],
              visual_beats=[dict(word_start=0, archive_ids=['report'])])])
    patch = dict(segments=[dict(index=0, draft_index=0, changes=dict(say=march+' '+october, facts=['f11','f15']))])
    result = apply_edits(sc,patch,{'report':exhibit('OpenAI report')},[dict(say=march,facts=['f11'])])
    assert result['segments'][0]['say'] == march+' '+october
    assert result['segments'][0]['facts'] == ['f11','f15']


def test_omitted_plate_uses_real_cited_exhibit_and_describes_what_will_show():
    catalogue = {'earlier':exhibit('Standalone Comparator run', 'standalone'),
                 'nanoda':exhibit('November 20 full-export check', 'f33')}
    old = dict(chapters=[], segments=[dict(say='Nanoda accepted the full export.',facts=['f33'],
               design=None,direction='Show a transfer between the two runs.',pictures=['Transfer'],
               visual_beats=[dict(word_start=0,archive_ids=['earlier'])])])
    patch = dict(segments=[dict(index=0,changes=dict(visual_beats=[],pictures=[]))])
    line = apply_edits(old,patch,catalogue,[])['segments'][0]
    assert line['visual_beats'][0]['archive_ids'] == ['nanoda']
    assert line['pictures'] == ['November 20 full-export check']
    assert line['say'] == old['segments'][0]['say']


def test_complete_populated_replacement_retires_explicitly_removed_exhibit():
    catalogue = {k:exhibit(k) for k in ['module','comparison']}
    old = dict(chapters=[], segments=[dict(say='The record describes the module replay.', facts=[],
               visual_beats=[dict(word_start=0,archive_ids=['module']),dict(word_start=3,archive_ids=['comparison'])])])
    replacement = [dict(word_start=0,archive_ids=['module'])]
    result = apply_edits(old,dict(segments=[dict(index=0,changes=dict(visual_beats=replacement))]),catalogue,[])
    assert result['segments'][0]['visual_beats'] == replacement


def test_selected_draft_passages_carry_citations_and_cut_on_their_actual_words():
    from archive_repair import source_edits
    catalogue = {'module':exhibit('Module replay','f32'), 'nanoda':exhibit('Full export','f33')}
    evidence = exhibit_facts(catalogue, [])
    sc = dict(chapters=[],segments=[dict(say='The first check made the second possible.', facts=['f32'],
              visual_beats=[dict(word_start=0,archive_ids=['module']),dict(word_start=3,archive_ids=['module'])],
              pictures=[],direction='')])
    draft = [dict(say='The modules passed.',facts=['f32']),dict(say='Nanoda checked the full export.',facts=['f33'])]
    plan = dict(segments=[dict(index=0,passages=[dict(draft_index=0,archive_id='module'),
                                                  dict(draft_index=1,archive_id='mistyped-exhibit')])])
    patch = source_edits(plan,sc,draft,catalogue,evidence,{'f32','f33'})
    line = apply_edits(sc,patch,catalogue,draft)['segments'][0]
    assert line['say']=='The modules passed. Nanoda checked the full export.'
    assert line['facts']==['f32','f33']
    assert [(b['word_start'],b['archive_ids']) for b in line['visual_beats']]==[(0,['module']),(3,['nanoda'])]
    assert all('measured' not in b for b in line['visual_beats'])
