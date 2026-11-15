import json
import pytest
from primary_passage import locate,retained_passages,select_retained,existing_pdf


def test_original_passage_requires_literal_source_text():
    pages=['A report title', 'Paul had the tool. I had Paul by the legs, and Pete was flying.']
    assert locate(pages,['I had Paul by the legs, and Pete was flying.'])==[1]
    assert locate(pages,['I had Paul by the arms, and Pete was flying.'])==[]
    assert locate(pages,['Paul'])==[]


def test_page_lookup_preserves_real_page_index_and_whitespace():
    pages=['']*350+['I had Paul by the\r\nlegs, and Pete was flying.']
    assert locate(pages,['I had Paul by the legs, and Pete was flying.'])==[350]


def test_retained_figure_context_can_supply_a_distinct_original_text_page():
    url='https://archive.example/report.pdf'
    quote='The commander, using the pole as an aid, translated to the cutter end.'
    source=dict(url=url+'#page=18',title='Mission report',credit='Archive',licence='Public domain',
                licence_url='https://archive.example/licence',primary_context=[
                    dict(source_url=url+'#page=16',quote=quote),
                    dict(source_url='https://other.example/report.pdf',quote='A different report must not inherit the licence of this source.')])
    clip=dict(source=source)
    ft=dict(lines=[dict(clips=[clip])],editorial=dict(bank=[clip],pins=[dict(clip=clip)]))
    candidates=retained_passages(ft)
    assert len(candidates)==1 and candidates[0]['passages']==[quote]
    selected=select_retained(candidates,[dict(document=0,lines=[45,999],locators=[
        'using the pole as an aid, translated to the cutter end',
        'using the rope as an aid, translated to the cutter end'])],{45:[]})
    assert selected[0]['url']==url and selected[0]['lines']==[45]
    assert selected[0]['locators']==['using the pole as an aid, translated to the cutter end']
    assert locate(['Figure page',quote],selected[0]['locators'])==[1]
    with pytest.raises(ValueError,match='unknown retained'):
        select_retained(candidates,[dict(document=7,lines=[45],locators=[quote])],{45:[]})


def test_original_pdf_reuse_requires_known_legacy_source(tmp_path):
    folder=tmp_path/'evidence-documents';folder.mkdir()
    pdf=folder/'report.pdf';pdf.write_bytes(b'original retained report')
    url='https://archive.example/report.pdf'
    assert existing_pdf(tmp_path,url) is None
    (folder/'discovery.json').write_text(json.dumps(dict(documents=[dict(url=url)])))
    assert existing_pdf(tmp_path,url)==pdf
