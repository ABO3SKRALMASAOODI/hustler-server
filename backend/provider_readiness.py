"""Bounded readiness checks before selling a new hosted editing contract.

No project is touched and no customer credits are consumed. Cache across web
requests so checkout retries don't generate repeated model calls. Legacy
contracts and their renewal webhooks never pass through this check.

OpenAI is never a reason to refuse a checkout (15 Nov 2026, the OpenAI credit
ran out): every plan edits on the Claude lane (claude_plans), and the OpenAI
edit rung answers on Haiku whenever OpenAI cannot. So the editor is ready when
OpenAI answers, or when it does not and the Claude lane's key is there. A credit
refusal latches the probe off for 15 minutes (openai_credit), so checkouts send
no OpenAI call meanwhile, and alerts the admin once.
"""
import os
import threading
import time
import requests

import openai_credit
import provider_creds

_lock = threading.Lock()
_cache = {}
FALLBACK_MODEL = os.getenv('EDIT_RUNG_FALLBACK_MODEL', 'claude-haiku-5-5')


def _fallback_ready(result, reason):
    """The Claude lane edits when OpenAI cannot."""
    if provider_creds.key('ANTHROPIC_API_KEY'):
        result.update(ready=True, model=FALLBACK_MODEL, reason=None, fallback=reason)
    return result


def advanced_readiness():
    now = time.monotonic()
    with _lock:
        if _cache and now < _cache['expires']:
            return dict(_cache['result'])
        key = provider_creds.key('OPENAI_API_KEY')
        result = {'ready': False, 'model': 'gpt-6.1-sol', 'reason': 'provider_unavailable'}
        if key and not openai_credit.available():
            result['reason'] = 'provider_funding'
        elif key:
            try:
                r = requests.post(os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/') + '/responses',
                    headers={'Authorization': 'Bearer ' + key},
                    json={'model': 'gpt-6.1-sol', 'input': 'Reply OK.', 'reasoning': {'effort': 'low'},
                          'max_output_tokens': 512, 'store': False}, timeout=(3.05, 12))
                body = r.json()
                if r.ok and body.get('status') == 'completed' and body.get('output'):
                    result.update(ready=True, reason=None)
                elif (body.get('error') or {}).get('type') == 'insufficient_quota' \
                        or openai_credit.note(str(body.get('error') or '')[:400], 'checkout_readiness',
                                              status=r.status_code):
                    result['reason'] = 'provider_funding'
                    if openai_credit.available():
                        openai_credit.trip('checkout_readiness', str(body.get('error') or '')[:300])
            except (requests.RequestException, ValueError):
                pass
        if not result['ready']:
            _fallback_ready(result, result['reason'])
        _cache.update(result=result, expires=time.monotonic() + (120 if result['ready'] else 30))
        return dict(result)
