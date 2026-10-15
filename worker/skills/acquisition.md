# acquisition — when the footage cannot carry a device: plan the shots (plan_footage) and offer at most three ways forward, each with its cost and time

## Editorial decision principles

The strongest world devices were purpose-shot: type on a floor needs an
overhead with a hard shadow and a clean plate; a word behind a laptop
needs a laptop in front of the speaker; a fingertip card needs a hand
raised to the lens. When the user asks for one and the upload has no
such moment, the answer is never "not possible". It is a plan:

1. Say in one line what the footage lacks ("there's no surface in frame
   long enough to write on").
2. `plan_footage(brief)` — the shortest shot list that covers every
   device the brief asks for: <= 12 shot cards with framing, lens,
   camera, light, what to do, and a framing diagram; <= 20 minutes on a
   phone and a tripod.
3. Offer AT MOST THREE options, each with its cost and time, and let the
   user choose (`ask_user` with the options):
   - Shoot the list (≈N min, no credits until the upload).
   - Edit what was uploaded now (the devices it supports; the rest fall
     back to type in front or static placement).
   - Stand-ins: procedural light, layouts and type on black in place of
     the missing setups (never generated imagery).

Only offer the dialogue when it changes the result. If the uploaded
footage carries the idea another way, do that and mention the plan in
one line at most.

## Evidence to inspect

- `look_at` the candidate windows: a visible surface, an object in front
  of the speaker, a raised hand, a silhouette against light.
- `suggest_devices` returns no world candidate when the measurements do
  not support one — that is the signal to plan, not to force.
- The user's own words: "I can film more", "I only have this".

## Strong treatment patterns

- "I want my words on the floor like that reel" with a seated upload:
  plan_footage → 1 overhead floor shot (5 s empty plate first) + the
  existing A-cam; offer the three options.
- A product launch with only a screen recording: plan the desk overhead
  and hands shots; meanwhile edit the screen recording on a device stage.
- `plan_footage(brief, have=['talking head'])` skips the shots the user
  already has.

## Common failure modes

- More than three options, or options without a cost and time.
- A shot list longer than 12 cards or 20 minutes: cut to the devices the
  brief names.
- Promising generated footage: the generative lane does not exist.
- Asking before trying: if the upload can carry the idea, edit first.

## Verification procedure

- The plan's coverage line reads >= 90% of the brief's devices; anything
  missing is named.
- The options are numbered, at most three, each with time and cost.

## Repair ladder

1. Coverage under 90%: narrow the brief to its strongest devices and
   plan again.
2. The user cannot film: take the stand-ins option and say which devices
   changed.
3. The user films: on the new upload, `suggest_devices` will propose the
   world devices the new shots support.

## Through apply_edit_batch

The plan writes nothing. After the user films, the devices write as
usual, for example:

```json
{"action": "upsert", "layer": "fx", "id": "pl1", "value": {"id": "pl1",
 "type": "plane", "stage": "world", "start": 2.0, "end": 6.0,
 "src_t": 2.5, "quad": [[0.1, 0.55], [0.9, 0.55], [0.98, 0.95],
 [0.02, 0.95]], "surface": "floor", "motion": "static"}}
```
