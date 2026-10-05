# Story and design direction

## The editorial decision

Choose the moment before choosing its effect. A strong short changes what the
viewer understands or feels: a surprising answer, an earned reversal, a useful
principle with evidence, a revealing admission, a clear disagreement, or a
specific story with stakes. Celebrity recognition alone is not the payoff.

Read the full passage around a candidate. In the assignment record four short
sentences: **viewer question, immediate hook, turn/evidence, final payoff**.
If the payoff merely restates the opening, look for a better passage. If the
first ten seconds are a long interviewer setup, seek a self-contained start
closer to the answer. Keep a necessary question or qualification. Never splice
words into a claim the person did not make, reorder causes, or turn a cautious
statement into certainty. Do not fake personal testimony in headlines.

Rank candidates against each other, including duplicates of the same lesson.
“Interesting topic”, “famous person”, and “complete sentence” are insufficient
selection reasons. Describe the actual tension, image or insight. A batch may
contain a broad range of subjects without packaging every paragraph.

## Use references as visible evidence

The saved Palantir examples show **hierarchy**: small connecting language and
larger meaning-bearing words, selective accent, speech-led reveals and specific
visual evidence. They do not show identical three-word captions throughout.
The headline examples show deliberate stillness, a wider picture, a bold topic
above it and separate changing subtitles. Their simpler cutting works because
the speaker and idea carry the passage. Apply the relevant traits; don't import
another creator's logo, music, unlicensed footage, personal endorsement or outro.

At phone size, compare your opening, strongest beat and payoff against the
reference. Is the idea as clear? Is the face/picture large enough? Does one
element lead the eye? Is there a reason to continue watching? A style name or
matching color does not answer these questions.

## A small art direction, not a template

Before the edit, commit to one visual idea in the assignment: what the viewer
will see at the turn/payoff and why. Examples of relationships:

- A misconception gives way to the corrected idea; type changes on that turn.
- A consequential figure dominates while its meaning stays readable below it.
- Two outcomes are visibly contrasted, with the decisive difference emphasized.
- A specific machine/process/gesture supplies visual evidence while speech continues.
- A vulnerable admission stays on the face; quieter captions let the pause land.

These are possibilities, not required objects or an effect quota. If the plan
is simply “add captions, put a headline above it”, assess whether the chosen
conversation is strong enough to sustain the headline format. A fast visual
format requires an actual visual argument. Don't disguise generic slides as B-roll.

## Captions should perform with the speech

Use a clear dominant font and semantic phrases, not mechanical word buckets.
Emphasize the consequential word, number or verb—never “the”, “and” or “well”
just because it occupies a preset slot. Keep active-word color when requested;
it is separate from size hierarchy and movement. A restrained rise/settle or
brief scale landing can articulate speech without making every word bounce.

Valmera supports native phrase layout, `emphasis_words`, hierarchy and entrance
motion. Choose the preset for the actual delivery, then set only the necessary
overrides. `reels`, `podcast`, `stacked` and `clean` are different visual grammars;
inspect a rendered passage before deciding. Read Valmera's captions tool skill
for supported values. Do not blanket-set `animation:none` and `emphasis:none`.
Stillness is legitimate for calm or emotionally exposed passages, with a clear
reason. It must not become the fallback that flattens the whole batch.

Judge at approximately 360px phone width. Dialogue should read comfortably at
normal playback without zooming, including the densest phrase. Avoid a tiny
subtitle line under a giant headline. Headline and dialogue occupy separate
regions, clear of eyes/mouths, platform controls and the corner brand mark.
Caption position should remain stable across a coherent shot; don't build a
new placement track for every word. Use phrase or shot boundaries when a move
is actually necessary.

## Cards that explain something

Cards are an editorial beat, not a mandatory decoration. A word can land large
on a clean contrasting field; a contrast can reveal two ideas; a key number can
have a concise label. Keep the transcript audio intact underneath. Do not use
an inserted silent title-card clip when the point is to emphasize ongoing speech.

The observed failed pattern is a small heading, a thin vertical rule, and a
tiny explanatory sentence flashed for .8 seconds. Replace that with one strong
visual statement, large enough to read and held long enough to understand.
A card should make the relationship visible: differentiated scale for the
consequence, a reveal on the turn, spatial contrast between alternatives, or
one unmistakable figure. Replacing the face with the same centered heading
and subtitle every time is still a slide deck.
No generic headings such as PROGRESS, THE FUTURE or IMPORTANT without the
story-specific idea. Never add fine print that exists only to fill space.

Use `scripts/design_graphics.py` for a fast native starting composition:
`word`, `statement`, `contrast`, or `metric`. It returns editable text/vector
layers and minimum reading-time validation. Supply original story-specific
copy, exact cue times, palette and purpose. Its presets are starting points;
compose custom native layers when the story deserves a different treatment.
The tool does not decide facts, taste, rights, or whether the effect is warranted.

Speech-emphasis cards first become readable on the actual spoken cue. If the
card paraphrases an idea rather than duplicating speech, allow enough time to
read it alongside the audio and keep any needed qualifier. Avoid subtitling
the exact same words twice. A native text item can own a brief caption mute;
a persistent topic headline uses `mute_captions:false`.

## Momentum and visual evidence

Choose B-roll for the specific noun, action, mechanism or consequence on screen.
When the speaker says “engines”, an operating engine is more informative than
an unrelated city at night. Verify identity and context; don't pass archival
footage off as the exact event being discussed. Use licensed or authorized media.
Keep a small shared asset registry with source, usage rights and useful ranges.
One coordinator retrieval can serve several editors; don't repeat the search.

In fast conversation, vary holds with the delivery and visual action. A sharp
word hit and a 2-second evidence shot may belong together; eight identical
.8-second cards do not create pacing. No repeated visual moment within a short.
Stop adding effects when they weaken comprehension. Silence in the montage
and action formats is deliberate; no music or SFX should fill it by habit.

## Creative review before technical polish

The reviewer sees the actual result, not the editor's favorable summary. State:

1. What makes the opening worth watching and where the payoff lands.
2. The strongest designed moment and the exact reference trait it achieves.
3. The weakest moment, with a timestamp and its effect on the viewer.
4. Whether the short would survive with the title/celebrity name hidden.

Compare the result against the assignment, the best relevant reference and the
other candidates. A technically clean but forgettable result needs an editorial
or design revision, not another thousand-frame inspection. A score cannot
substitute for this judgment; never stamp every candidate 92 by convention.
