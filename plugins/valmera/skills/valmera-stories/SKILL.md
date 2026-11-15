---
name: valmera-stories
description: Make a finished narrated vertical story film about a founder, a company or a topic (workflow 3, cut like the @metromedia.house reels) with one command and at most a few words (/valmera-stories, or /valmera-stories <name or topic>). The run finds a true story worth telling on the web, checks every fact against its page, writes it for the ear, narrates it with the default narrator (Phonon 31M Easton), cuts real footage and free photographs of every person, place and event it names at the judge's pace (about 16 pictures a 10 s, never one picture twice, collages, flash frames, finishes by section, display words, captions word by word), lays the references' sound design and chosen music, delivers one complete folder once and deletes its work. Use when the user asks for a story, founder story, company story or topic story film, with or without a subject. Does not publish.
---

# Valmera story films (workflow 3)

Never the same idea told the same way twice (owner, 14 Nov 2026): the ideas stage sees this workflow's delivered ideas from `.valmera/workflow/delivered.jsonl` and a gpt-6-sol judgement takes out any exact repeat before scripting; a new angle, version, structure or story is welcome (the same events told in a new structure, such as a dated timeline, a list or another narrator's frame, with new narration and beats, are a new way), but the same narration or beats with only a new visual look is a repeat. Only a folder inside the owner's delivery folders (Podcast Shorts, Story Films, Marketing Films, Long Films) is recorded; any other `--out` is a test. A test run uses `story.py --test-out <dir>` (or `--test-out=<dir>`), which never delivers or records; any other flag is refused, never read as the topic. A remake of a delivered film (`run.py --args '{"remake": ...}'`) is test-only: an `--out` outside the delivery folders, or it goes through the repeat judgement like any idea.

## Start it: one command, nothing or a few words

```
python3 /Users/masaoodi/Documents/Valmera/hustler-server/plugins/valmera/skills/valmera-stories/story.py [name or topic]
```

- No words: the run chooses its own story. A name, a company or a topic ("Claude Shannon", "the Walkman"): the
  run finds the strongest true story inside it. Ask the user for nothing else: no style, no length, no song.
- Run it in the background (`run_in_background: true`); it takes 30 to 90 minutes. Do not poll; you are woken when it exits.
- Its last line is one JSON object: `delivered` (the folder), `title`, `style`, `measured` (pictures a 10 s, collages,
  flashes, finishes), `music`, `spend_usd`, `work_empty`. Tell the user the folder once, with the title.
- Exit 2 (`stopped`): run the same command with the same words; it continues where it stopped. Exit 1: report the reason.
- `--out <folder>` delivers somewhere else (default `/Users/masaoodi/Documents/Valmera/Story Films/`).
- Never rate the film yourself: a separate Opus session rates it on rubric v3.

