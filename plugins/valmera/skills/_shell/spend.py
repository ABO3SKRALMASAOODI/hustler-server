#!/usr/bin/env python3
"""Read the coordinator's OpenAI ledger. No API calls; no prompts in the output."""
import argparse
import collections
import json
from pathlib import Path
from openai_cache import DEFAULT_LOG


def summarize(path, run=None):
    groups = collections.defaultdict(lambda: dict(calls=0, usd=0.0, unknown_cost_calls=0, prompt_tokens=0, cached_tokens=0))
    if not Path(path).exists():
        return []
    for line in Path(path).read_text().splitlines():
        row = json.loads(line)
        if run and row.get('run') != run:
            continue
        g = groups[(row['skill'], row['run'], row['purpose'], row['model'])]
        g['calls'] += 1
        if row.get('usd') is None:
            g['unknown_cost_calls'] += 1
        else:
            g['usd'] += row['usd']
        g['prompt_tokens'] += row.get('prompt_tokens', 0)
        g['cached_tokens'] += row.get('cached_tokens', 0)
    return [dict(skill=k[0], run=k[1], purpose=k[2], model=k[3], **v,
                 cache_read_ratio=v['cached_tokens'] / v['prompt_tokens'] if v['prompt_tokens'] else None)
            for k, v in sorted(groups.items())]


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--log', default=DEFAULT_LOG)
    ap.add_argument('--run')
    args = ap.parse_args()
    print(json.dumps(summarize(args.log, args.run), indent=2))
