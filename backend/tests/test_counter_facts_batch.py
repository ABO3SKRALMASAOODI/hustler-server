from test_w0c_mcp_music_gate import api  # Reuse the in-memory batch transaction.
from routes import video


def test_mcp_counter_refusal_saves_no_edl_or_success_receipt(api, monkeypatch):
    execute = api.execute

    def with_words(sql, args=()):
        if "json->'words' AS words" in sql:
            api.one = {'words': [{'w': w, 't0': 5 + i * .2}
                                for i, w in enumerate('dia 20 até dia 29'.split())]}
        else:
            execute(sql, args)

    api.execute = with_words
    monkeypatch.setattr(video, '_active_original', lambda *_: {'duration_s': 20, 'sha256': 'source'})
    result, status = video.apply_edit_batch_core(1, 2, {
        'base_version': 1, 'operation_id': 'counter-date-range-0123456789',
        'operations': [{'action': 'upsert', 'layer': 'motion', 'id': 'date', 'value': {
            'id': 'date', 'template': 'counter', 'start': 5, 'end': 10,
            'params': {'value': '20–29', 'label': 'dias de promoção'}}}]}, origin='mcp')
    assert status == 400 and result['code'] == 'invalid_edit_batch'
    assert 'calendar dates' in result['error']
    assert list(api.edls) == [1] and not api.messages
