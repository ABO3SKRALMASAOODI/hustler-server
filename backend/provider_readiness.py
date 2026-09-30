"""Bounded readiness checks before selling a new hosted editing contract.

No project is touched and no customer credits are consumed. Cache across web
requests so checkout retries don't generate repeated model calls. Legacy
contracts and their renewal webhooks never pass through this check.
"""
import os
import threading
import time
import requests

_lock = threading.Lock()
_cache = {}

def advanced_readiness():
    now = time.monotonic()
    with _lock:
        if _cache and now < _cache['expires']:
            return dict(_cache['result'])
        key = os.getenv('OPENAI_API_KEY', '')
        result = {'ready': False, 'model': 'gpt-6.1-sol', 'reason': 'provider_unavailable'}
        if key:
            try:
                r = requests.post(os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/') + '/responses',
                    headers={'Authorization': 'Bearer ' + key},
                    json={'model': 'gpt-6.1-sol', 'input': 'Reply OK.', 'reasoning': {'effort': 'low'},
                          'max_output_tokens': 512, 'store': False}, timeout=(3.05, 12))
                body = r.json()
                if r.ok and body.get('status') == 'completed' and body.get('output'):
                    result.update(ready=True, reason=None)
                elif (body.get('error') or {}).get('type') == 'insufficient_quota':
                    result['reason'] = 'provider_funding'
            except (requests.RequestException, ValueError):
                pass
        _cache.update(result=result, expires=time.monotonic() + (120 if result['ready'] else 30))
        return dict(result)
