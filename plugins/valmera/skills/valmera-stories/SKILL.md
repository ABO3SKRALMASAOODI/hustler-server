---
name: valmera-stories
description: Make a finished narrated vertical story film about a founder, a company or a topic (workflow 3, cut like the @metromedia.house reels) with one command and at most a few words (/valmera-stories, or /valmera-stories <name or topic>). The run finds a true story worth telling on the web, checks every fact against its page, writes it for the ear, narrates it with the default narrator (Phonon 31M Easton), cuts real footage and free photographs of every person, place and event it names at the judge's pace (about 16 pictures a 10 s, never one picture twice, collages, flash frames, finishes by section, display words, captions word by word), lays the references' sound design and chosen music, delivers one complete folder once and deletes its work. Use when the user asks for a story, founder story, company story or topic story film, with or without a subject. Does not publish.
---

# Valmera story films (workflow 3)

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

## What the run does

| Stage | What happens |
|---|---|
| ideas | The writer model (OpenAI, with web search) finds six true stories about the words given, or of its own choosing, each told through one decisive moment, with the people, places and events a camera filmed; each idea's free photographs are probed; the best idea with pictures wins, in the style that fits it among those not used in the last three films. |
| script | Written in the chosen style with every fact quoted from its page; each quote is fetched here and must be found on its page, every number on screen must stand in a verified quote (`writer.verify`). Then written for the ear (`ear.py`: sentences of 9-14 words, hinges, verdicts, the name landed alone, the stress word late, joins of 0.18-0.26 s, hard words in their spoken form), audited by a second blind call, verified again. |
| supply | The narrator (`voice.py`, below); real footage per line (`worker/footage`: YouTube and archive.org, every frame checked by a vision model, a person held to one face, 1-3 s clips, asked for 1.7 times the clips a line); six free photographs a named thing (Commons, Openverse, checked); a person speaking on camera is measured on the clip's own frames (`reel.talking`: the lip gap changing frame to frame) and never plays full frame under the narrator; up to two real voices cut in where the person said it, levelled to the narrator's loudness within 1 LU. |
| score | The story reel (`reel.py`), then the taste check (`film.taste_check`). |
| render | Valmera's engine (`worker/scene`) through `heavy.sh`, then the brand finish (watermark, end card), the film's music (`worker/music_film.py`: a track from the licensed library chosen the way the references choose it, its build on the turn and its drop on the payoff) on `film.mp4` and none on `film_clean.mp4`. |
| deliver | Once, complete, then the work folder is deleted (the shell checks it is empty). |

## The story reel (`reel.py`), against the judge's gaps

The owner rated the first three story films 4-5 of 10 (`week-1112/lanes/RATINGS/2026-11-13-O.json`, `-PROOF.json`).

- **Pictures:** a new one about every 0.6 s (the judge: 16 cuts a 10 s, median shot 0.49 s), on the voice's stress, each
  thing's pictures from the clause that names it: its line's footage, then its photographs, then its footage found for
  other lines, then a clip cut inside itself on unplayed seconds. A photograph is shown once (a second time only after
  12 s, on another part of it, never a third); a clip's seconds are never played twice. Every frame has a real picture.
- **Collages:** lines that name several things (the writer marks them; then the longest lines naming two or more) land as
  tilted prints stacking three to five deep, up to the style's share of the film (28% of the judge's shots are 3+ layers).
- **Flash frames:** white into the turn and the payoff, negative into the low point and into a black-and-white section.
- **Finishes by section:** each segment carries the year it speaks from (`era`, the writer's): before 1980 black and white
  in a film gate, 1980-2005 television scanlines, today in colour; the low point is always black and white.
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