The folder: `film.mp4` (with the music Valmera chose), `film_clean.mp4` (the same film without music), `film.json`
(every fact with its quote and URL, every clip and photograph with its source, licence and time range, the music
credit, the narration's gate, the measures) and `SOURCES.md` (the credits to paste into a post's caption: the footage
is not licensed, by the owner's decision of 13 Nov). Nothing is published; nothing is ever added to a delivered folder.

## How to run it (Claude Code and Codex alike)

- Claude Code: `/valmera-stories` or `/valmera-stories <name or topic>`. Codex: `$valmera-stories` or
  `$valmera-stories <name or topic>`. Either way the words after the command, if any, are the topic, passed to
  `story.py` as its only arguments; with none, the run picks its own story.
- Start `story.py` as a background job (`run_in_background` in Claude Code; a background terminal in Codex), do not poll
  and do not end the turn while it runs, read the last JSON line when it exits, and on exit 2 start the same command
  again. Measured on 14 Nov 2026: a name gave a delivered film in 84 minutes for $2.81 of OpenAI credit.

## What the run does

| Stage | What happens |
|---|---|
| ideas | The writer model (OpenAI, with web search) finds four true stories about the words given, or of its own choosing, each told through one decisive moment, with the people, places and events a camera filmed, and each with its spine (hook, turn, payoff, close) as claims, every claim with one sentence from a readable page, primary sources first; each idea's free photographs are probed, and its spine too, cheaply (`wf3.spine_probe`: no model call, each claim's sentence fetched and looked for on its page); ideas are tried in order of strength, pictures and verified spine (`wf3.idea_rank`), in the style that fits among those not used in the last three films. |
| script | VERIFY FIRST, then WRITE FROM THE BANK (15 Nov: true AND gripping; truth is never traded for punch, and punch never dies for truth). The chosen idea's spine is researched before a word is written (`writer.spine_bank`, report `spine<n>.json`): its own sources fetched (every door below) and copied from (one call copies the sentences that state each claim and tell the rest of the story; each sentence is verified word for word), a model judges which claims the verified quotes state in full (who did what, the reversal, the figure and who paid it), and every claim not stated gets up to three search rounds with queries never tried before, primary sources first (court records, filings, the company's own statements, news written at the time), then any readable page. A quote is a meaningful fragment: four words or more besides its numbers, never a bare number or date (`writer.thin_quote`). A spine that cannot be verified gives the next idea (at most three). The writer then gets only the verified bank (quote, page, whose statement, when) and the spine, with no web (`writer.write_from_bank`): every line is written from it, the hook, turn, payoff and close each carry the idea's point (the reversal, the price, the irony), and a line cites only the facts it uses. Before the ear every line is judged on its quotes and the spine is held (`writer.hold_spine`): a beat whose line lost its point is rewritten on the bank, and when the bank cannot carry it its claims are searched again; still flat, the script is written again (twice at most), then the next idea. The close is a core beat: it reframes the hook (the hook's collision said again, resolved, never a moral) on the hook's verified facts, so a close claim of its own that no readable page states never costs a verified story (`close_from_hook` in the record); it is restated, never dropped. Then, as before, each script is checked line by line: each quote is fetched here and must be found in its page's readable text, every number on screen must stand in a verified quote (`writer.verify`; only the numbers of a list the film counts out, display role `ordinal`, are positions, not figures). A page that will not open is asked again with a browser's headers, then read through Wikipedia's API, its Wayback copy or another copy the writer cited; a fact still unverified is sourced again by one search call, and when no readable page states it, the lines resting on it are rewritten without it or dropped and the script is checked again (`writer.settle`). Every line that cites a fact whose quote was replaced, and every rewritten line, is judged by a model (gpt-6.1-sol) against the verified quotes it cites; an unsupported line is rewritten on those quotes and judged again (at most twice), else dropped. Words the narration gives as someone's own (the writer's `quotes` field, and the spans the judge names) must stand in the line and word for word in a verified quote of that person the line cites (`writer.speech_backed`); a supported line keeps citing only the facts the judge says it uses. After a rewrite one judgement over the whole script catches a line that repeats another; it is rewritten once, else dropped when not core. The Wayback door waits on a 429 at most twice and 10 s per page, then moves on. The stage stops only when no idea's spine can be verified, or a core line (hook, turn, payoff, close) cannot stand; it says which and why. `run.py run ... --until script` runs the stage alone and delivers nothing. Then written for the ear (`ear.py`, gpt-6.1-sol: sentences of 9-14 words, hinges, verdicts, the name landed alone, the stress word late, joins of 0.18-0.26 s, hard words in their spoken form; each segment carries the verified quotes it stands on and may add no fact beyond them, and each core segment its spine point, said at least as sharply), audited by a second blind call against those quotes, verified again. Then the final support pass, the last text gate (`writer.final_support`, `wf3.final_gate`, report `script_final.json`): EVERY line is judged against the verified quotes it cites in one batched gpt-6.1-sol call; an unsupported line is rewritten on its quotes in the ear's style (`ear.KEEP_STYLE`) and judged again, at most twice, else dropped; a core line is restated or the stage fails; quoted speech matches whole words in order (`writer.has_words`: Unicode-normalised, apostrophes unified, never a substring), and a span the judge names that stands verbatim in no cited quote makes the line unsupported whatever its verdict (fail closed); a core line whose words changed since the spine was held is compared with those words and with its spine point by a model and, when it lost its claim or tension, rewritten once more on its quotes and judged (new words that do not stand are not kept, and the reason is recorded in `point_failed`). The narration is frozen after it: annotate changing a word stops the stage. Then the picture brief on the final words (`writer.annotate`, one OpenAI call, the words never changed): per line what a picture must show (`see`: the thing, the story time and version, the moment), the writer's devices (`finish`, `colour_turn`), each display item anchored to its spoken word (`word_i`) and the caption phrases. A re-made film gets the same pass on its own words, so its narration is reused. |
| supply | The narrator (`voice.py`, below); real footage per line (`worker/footage`: YouTube and archive.org, every frame checked by a vision model, a person held to one face, a clip every 0.45 s of a line, windows of 1.0-1.6 s, five sources a subject, the brief's moments searched for themselves); eight free photographs a named thing (Commons, Openverse, checked); every clip's content motion and inner cuts measured (`reel.clip_motion`: optical flow, the camera move removed), clips of one take grouped (`reel.take_groups`); the people's photographs searched first (their anchors find a person's own voice), then the footage engine and the other photograph searches side by side, the search-time check asking who or what, never when; every picture judged against the lines it may carry (`fit.py`: the exact thing in any of its own versions and never a stand-in, at the time and version the words under it mean, nothing contradicting the words, the brief a wish list and the grade a ranking; gpt-6.1-sol vision, cached in `supply/fit.json`); a story reel stops only after both engines and fit, when fewer than two named things have a fitting photograph or footage take, naming for each thing what was searched and why each candidate was rejected (`supply/refusal.json`), and a line with fewer fitting pictures than it wants (one every 0.5 s) gets one top-up footage call; a person speaking on camera is measured on the clip's own frames (`reel.talking`: the lip gap changing frame to frame) and never plays full frame under the narrator; up to two real voices cut in where the person said it, levelled to the narrator's loudness within 1 LU. |
| score | The story reel (`reel.py`), then the taste check (`film.taste_check`), then the story gates (`wf3.stories_gates`): every planned shot judged against the words under it (`fit.audit`), a failing shot dropped on that line and the plan built again (three rounds at most); black and white on 4-35% of shots or the devices halved once; repeats by distinct take at most 5%. |
| render | Valmera's engine (`worker/scene`) through `heavy.sh`, then the brand finish (watermark, end card), the film's music (`worker/music_film.py`: a track from the licensed library chosen the way the references choose it, its build on the turn and its drop on the payoff) on `film.mp4` and none on `film_clean.mp4`. |
| deliver | Once, complete, then the work folder is deleted (the shell checks it is empty). |

