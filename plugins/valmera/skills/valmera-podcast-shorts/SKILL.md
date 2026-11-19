---
name: valmera-podcast-shorts
description: Turn a podcast or interview link into finished vertical shorts with one command and one argument, the link. The run fetches and transcribes the source, finds and ranks every story-worthy moment (no fixed count), gives each short its own style, builds every short on Valmera's engines (real footage of what is said, the judge's pace and camera moves, sound design, chosen music, word-by-word captions, display words, finishes by section, collages) and delivers one complete folder once. Use when the user gives a podcast or interview link or asks for podcast shorts. Does not publish.
---

# Valmera Podcast Shorts (v10, 14 Nov 2026)

Never the same podcast twice (owner, 14 Nov 2026): a link whose video id or source sha256 is in the shared delivered ledger (`.valmera/workflow/delivered.jsonl`) stops at once with one JSON line naming the delivered folder (exit 4); a test run uses `--test-out <dir>`, which never delivers, never writes to a delivery folder and cannot be combined with `--out`. Only a folder inside `Podcast Shorts` (or another of the owner's delivery folders, however its path is spelled) is a recorded delivery; any other `--out` is a test.

The only input is the link.

```
python3 scripts/podcast.py "<podcast or interview link>"
```

`scripts/` is beside this file. Any `python3` starts it: the script moves itself onto the engines' interpreter.
Nothing else is asked of the user: no brief, no count, no style, no song.

For the owner's local subscription adapter, source `.valmera/tools/codex-proxy/env.sh` before the command
or wrap it with `zsh .valmera/tools/codex-proxy/run.sh`. Keep one short lane active (`--lanes 1`), never
retry an unchanged failed model call, and keep all model decisions on that guarded local route. The
operator-controlled `FOOTAGE_FETCH_CMD` can fetch original public video through a box's authorized egress;
it returns media and provenance, never account credentials. Remote rendering needs no model access.

## How to run it (Claude Code and Codex alike)

1. Start the command above as a background job (`run_in_background` in Claude Code; a background terminal in
   Codex). Measured 15-16 Nov 2026 on the subscription route (Jensen Huang, CBS, 46 min, one lane): fetch 5 min,
   local hearing 36 min, the reading 6 min, then 30-115 min and 600-1,400 model calls a short. A run of many
   shorts takes many hours: let it run, it continues where it stopped.
2. Do not poll it and do not end your turn while it runs. When it exits, read its last line: one JSON object with
   `delivered` (the folder), `shorts`, `not_made`, `spend_usd` and `work_empty`.
3. Tell the user the folder's path once, how many shorts are in it, and anything in `not_made` with its reason.
   Do not rate the shorts: a builder never rates its own work.
4. If it stopped (exit 2, a `stopped` object), run the same command with the same link: it continues where it
   stopped. A short that failed is tried again on that run. When the model route stops answering (a usage limit,
   the adapter's cap, an outage: HTTP 429/502/503/504) the short in hand keeps its work, the shorts not started
   wait, and nothing is delivered until the set is whole: run the same link again when the route answers. Fix a real defect in the product, never by hand in a
   film.
5. **"YouTube is refusing this Mac as a bot"** is a temporary outage of one input, recovered by the run itself: a
   busy address is walled on every player for a while (a seven-short run fetches about a hundred sources), so the
   run paces every YouTube fetch of all its shorts through one shared bucket (`FOOTAGE_PACE_S`, 3 s after a burst
   of 4; `FOOTAGE_PACE_BURST`). When half
   or more of a short's footage sources are refused, it fetches exactly those sources again (the search already
   found them) through every route: the operator's transport (`FOOTAGE_FETCH_CMD`), every player client on every
   configured proxy (`YTDLP_PROXY`, `YTDLP_PROXY_FALLBACK`, read from the environment or the backend's `.env` into
   the footage engine's), then the Wayback copy; after 2 and after 5 minutes, and at once each time the run continues the short. A source
   is waited for when YouTube's own answer (not the Wayback copy's) is the wall; while nothing has opened or been
   cut, no source is set aside as gone. Still refused, the short keeps all
   its work and waits; shorts whose footage is already in hand go on; the run waits 10, 20, 40, 60 and 60 minutes
   and continues the waiting shorts each time (`PODCAST_WALL_WAITS`). A short is never built from no footage and never
   delivered as the bare take. Only if the wall outlasts that does the run stop with this message: nothing is
   delivered until the set is whole; tell the user and run the same command again later. Do not look for a way round
   it with anyone's account or cookies.

## What the run does

| Stage | What happens | Where |
|---|---|---|
| fetch | The video at 1080p or under and its captions. A download of the same video an earlier run left on this Mac is used in place. YouTube is asked down a ladder of player clients (plain, embedded, mobile web with a PO token, TV, Safari): when it walls this Mac as a bot on one, the next is tried, and the one that worked goes first from then on | `podcast.py fetch`, `ladder` |
| hear | Every word with its time: the platform's captions, else the whole source heard locally (whisper large-v3 through `heavy.sh`) | `podcast.py hear` |
| moments | The conversation is read in ten-minute parts (one high-effort reading each, a minute of overlap): each returns every moment that starts in its part, stands alone, hooks in its first sentence, lands its payoff, runs 18 to 55 s and is about at least one real thing a camera has filmed. Strong opinions, confessions, predictions and numbers about real things count like stories. Each is graded 1 to 10 on its own; every moment at 7 or over is made, strongest first; of two that overlap the stronger stays. There is no count, minimum or maximum | `podcast.py moments`, `parts_of`, `rank` |
| styles | One of the four styles below per short: the moment's own best fit, never the style of either short before it, spread evenly | `podcast.py assign_styles` |
| shorts | Each moment is one run of the workflow shell: the exact clip on word edges, the footage and photographs, the film in its style, the engines, the render, the brand finish, the checks, the music | `wf1_short.py`, `wf1_film.py`, `../_shell` |
| deliver | Once, complete, into one folder, then the work folder is deleted and the run fails if anything of it is left | `podcast.py deliver` |

## The bar (owner, 13 Nov 2026; these replace every older rule in this skill)

The judge is the 28 @metromedia.house reels: `.valmera/week-1112/judge/metromedia/TEARDOWN.md`. Podcast shorts are
judged at that craft level, not against plain podcast clips: 16 cuts per 10 s, 69% of shots moving video, music
under 87% of the film, 4.3 camera moves and 3.6 audible sound effects per 10 s, captions that build word by word,
display words on 17% of shots, finishes that change by section, collages of three layers and more.

- **Fast and dynamic, done well.** A picture or framing that holds for long is a defect. A new picture lands about
  every second over what he says, motivated by the words, eased, faces whole, one consistent design.
- **Real footage of what is said.** Video of the actual people, companies, places and events he names. Photographs
  are the fallback, generic stock is never used, and this conversation's own take is never a cutaway. The speaker's
  own filmed past (a livestream he hosted, a keynote, his training, an event he took part in) is archive like any
  other, wanted when he tells of it.
- **Sound effects are part of the film.** The same kinds and about the same amount as the references, each on
  something that lands, at the references' level under the voice, real recordings only. A short with no designed
  sound is a failure. So is a sound on every caption.
- **Every short gets music.** Valmera chooses a track from its licensed library the way the references do and cuts
  it to the short's turn and payoff. The same short without music is delivered beside it.

Withdrawn: "sound effects default to zero", "restraint is the default", "music is off unless the owner supplies a
song", "at most 1-2 sounds per short", "a steady picture is the default". They produced the bland, static films of
12 and 13 Nov. What stays from them: nothing is thrown in without a reason on screen, and nothing sits on a face. Sounds keep one level hierarchy under the voice. Camera moves use
`worker/scene/pace.py` and `motion.py`; faces stay whole.

## The styles (`scripts/wf1_film.py`, registry `../_shell/styles.json`)

The take is the spine and the sound of every style, the payoff is always his face, and the film ends on his last
word so the end card follows directly. What differs is how a found picture is shown, and the type and colour.

| Style | A found picture | Type and colour |
|---|---|---|
| `footage-cut` | fills the phone, in colour | heavy grotesk capitals, yellow accent |
| `window` | a photograph or document plays in a four by three window the width of the phone over itself out of focus (three layers) | condensed capitals, cyan accent |
| `archive-reel` | fills the phone in black and white with heavy grain, a projector flash into each run; he stays in colour | cream serif italic |
| `prints` | a photograph or document lands as a readable, nearly full-width tilted print on its own dark out-of-focus ground; the previous print clears so document and interface detail stays visible | high-contrast serif, orange accent |

Moving footage fills the phone in every style (owner's rating, 16 Nov 2026: cutaways as small windows over a blurred
fill were the defect). Every style asks the footage engine for the phone's full size (`wf1_film.FOOTAGE_FRAME`, 1080x1920;
19 Nov: the window and prints styles asked for their card size and every 16:9 clip arrived 1080x608, in a window card).
Clips keep their source's own pixels (`chain.footage(native_pixels=True)`); whole-first sources come as separate 1080p
video and sound streams first (`fetch.whole_av_format`); a rung on which they time out or fail (not walled) is asked at
once for the earlier 720p ladder (`run_ladder(lighter=...)`), so a slow route still brings the source; and a whole file
that still arrived small is cut from the live stream at up to 1080p. A wide clip is cover-cropped round the subject the look placed and its people. A clip still too
small to fill the phone within 3x is brought up once with a detail-keeping scaler (`wf1_film.fill_size`); it is never
shown as a window. Each picture's size, cut route and fetch rung are in the record (`checks.podcast.sources`).

