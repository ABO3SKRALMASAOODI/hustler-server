# Legacy material (v6-v8)

Kept only to resume runs started before v9. New runs use the top-level
`SKILL.md`, `references/` and `scripts/run.py`.

## Resuming a run whose `run.json` says `valmera-podcast-shorts-v7`

Both v7 and v8 runs use this state format.

- State: `scripts/run_state.py` (unchanged; `--help` per command). Its QC
  score field and caption-treatment checks apply to these runs only.
- Keep the run's frozen brief and taste profile. Do not relabel its accepted
  artifacts as v9 quality.
- v8 runs: `references/creative-v8.md`, `references/production-v8.md`,
  `references/editing-v8.md`, `references/premium-design.md` and
  `references/additional-reels.md` (now under `legacy/references/`), plus the
  helpers that stayed in `scripts/` (`compose_short.py`, `design_graphics.py`,
  `inspect_cut.py`).
- v7 runs: the `*-v7.md` files and the contracts in `legacy/references/`.
- Paths in old run notes that point to `references/`, `schemas/` or the
  scripts below now resolve under `legacy/`.

## Contents

- `references/`: v6-v8 creative direction, lane definitions, QC, export,
  editor and selector contracts. `style-lanes-v7.md` still documents the
  admin watermark placement modes (`frame`, `lower`, `scene`).
- `schemas/`: the JSON schemas used by `scripts/validate_contract.py`.
- `scripts/`: `run_registry.py`, `validate_contract.py`, `source_ledger.py`,
  `canonical_fingerprint.py`, `transcribe_candidate.py` and their tests.
  `validate_contract.py` and its dependants need `jsonschema>=4.18`.