## The story reel (`reel.py`), against the judge's gaps

The owner rated the first three story films 4-5 of 10 (`week-1112/lanes/RATINGS/2026-11-13-O.json`, `-PROOF.json`).

- **Pictures:** only a picture whose verdict passes for its line (`fit.py`), never one recycled across lines. Per line: its
  own fitting clips (the most moving first, played on their most moving part), then fitting clips found for other lines
  and not yet played, then fitting photographs (at most 10% of shots on lines with footage), then a designed type card
  from the line's own display item, then the current take held on a camera reframe (at most 1.2 s, logged as the line's
  shortfall). Shot lengths are drawn from the judge's distribution (median 0.5 s), by the segment's pace and beat; a
  line that lists three things bursts (4-8 pictures of 2-5 frames). A picture is new only by distinct take.
- **Collages:** lines that name several things (the writer marks them; then the longest lines naming two or more) land as
  tilted prints at their own aspect (0.78-0.86 of the width) that keep moving, stacking three to five deep.
- **Flash frames:** white frames of 1-2 frames at full strength (also inside bursts), negatives on about one cut in twenty
  on the voice's strongest stresses; the caption pool leaves the white frames whole.
- **Finishes:** colour under the film's look. Black and white, a film gate or scanlines only on the segments the writer
  marks (`finish`, ranked), held to 12-25% of the voice, never the payoff, the close or anything after `colour_turn`,
  whose first shot comes up from black and white to colour over seven frames.
