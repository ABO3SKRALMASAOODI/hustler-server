# Offline cache audit (15 Nov 2026)

No model calls. `collect.py` opens production and preserved replay PostgreSQL
connections read-only, retrieving Nov 9–15 `llm_calls` into a private gzip file.
`report.py` joins recorded usage with a frozen workflow ledger and deduplicates
provider request IDs where present. It reports unknown usage separately.

Run with the local worker test Python, psycopg2 and tiktoken 0.12.0. Tokenizer:
`o200k_base`. This is a **text proxy**, not OpenAI's rendered multimodal context.
The collector never prints credentials. Do not commit the raw calls.

```
python scripts/cache_audit/collect.py --workspace /path/to/Valmera --out /private/evidence
python scripts/cache_audit/report.py --evidence /private/evidence --ledger /private/frozen-spend.jsonl
python -m pytest scripts/cache_audit/test_simulator.py
```

The simulator implements legacy 1024/128-token eligibility and key routing, and
modern explicit/message boundaries, concurrency, 30-minute sliding reads,
12-hour writes/upgrades, and write pricing classes. Exact machine routing,
hidden instructions, cached image tokens and web-search-internal turns require
provider/wire evidence; they cannot be derived from input-token totals.

Calibration requires complete inputs and <=2% token-weighted mean absolute
per-call cached-token error. Aggregate errors cancelling out is not a pass.
The recovered dataset **fails** this gate, so no after-cost forecast is certified.

## Implemented

- Sorted tool registry and canonical schema object keys without reordering
  schema arrays; stable role keys; 12h shared instruction boundaries and 30m
  project/conversation boundaries. No padding.
- Writer web-search calls retain implicit evolving-history caching.
- Footage look separates shared subject/lines from changing sheet data, and
  finishes the first actual batch before concurrent batches. Existing image
  batch coverage is retained; contact-sheet detail is retained.
- Named checks place subject context before the picture batch and request low
  detail for already <=448px thumbnails. Claude fallback removes that
  OpenAI-only image transport field.
- Preview critic puts its complete unchanged rubric before changing evidence;
  concierge puts reply rules before changing facts; greeting branch before stats.
- Exact transcript content/interval/model cache, atomic writes, process locks,
  successful-piece reuse after partial failure, no failed/Claude fallback result
  cached as an OpenAI result. Bounded to 128 MiB and 30 days on the host, also
  under the Mac janitor's existing cache root (explicit 128 MiB entry added).
- Content-free ordered prefix hashes and audio identities accompany future usage.

## Explicitly still open

The workflow ledger has usage but no request bodies. Director logs generally
have only model and max_tokens. Editor logs omit full tool schemas and replace
pixels with `[image attached]`. 28 preserved stack databases were unavailable;
169 worker/backend logs contained neither cached_tokens nor wire cache options.
These missing historical inputs cannot be rebuilt faithfully. Reordering also
does not prove identical stochastic model outputs.

The 80% site target and 60% whole-bill reduction are **not proven**. `footage_look`
has only 528 static tokens (809 with its extra brief), named checks 319, rank 368;
unique pictures/project data dominate their input. Stable short prefixes cannot
cross the cache minimum without adding content. Batching already exists.

One initial transcription call per long clip is **not implemented**: the current
model returns text without word timestamps. Replacing 12s timed pieces with one
whole-clip text would break `said_at`, moment selection and precise voice cut-ins.
A separately validated alignment path is needed to merge these while preserving
those outputs. Repeat successful clips/pieces now incur zero OpenAI calls.

Evidence: `.valmera/week-1112/lanes/CACHE-FIX/{TABLE.md,report.json,sources.json,
log-inventory.json,tests.log,backend-tests.log}` in the owner's workspace.
Official rules: https://developers.openai.com/api/docs/guides/prompt-caching
