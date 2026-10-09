# Editor playbook

You own one child short at a time: its `project_id`, Look, structure and
brief. Make it the best short in the batch in about 15-25 Valmera calls and
1-2 renders. You do not spawn agents, touch siblings, export, or approve your
own work. Read [looks.md](looks.md): **Shared grammar**, your structure, and
your Look block. Nothing else is required.

## 0. Once per editor (reuse for every later short)

- `list_motion_templates()`: the live template list with params and sound
  cues. Params change; never guess them from memory or from these docs.
- `list_sfx_kit()`; and `list_music_library(mood)` when the brief has music.
- Read a tool's schema before its first use. Do not read Valmera backend
  source code to guess EDL fields; typed tools and `get_edl` show the shapes.

## 1. Read the assignment and check the story

The brief gives the Look, structure, music switch, the story in four lines
(viewer question, hook, turn, payoff), a beat sheet with source-time word
cues, and art direction. Call `get_kept_transcript` once and confirm:

- The first 1.5 s answers "why keep watching?" If the child opens on routine
  interviewer setup, tighten inside the selected range with `keep_segments`
  so it starts closer to the answer; a faithful on-screen context line may
  carry the question.
- The turn changes or deepens the idea and the payoff resolves it. Stop on the
  strongest sentence; the payoff needs 1.0-1.5 s of hold before the end.
- Keep every qualifier ("I think", "probably", "in our case"). Never splice
  words into a claim the person did not make, reorder causes, or turn caution
  into certainty. Cuts remove setup and filler, not meaning.
- The editorial program fits 10-40 s (montage editorial at most 25 s); the
  native 5 s Valmera ending is added at export and must not be trimmed.

If the story fails these checks, say so in the handback instead of styling it.

## 2. Beat sheet in output time

After any cut, read the program words once more (`get_kept_transcript`) and
write the beats you will execute, in output seconds:

```text
hook    0.00  landing 1.16x + whoosh_soft; hook_title "Computers look like *garbage*" 0.15-2.6
hero 1  6.42  "garbage"   word_slam serif, start 6.22 (lands +0.2)
turn   14.80  "ten million" counter 0 -> 10M, tick per step, punch 1.15x
hero 2 21.10  camera change -> landing; marker_text "whether they look great or not"
payoff 33.60  "look great" word_slam + chime; hold to 35.0; music button if on
```

Every timestamp comes from a tool result. Readable frames land on the word's
audible onset (0-3 frames early); `word_slam` lands 0.2 s after its start;
`phrase_build` rows take item-relative `at` times. Check shot changes with
`get_shots`, and batch `look_at(times=[...])` into one call to see the faces
and the negative space you will design into.

## 3. Execute in a few deliberate calls

Order matters: cuts first (they move every later time), then frame, captions,
designed beats, camera, transitions and sound. If you must re-cut later,
recheck every output-timed item.

| Step | Calls | Notes |
| --- | --- | --- |
| Cuts | 0-1 `keep_segments` | only to tighten inside the range |
| Look | 1 `apply_look(name)` | sets caption look, grade, grain, base transitions; read what it set |
| Captions | 1 `add_captions(mode='from_transcript', style={...})` or `set_caption_style` | `style.motion_look` per Look; `emphasis_words` = the meaning-bearing words; keep the active-word accent if the brief asks |
| Frame | 1-2 `set_frame` (or `auto_reframe`), `set_picture_card` | full-bleed with a per-shot `focus_track` (source seconds), or a card with a designed `background` |
| Headline | 0-1 `set_editorial_graphic(kind="headline", speaker, text)` | headline-conversation only; verified speaker first |
| Designed beats | 3-6 `add_motion_graphic` | one per beat, `sfx` left on, `purpose` names the beat, stable `id` |
| Camera | 3-6 `add_zoom` | `landing`, `punch`, `pulse`, `push` per the Look |
| Transitions | 0-1 `set_transitions` | base style, `scope='scene'` |
| Extra sound | 2-4 `add_sfx(storage_key='kit:...')` | transitions, the payoff, the hook; templates already cue their own |
| Music | 0-1 `add_library_music` | per the switch; bed -20 dB ducked; montage leads |

Plain layer changes you already know the shape of may go together in one
`apply_edit_batch`; prefer typed tools when unsure. One child's writes are
serial. On a `REJECTED` result, correct the arguments from the message and
retry once; a transient error (for example "shard is busy") gets one retry
after a short pause, then report it.

### Do

- Fill the frame: full-bleed when the face crop needs at most 2x upscale;
  otherwise a card covering at least 0.55-0.65 of the canvas on a `blur` or
  gradient background. Archival footage is accepted as is; type stays sharp.
- Captions with a cap height of 3-4% of frame height, in face-free space,
  stable within a shot; 1-2 accent words per phrase.
- Hero type at 8-30% of frame height on the exact word; hold 0.8-1.6 s;
  exits faster than entrances.
- `layer='behind_subject'` only inside one continuous shot (no cut in the window).
- B-roll that shows the exact noun or action, licensed, with provenance in the
  handback; every still pushes or pans.
- Sound on every structural event; a stop-down of 50-280 ms before a reveal
  is a choice, longer silence is a defect.

### Don't

- A picture band on a flat black canvas, or a fade from black on frame 1.
- A whoosh on every caption, a punch every sentence (keep 4 s between punches),
  a zoom across a cut.
- Graphics over the face for more than 1 s, or under the corner brand mark.
- Numbers, quotes, notifications, chats or posts the source does not support;
  a reference person's name or identity.
- Downloading previews to re-encode, local ASR, re-uploading or re-indexing
  the source, or hundreds of frame dumps.

## 4. Render once

`render_preview(quality='approval')` renders the complete edit and now waits
longer before returning. If it still hands back a running job, call
`wait_for_job(job_id)`; each call waits a bounded time, so a few calls at most.
Never loop on short polls, never start a second render of the same version,
and never render after each operation.

## 5. Self-check, then fix only the weakest moment

1. `watch_video(render=false)` on the whole edit at 1x: the hook, the pace,
   the sound, the payoff.
2. One `look_at(rendered=true, output_times=[...])` with the hook (0.3 s,
   1.0 s), every hero moment, the densest caption and the payoff.
3. `audit_captions` once if you changed captions; read the render's warnings.
4. Measure against your Look's targets: hook at or before 0.6 s, visual
   change rate, hero count, picture area, sound present, payoff held.

Fix real defects (clipped words, collisions, unreadable or wrong captions,
silence, an exposed edge or one-frame pop) and the single weakest designed
moment. Re-render only if the fix changed pixels or sound that matter;
two renders is the ceiling.

## 6. Hand back

Return a note of at most 10 lines plus `project_id`, `edl_version` and the
preview link:

```text
s07 · kinetic-poster · fast-conversation · EDL v14 · 1 render
Hook: 0.2s landing+whoosh, hook_title by 1.1s ("Why fonts were garbage")
Heroes: 6.4 garbage slam; 14.8 counter 10M; 29.0 typeface cycle
Payoff: 33.6 "look great" slam + chime, held 1.3s
Sound: 11 kit cues; music off (conversation default)
Targets: change ~0.4s, picture full-bleed, 3 heroes
Weakest: 18-20s talking head with only captions (pushed 1.05x)
Assets: Lisa photo (Wikimedia, CC BY-SA 4.0, credit in handback)
```

Optionally attach a handback JSON (`music`, `broll`, `required_credits`,
`rights_note`, `headline`, `kept_transcript`, `kept_source_ranges_s`) so the
manifest is complete. Do not self-score.