The placement check is a model that chooses (19 Nov: a check that rejected by default emptied six of twelve films). It
sees each wide clip's full source frame (lower thirds, wordmarks and signs included) and then the phone-shaped slice,
judges it against the whole clause it sits under, with its provenance (the source's title, channel and description,
what the footage engine saw), ranks the candidates of a clause (`fit`), says where the subject stands so the slice is
centred on it (`subject_x`), and says no only for a contradiction a viewer would be misled by (a different person,
company, place, era or event). Identity comes from that provenance; on-screen attribution is not required, and a lower
third naming the subject is attribution, not foreign text. The verdict is keyed on the source picture's frames, its
clause and its provenance, never on the director's search brief, so a re-brief or a moved slice never re-judges it.
Other editors' text (subtitles, promo labels, date stamps) is found by the same look, never by a word list. Its box is
grown to the text lines measured on the clip (`worker/footage/overlay.py`: seated stroke lines, signal only), the clip
is cut down to the largest band clear of it that still fills the phone, and that new clip is checked again. Text no
band can shed stays on the picture, recorded (`text_kept`); the picture keeps its place. When the evidence names the
person a picture shows (a lower third the phone slice cuts away, the source's title or channel), the check returns that
name (`name_super`) and the film draws it in the label type, once, while the picture is up (two lines when long, never
smaller than the reading floor). A window with fewer chosen pictures than its clause wants has them share the whole
clause, a clip up to its own seconds (`wf1_coverage.clause_shares`); with enough pictures the picture changes about
every 1.4 s.

The coverage plan starts from the script's resolved referents (`script.named`, with their event-level `must_show`)
and gives every window a depiction ladder: the event itself, then the named person's or company's own archive, then
the place or product, then related moving context. A window research leaves empty takes its next rung, acquired at
once, before the final build (`wf1_short.descend`). The final build fills any stretch of the take over 3 s with the
approved pictures the film has not shown, each checked on its own words (`wf1_film.fill_holes`). An interval is never
shown twice: a window whose candidates were all shown takes a fresh interval of the same kept original beside the
verified one (`wf1_coverage.fresh_interval`).

## What every short is built from

| Part | How | Engine |
|---|---|---|
| The clip | In and out on word edges, the title, the hook and close lines, and the real things it names | `wf1_short.script` (OpenAI) |
| Real footage | Video of what each sentence names from YouTube and archive.org, every frame checked, a person held to one face, 1 to 3 s clips inside one shot, graded to one look, each with its URL, channel and time range. A clip that is near black or flat is left out. Source aspect and every edge are preserved before the layout; portrait crops are never baked into supply. Pictures never move to a different sentence without a fresh placement check | `worker/footage` through `_shell/chain.footage` |
| Photographs | Of the same things, after the footage of them and where footage is thin: Commons and Openverse, checked by eye, licence kept |
| Documentary coverage | A model inventories every concrete named thing and assigns pictures to exact word spans. The targets: actual pictures on at least 45% of the short, moving footage on at least 60% of picture time, six new real pictures per ten seconds of named stretches. Every picture is chosen for its clause on gpt-6-sol and every composed entry, middle, exit and display transition is looked at again; a picture that contradicts its words never ships, and its slot goes to the next-ranked candidate. Under target, the short spends at most `RECOVERY_PASSES` (2) research passes, counted across rebuilds, and one more pass for moving footage after the render review; then the film is built from every approved picture, the speaker carries the rest with his own camera cuts and motivated moves, and `coverage.short` in the record says what was short. A target is never a reason to lose a short | `wf1_coverage.py`, `placement_check.py` |
| Pace and moves | New pictures carry pace. Speaker footage keeps its own camera cuts; only model-justified rhetorical words trigger eased, face-safe moves. Never alternate hard crops to fill a cut quota | `worker/scene/pace.py`, `wf1_film.py` |
| Look | One look solved on the film's own pictures, skin kept | `worker/scene/looks.py` |
| Finishes by section | When part of the story is about the past, that part's pictures are black and white (a story that is all past keeps its colour, except in `archive-reel`); one new picture in three opens soft and is sharp by its fourth frame; a white frame on the cut back to his face for the payoff | `wf1_film.build` |
| Captions | One face-safe seat for the whole film, measured against every final camera crop; a line is laid as it will stand and each word is inked on its own spoken word. After a display hold, already-spoken words are omitted; only the current and following words resume, never a restarted chunk. On a picture that is bright under the seat the type is dark for as long as that picture is up | `film.caption_build`, `wf1_film.ink_by_ground` |
| Display words | Four to eight strings, the figures, names and words that carry the story, large on the caption seat on the word that says them; the first two seconds always show the spoken hook word by word; the headline stays in delivery metadata; all display blocks are seated against final face boxes on every frame, shrinking long titles if needed; search-subject labels are omitted so they cannot make unsupported factual claims | `wf1_short.direction_of` (OpenAI), `wf1_film.build` |
| Sound design | The references' kinds of sound on what lands, on new pictures and meaningful graphic actions, no recording twice. Audibility is measured from 50 ms broadband onsets against the whole mix, with retained stems verified against the encoded export | `worker/score/sound.py` (SND3) |
| Music | A track chosen from the licensed library, opened on a full part, built where the story turns, shaped on the payoff even when fewer than two bars remain, 5–6 dB below speech on the final broadband ruler, a 0.5–2.3 s payoff drop at 10–24 dB, and the current product brand finish (the owner has held the old end sting pending STING-REF). No two shorts of one run play the same track | `worker/music_film.py`, `worker/score/music.py` (MUSIC2) through `_shell/chain.music_version` |
| Brand | The product's own watermark on every frame and its end card, measured on the delivered file | `_shell/brand.py` |

## The alien bank (`worker/scene/alien`): only as the verdict of 14 Nov allows

`.valmera/week-1112/notes/S.md`, binding. `diecut` and `answer` are gimmicks: never used. `lamp` is not ready: not
used. `arc` is asked for only behind its gate: the moment says two or more different calendar years aloud, in the
style that keeps its pictures in colour, and each year is also set as a display word so it reads. `rhyme` needs
four to six moving round links, which a podcast moment does not supply: this skill does not ask for it.
`wf1_film.alien_asks` holds the gate and `clean_asks` drops any other device, whoever asks.

## The delivered folder

`~/Documents/Valmera/Podcast Shorts/<Speaker> - <Episode> (<year>) - <N> Shorts/`

```
01 - <title>.mp4      each short, strongest first, with its music
clean/01 - <title>.mp4   the same shorts without music
SOURCES.md            every short's sources, in the order they are shown: the podcast window, each footage clip (title,
                      channel, URL, its seconds in the source and in the short), each photograph (licence, credit),
                      the track and its credit line
shorts.json           every number: rank, grade, style, window, checks, pictures, display words, sounds, music, spend
records/NN.json       each short's full record from the shell
README.md             the list, what was not made and why
```

- Delivered once, complete. Nothing is ever added to a delivered folder: the owner's publishing agents take it as
  one set. A second run of the same link makes a new folder.
- The work folder (`.valmera/workflow/podcast/<id>-<time>/`, the downloaded source included) is deleted after the
  folder is in place.
- Never published by this skill. The real footage is not licensed (the owner's decision: whoever publishes decides
  to keep it), and a CC-BY track's credit line goes wherever a short is posted.

## Rules that never bend

- The end card and the watermark are the product's own, on every short, never cropped, covered, moved or replaced.
- Faithful: a clip is one continuous window of what was said. No spliced claims, no invented numbers, names or quotes;
  every display word is a word he says.
- Every clip, photograph and track is listed with its source.
- No model call on Anthropic's API: the run's calls are on the OpenAI rung (`../_shell/llm.py`).
- No word list or regex decides what a moment means or whether a film is good: models read, signals measure.
- A builder never rates its own film: `shorts.json` carries measurements, not scores.

## Files

- `scripts/podcast.py` the run. `scripts/wf1_short.py` one short on the shell. `scripts/wf1_film.py` the four styles.
  `scripts/test_podcast.py` their tests (`.valmera/venv-worker/bin/python -m pytest -q scripts/test_podcast.py`).
- `../_shell/` the workflow shell every workflow skill shares (stages, brand finish, music, deliver once, cleanup).
- `legacy/v9/` the earlier workflow (a coordinator and editor subagents on the Valmera MCP tools, `scripts/run.py`):
  kept only to resume a v9 run. Its rules on sound, music and restraint are withdrawn. `legacy/README.md` covers v6-v8.
- `alien/` and `../_shell/wf1.py`: the earlier one-film styles (room-listens, lit-by-voice, receipts, second-camera).
  Not part of this workflow.

Never report plan counts as viewer measurements. Use the independent rater’s final-film scripts and judge3; a film's measured coverage shortfall (`coverage.short`) is reported with it, never hidden. A fixer does not independently rate its own work.

Research the director's visual subjects, keeping the full spoken-entity inventory for audit. Pass the depiction context all the way to the footage reviewer. A contextual campus illustration is distinct from a claim to record a particular historical conversation; never invent the missing participant. The podcast uses the engine's contextual-check route: every selected source cut, composed placement and branded transition is checked against the actual words. Named people are identified from provenance (the source's title, channel and description, a lower third anywhere in the full source frame), never by face comparison. Preliminary motion scouting is bounded to the source frames being examined; the selected clips still receive exact motion measurements. A failed coverage gate triggers targeted research with retained sources, never silent deletion of the pictures.

For subjects that need primary evidence, the product also researches original institutional PDFs and images,
renders their actual pixels with PDFium, and checks readable crops against the spoken passage. These remain
stills and never count as moving footage. Source provenance follows each placement into the final-frame
review; it cannot excuse a contradictory image. A failed source download does not discard other usable
sources. The bounded research budget includes both documentary artifacts and moving-footage recovery.
Before declaring a referent unfilmable, research the speaker's primary account and the original publication;
an unnamed project or recent result may have a documented identity. Primary article pages are captured
headlessly as actual publisher pixels and remain stills. Public original-publisher video files and verified
cached originals can enter the same footage engine when platform access fails. A newly available original
invalidates an exhausted search cache, while every placement still needs a fresh word-specific verdict.
After picture selection, exposed speaker passages are checked again: only a director-justified rhetorical
word can trigger an eased move. A numerical hold detector never creates automatic alternating crops.

Repeated overlapping document crops or source-video intervals do not count as new pictures, even under different cache paths. Final review receives separate phone-resolution frames; a half-open placement ends before the next cut. Changed scene or audio plans invalidate the retained render and final mix before review.
A meaningful silent reaction may carry a director-justified eased move anchored to the preceding spoken line; its timestamp must stay inside that line's following silence. Unresolved holds return to direction with their exact frame bounds, never an automatic crop pattern.

New documentary stills do not consume a recovery attempt when moving coverage is short: that same attempt continues into moving-footage research.

Moving footage needs both the residual-motion measurement and visible subject action across the checked
frames. Card transforms, pans over documents, and cursor-only changes on a static interface remain stills.
Their failed motion verdict lowers their acquisition priority; it does not falsely reject a faithful picture.
Retained publisher originals enter the first acquisition pass as attributed candidates for their exact
subjects, as well as later recovery. Reusing their bytes never bypasses motion or placement checks.
The composed review judges only what composing changed and repairs it where it was found: a crop through a face or a
subject outside the frame is framed again first; a contradiction, or a second finding on a picture already framed
again, gives that one window's slot to its next candidate; a finding that still returns on that window gives it to the
speaker. The picture stays eligible in every other window (`render_rejected.json` holds window, the candidate's own path
and fault; a copy cut clear of text answers for its candidate through `origin`). A finding with no step left is recorded
on the film (`unrepaired`) and the film is delivered. A contradiction may move a window's boundary; no finding and no
rejection reason ever reaches the acquisition brief. A paused repair resumes before rendering a stale plan. The
rendered programme's cuts are measured on its pixels (`measure_cuts`: cuts per 10 s, longest hold, every hold over
6 s); the windows under a long hold, or under fewer than 4 cuts a 10 s, go back to research once a short
(`_rebuild_holds`), never as a gate, and the delivered record carries the measure (`checks.podcast.measured`).
The composed film aims at 60% moving picture time (one more footage pass when it is under, then it stands). A still discovered there stays
eligible as a still, while the product returns to footage research; it cannot regain moving credit from an earlier source check.
Research is split into small referent groups with retained answers. A source replay can reuse verified word
timings while recovering the same original at a resolution that permits real, face-safe eased movement.
Sound attacks are balanced against the calibrated bed, then checked against decoded AAC using the rater's
stem fit and T2 test. One bounded rebalance changes only already-earned effects; one further bed-only
correction handles changed limiter gain. Test runs retain their
stems and scene evidence beside the exports before ordinary workflow scratch cleanup.

When local ASR is queued behind shared Mac jobs, `SHELL_HEAR_CMD` may route the exact clip to an isolated local Whisper worker. The transport returns source SHA-256, engine provenance, and ordered word times; the shell rejects another source or out-of-range timings. It never substitutes untimed platform captions for alignment.

After local ASR, a small model reconciliation compares the timed hearing with the source captions and selected hook/close. It corrects clear transcription homophones and chooses heard word edges. The product recuts the original clip and shifts its word times together; it cannot remove interior speech or rewrite the story. Changed timed text invalidates its coverage/direction and line-assigned footage caches while preserving downloaded originals.

WF1 uses the interview as its voice spine, so documentary acquisition passes `--hear-seconds 0`: it does not transcribe unused B-roll audio or seek voice cameos. Every source still receives its normal provenance, motion, word-placement and final-frame checks. Fetched originals enter the run-local retained source cache immediately, before any later stage can fail. Other workflows retain main’s whole-clip local Whisper path.

When measured moving coverage is short, recovery prioritizes new original video before requesting
additional still documents. Existing primary documents stay available and checked; more still research
resumes once the moving share is sufficient. A queued model call is never retried unchanged.

Physical camera travel through a real three-dimensional scene counts as moving footage when the
frames show depth/parallax or newly revealed geometry. A flat pan/zoom over a still, cursor-only
movement or an editorial card transform does not. The final-film residual-motion gate remains.

When all referents have faithful pictures but motion remains short, pass the actual failed motion
verdicts back to acquisition once. A changed brief may research different original footage once;
it never resets the platform budget or approves a formerly static or incorrect placement.
Revised depiction briefs can reconsider the attributed retained-source catalog through the model
ranker and fresh frame checks. A dark background alone is not an empty picture; nonblank diagrams
still need readable final-frame approval and cannot receive motion credit from brightness.
Caption pages keep their final word visible before the next page opens. The ink delay must
never delete a short spoken word at a page boundary; resumed older caption plans are rebuilt.
Windows with the same director-assigned referent can reconsider each other's retained clips.
Every new placement is checked on its own words; a repeated source interval remains one new picture.
