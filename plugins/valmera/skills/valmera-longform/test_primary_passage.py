from primary_passage import locate


def test_original_passage_requires_literal_source_text():
    pages=['A report title', 'Paul had the tool. I had Paul by the legs, and Pete was flying.']
    assert locate(pages,['I had Paul by the legs, and Pete was flying.'])==[1]
    assert locate(pages,['I had Paul by the arms, and Pete was flying.'])==[]
    assert locate(pages,['Paul'])==[]


def test_page_lookup_preserves_real_page_index_and_whitespace():
    pages=['']*350+['I had Paul by the\r\nlegs, and Pete was flying.']
    assert locate(pages,['I had Paul by the legs, and Pete was flying.'])==[350]