- **Type:** display items land on their anchored word (`word_i`); a person's name large at their first mention; the
  payoff's figure held 1.7 s; an item not placed is recorded (`beats.display_dropped`); captions by the writer's phrases.
- **Checks on the film** (stories only): `checks.stories.picture_fit` (each rendered shot against its words; the film is
  not ok while any second fails), `colour` (black-and-white share, colourfulness, the turn's step), `voices`.
- **The engines:** the judge's pace and camera moves (`scene.pace`), one look solved on the film's pictures (`scene.looks`),
  a soft first frame on one new picture in three, captions inked word by word at one seat, the writer's display strings
  landing large on the words that say them, the references' sound design (`score.sound`, SND3).
- **Styles** (never one of the last three delivered): `reel` (bleed, yellow grotesk), `pinboard` (prints on a dark board on about
  half the lines, orange serif), `archive` (the past in a film gate with projector flashes, cream serif italic), `window`
  (each picture in a framed window over itself out of focus, cyan condensed capitals). The older `character-sheet`,
  `whole-page` and `one-photograph` (`film.py`) run only when named: `run.py run --args '{"style": "..."}'`.
- **Alien devices** (`worker/scene/alien`, verdict of 14 Nov): never `diecut`, `answer` or `lamp`; `rhyme` and `arc` only,
  only when a direction asks (`direction.json` `alien`), never in a first film.

## The narrator (`voice.py`)

Phonon 31M Easton (the owner's choice), read through the narrate tool's own parts (`_shell/narrate`), closing the
independent voice check's gaps (`lanes/RATINGS/2026-11-13-VO2.json`):

- whole sentences a generation (one to three, never a sentence cut between two), so there are a third as many joins;
- six takes a generation, three at the runtime's temperature as the owner heard it and three freer; every word heard
  first, then the tool's prosody score plus the line's pitch range and how high it restarts over the last line's end;
- joins of 0.22 s, 0.40 s only where the story turns; room tone (-46 dB under the speech) in every pause;
- the writer's one or two focus words per line ridden to +4.3 dB over their line, measured per word;
- each line set by its beat and energy, scaled until the read measures about 3.3 LU (EBU R128); -14 LUFS, under -1 dBTP;
- the VOICE gate (`narrate.py check`: word error under 1% on the spoken form, pace, pitch, loudness, joins) is in `film.json`.

## Rules

Only true things, with receipts. Real pictures of the real thing; no stock lookalikes, no generated faces. Never publish,
never touch a customer's project, never alter the end card or the watermark, never rate the film. Model calls run on
OpenAI (`story.py` reads the key from the backend `.env`, never printed, and unsets any Anthropic key). A run's state is
in `.valmera/workflow/stories/` (its own work root, so a start sweeps only its own stopped runs; the ledger of delivered
stories and styles is there too).

## Files

`story.py` the one command; `run.py` the shell launcher (`run.py run --out <dir> --args '{...}'`, `--resume <id>`,
`status`); `wf3.py` the stages; `writer.py` the story finder, writer and fact check; `ear.py` writing for the ear;
`voice.py` the narrator; `pictures.py` photographs and faces; `reel.py` the story reels; `film.py` the older styles and
the shared clock, captions, display words, engines and taste check (workflow 1's films import it too);
`styles.json` the cards; `REFERENCES.md` the bar the writer reads; `test_film.py`, `test_reel.py`. The shell is
`../_shell` (lane SKILL-WF1); the engines are `worker/scene`, `worker/score`, `worker/footage` beside this plugin.
