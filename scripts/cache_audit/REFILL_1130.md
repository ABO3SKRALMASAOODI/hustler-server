# Internal OpenAI admission and the November refill

The product, replay stacks and workflow skills share one PostgreSQL authority.
`app_kv.spend_fence.openai` defaults to $30 per UTC day, $3 per replay or lane
test, and $8 per workflow run. `enabled: false` stops internal OpenAI admission.
Production customer calls return before configuration, pricing or database
access; this budget cannot cap them. The existing provider-credit latch remains
separate. No live model calls were made for this change.
The existing local subscription proxy is not an API-credit call and bypasses
this admission guard; its separate deadline and retry policy are preserved.

## Hard admission

`worker/openai_budget.py` is mirrored byte-for-byte in backend and `_shell`.
Before **each wire attempt**, it locks the central authority, includes settled
cost and outstanding reservations, and reserves a conservative ceiling. Usage
settles that reservation; cached savings become available only after settlement.
Timeouts, interrupted streams, crashes and missing usage retain their reservation.
Only definite pre-generation refusals refund it. A ceiling breach closes the
pool for investigation. Missing identity, configuration, authority or audited
pricing fails closed with `OverBudget`; optional fallbacks must propagate it.
The workflow shell checks the stopped run before and after every stage.

Day limits reset at UTC midnight. Run totals survive midnight and process
restarts. All processes use the same central DB, including replays whose own
`DATABASE_URL` points at a disposable copy. A replay carries `GAUNTLET_RUN`;
a workflow carries `VALMERA_RUN`; standalone lane tests must set a stable
`OPENAI_INTERNAL_RUN`. Do not change these IDs to evade a stopped run.
The gauntlet runner passes its existing run ID to both backend and worker.
Helper thread pools carry the owner's spend identity without sharing DB handles.

The local file `~/.config/valmera/openai-spend.json` has the same keys (optionally
inside `openai`). It can tighten central dollar limits or turn admission off,
but cannot expand them. `OPENAI_INTERNAL_CONFIG` selects another file.
`OPENAI_BUDGET_DATABASE_URL` explicitly selects the authority; the Mac otherwise
reads the existing backend `.env` privately, and production uses its normal DB.
Secrets and prompts are never stored in the budget state.

The first guarded call seeds today's amount from the existing internal/replay
spend ledger and imports the local workflow journal once. Unknown legacy billing
closes admission. Conservative duplicate historical accounting is preferable to
making previously spent money available again. State lives in
`app_kv.openai_internal_budget`, in integer microdollars.

**The maximum is a ceiling, not an average prediction.** Hosted search must have
an explicit call bound and reserves its possible context growth. An existing
large search request can therefore be rejected below the nominal $8 run total.
Unpriced image/speech generation and unbounded binary audio/file requests are
also rejected internally until they have audited adapters. Customer behavior is
unchanged. No prompt is shortened, model downgraded, or tool silently disabled by
the budget. Do not replace these ceilings with historical average costs.

Inspect or operate through the existing ops CLI:

```sh
zsh .valmera/ops-loop/ops.sh openai-budget
zsh .valmera/ops-loop/ops.sh openai-budget --enabled off
zsh .valmera/ops-loop/ops.sh openai-budget --day-usd 30 --enabled on
```

Do not erase pending reservations merely to make a run fit. Reconcile uncertain
provider billing first. A killed in-flight request cannot be unsent; its reserved
amount remains included.

## Content equivalence and whole-clip hearing

`worker/tests/fixtures/cache_before.json` pins the actual pre-round-1 functions
from `dc936a33^`. `test_cache_content_equivalence.py` executes those and current
builders for footage batches, named pictures, critic, four greeting branches,
four concierge branches, fit, shell/writer/Director converters, and the common
cache wrapper. It compares exact nonempty text lines and multiplicity, image
bytes, registry values and order where order is meaningful. Prefix-stability
tests separately check identical serialized static prefixes and stable schemas.

These checks prove preserved **content**, not identical stochastic output or
unchanged instruction-role precedence. Named-picture checks explicitly changed
default image detail to `low` in round 1; their pixels and labels are identical.
The shell now honors an explicit detail setting. Those are reported exceptions,
not evidence of output equality. Budget controls and transcription-provider
selection are intentional round-2 behavior changes.

Footage hearing now extracts and transcribes one complete clip (within the
existing 900-second limit), returning the recognizer's real segment timestamps.
Local faster-whisper is the default. The content/model/options hash and lock
deduplicate repeats across paths and concurrent runs; even silence is cached.
Temporary audio is deleted, and the existing bounded transcript cache is reused.
`FOOTAGE_HEAR_PROVIDER=openai` explicitly opts into one `whisper-1` verbose-JSON
upload after credit becomes available. GPT-4o transcription does not supply the
required timestamps. Provider failures can use a separately identified local
memo; `OverBudget` cannot. Tests use synthetic audio and mocked recognizers,
not live ASR quality claims.

## Live proof: do not run until funded on 30 November

`live_proof_1130.py` refuses before 2026-11-30 UTC, without `--live`, while either
local or central funding is unconfirmed, or while the credit latch is closed.
Both configurations require `credit_available: true` and a timezone-aware
`refill_confirmed_at` on/after 30 November. A date alone is not funding evidence.
After observing the actual refill, use `openai-budget --credit-available on`
and update the local confirmation. Preserve any owner kill switch.

Run from a checkout containing this script, with the normal worker environment:

```sh
python scripts/cache_audit/live_proof_1130.py --live --out /path/to/proof-evidence
```

It sends each of eleven high-volume text/vision roles twice using real current
builders and identical synthetic inputs, and writes each response immediately to
`usage.jsonl`: actual input/cached tokens, share, usage, request hash and dollars.
There are no retries. The stable proof run ID is capped at $2.95 across restarts;
the subsequent four-second audio pair costs at most another $0.013 in reserved
ceilings. The global and $3 test limits also apply. Audio should upload once then
hit disk, and reports duration-based dollars without inventing cache-token usage.
Quota refusal persists a closed local latch and stops the proof.

The probe caps output at 512 and disables tool execution/search. It proves prefix
reuse, not complete editor outcomes, search-loop savings or transcription quality.
Small real prefixes may remain below the provider's cache threshold; the script
does not pad them or manufacture an 80% result. **This script has not been run.**

## Reconciliation

The retained round-1 audit explains $199.34 in individual calls and $54.59 in
additional run aggregates: $253.93 of the owner's $408, leaving **$154.07**.
Reconciliation needs the account-level November 14–15 cost/usage export with
request IDs and UTC boundaries, plus deleted replay request logs/DBs (27 DBs
and one manifest were missing) and usage for interrupted/unlogged calls.
Model-level dashboard totals cannot recover those request identities. No new
paid calls or broad data recovery were attempted merely to fill this gap.
