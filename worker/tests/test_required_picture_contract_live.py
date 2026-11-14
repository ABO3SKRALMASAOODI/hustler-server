"""Opt-in model contract regression; CI remains offline.

VALMERA_LIVE_PICTURE_CONTRACT_TEST=1 OPENAI_API_KEY=... pytest this file.
These fixed public-source examples reproduced extra Photoshop/Adobe/Apple asks.
The product's contract model is independent of the editor's provider.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

CASES = json.loads((Path(__file__).parent / 'fixtures/required_picture_contract.json').read_text())


@pytest.mark.skipif(os.getenv('VALMERA_LIVE_PICTURE_CONTRACT_TEST') != '1',
                    reason='Explicit opt-in required for metered model contract regression')
@pytest.mark.parametrize('case', CASES, ids=[c['id'] for c in CASES])
def test_only_requested_subjects_become_obligations(case, monkeypatch):
    from openai import OpenAI
    import llm
    import required_broll
    # No product DB or customer account: this probe only reads a model answer.
    monkeypatch.setattr(llm, 'record', lambda *a, **kw: None)
    ctx = SimpleNamespace(index={'words': case['words']},
                          llm_client=OpenAI(api_key=os.environ['OPENAI_API_KEY']),
                          agent_model='gpt-6.1-sol')
    ledger = {'asks': []}
    pictures = required_broll.record(ctx, ledger, case['request'])
    assert len(pictures) == case['count'], pictures
    assert set(case['anchors']) <= {i for p in pictures for i in p['word_ids']}
    ending = ledger.get('picture_story_end')
    assert ending is not None
    assert ending['word']['t1'] < case['words'][-1]['t1'] + .001
