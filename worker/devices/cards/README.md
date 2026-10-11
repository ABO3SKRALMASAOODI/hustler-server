# Device cards

One creative device per card: `<id>.yaml` (authoring) and `<id>.json`
(compiled; the only form a lane reads). The contract is
`worker/devices/INTERFACES.md` §5; `worker/devices/schema.py` enforces it.

- The track that ships a device owns its card.
- After editing a YAML card, run `cd worker && python -m devices.compiler`.
  `tests/test_w0c_cards.py` fails CI when a JSON is stale or a card breaks
  an invariant.
- A card whose `owner_flags.adds_zoom` or `adds_sound` is true is never
  proposed unless the user asked (`devices.proposable`), and never part of a
  look or format-pack default.
- `status: dark` resolves to `omit` (reason `dark`) unless its `flag` is on.
- `rungs` are ordered best-first and end with `omit`; a substitute names
  another card or one of `devices.BUILTIN_SUBSTITUTES`.
