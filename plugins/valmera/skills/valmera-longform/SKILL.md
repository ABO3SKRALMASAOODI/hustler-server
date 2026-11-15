---
name: valmera-longform
description: Make one researched 10–20 minute documentary film from no input or an optional topic, with Easton narration, real footage, original voices, chapter music and designed sound. Use for long-form voiceover films and documentary stories. Delivers locally and never publishes.
---

# Valmera long-form

Never the same idea told the same way twice (owner, 14 Nov 2026): the ideas stage sees this workflow's delivered ideas from `.valmera/workflow/delivered.jsonl` and a gpt-6-sol judgement takes out any exact repeat before scripting; a new angle, version, structure or story is welcome, but the same narration or beats with only a new visual look is a repeat. Only a folder inside the owner's delivery folders (Podcast Shorts, Story Films, Marketing Films, Long Films) is recorded; any other `--out` is a test. A test run passes `--args '{"delivered_ledger": "off"}'` and an `--out` outside the delivery folders, and is not recorded.

Run the same command from Codex or Claude Code. No questionnaire or topic is required:


```sh
/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python <this-skill-folder>/run.py
```

Optional: `--topic "a subject"`, `--out <folder>`. Existing `run --resume <id>` and `status` commands remain available. The default destination is `.valmera/week-1112/lanes/WF4-LONGFORM/`, never a publishing folder. `run.py` loads only the OpenAI key into its environment; it neither prints keys nor calls Anthropic.

Read [REFERENCES.md](REFERENCES.md) for the long-form pacing and music bar. This is a documentary, not a stretched short. The story determines its structure and treatment. Do not copy a reference's story skeleton or prescribe a fixed ledger, turn position or chapter duration. A hook must work in the first ten seconds; each chapter must change what the viewer expects.

## Engines and proof

- `story.py` finds ideas on the web, researches exact source quotes and verifies them against fetched pages. The writer uses verified facts. It writes complete spoken sentences, one or two focus words, ordinary joins of 0.18–0.26 seconds, and intelligible spoken forms of difficult words. A pronunciation change must preserve meaning.
- Before the final read, `archive_catalog.py` inspects the actual archive frames. `archive_script.py` conforms the researched draft to those views, checks its claims independently against verified quotes, and anchors visual subject changes to spoken words. Original drawings and ground rehearsals receive truthful context labels. The prior draft and read remain recoverable until delivery; the final encoded-picture gate still checks every shot.
- Once the spoken wording is audited and frozen, `archive_visual.py` checks a provisional picture edit alongside the guarded read. It can repair picture choices and labels, but cannot change the recording's words or chapter turns. It preserves checked intervals with word anchors when replacing a paragraph's other choices. Unresolved alternatives become research notes for the recorded word clock; they cannot erase existing source candidates. Its estimated coverage is planning evidence; only the final rendered film establishes the targets. Full-resolution still details and exact extracted report pages stay attached to technical figures, so a small thumbnail or missing caption context cannot decide their identity.
- `cinema.py` uses the shared Easton reader (`../valmera-stories/voice.py`, built on `../_shell/narrate/narrate.py`): whole sentences, expressive takes selected by signals, focus emphasis, tight joins. `longform_voice.py` measures the result and applies bounded pitch-contour and join-tempo corrections, then hears it again. Keep the owner's free Easton voice. Its pinned additional dependency is in `requirements.txt` for the narrator environment.
- `footage_box.py` calls `worker/footage` per chapter, with each line and its named subject. Discovery probes real video, not photo counts. The established Mac YouTube wall is handled on the box's existing fetch network; whole-source downloads support local cuts when remote sections fail. Existing YuNet/SFace models travel into private scratch for speaker verification. No service restart or production switch change. Each original source interval is reserved for this film, including earlier chapters. Never fill a gap with a repeated image, frozen frame or unrelated footage. Cut real voices in where the subject speaks and level them to the narrator.
- Fetch jobs use an isolated container pinned to an installed worker image, so production rollouts cannot remove their scratch. It inherits only the existing fetch route, has no database credentials, records costs locally and pins all model calls to funded OpenAI models. If repeat video searches leave a rare event unfilmed, use a unique, checked archival evidence photograph with movement; photographs can never displace the majority-moving film gate. Search primary archive APIs and cited article captions as well as Commons. Alternate scans and mirrors reserve the same photograph. When genuine technical diagrams are needed, `document_evidence.py` researches original licensed reports, selects and visually verifies their figures, and shows them as archive records; it never synthesizes historical hardware. Its worker dependency is pinned in `requirements-worker.txt`.
- `music_chapters.py` uses MUSIC2's chooser, arrangement and mix for each *story chapter*, following its own turn and payoff. Retain the licensed track credits. SND3 designs recorded effects against the voice and music. Never synthesize effects.
- `moving_picture.py` offers unused moving alternatives to checked stills. It accepts a replacement only after the entire spoken interval is covered and independently checked; a failed replacement preserves the original edit. Source-review reuse binds the exact words, context, labels, provenance, original bytes and checker version. Every encoded picture is checked again after rendering.
- The scene renderer supplies picture; the existing brand finish supplies the unchanged watermark and end card. Heavy Mac work goes through `heavy.sh`; narration uses the shared kernel lock. `guarded.py` records `/usr/bin/time -l` and physical footprint, and stops its own process group if a process exceeds 6 GB. The same guard covers verification listeners.

## Delivery gates

Before delivery, measure the finished file and retained evidence:

- 600–1200 seconds; hook before ten seconds; a factual turn per chapter.
- At least 50% of picture time real moving footage. No repeated original source range or image. No hold over eight seconds without movement. `measure_picture.py` uses judge3’s unchanged cut and camera detectors on the encoded film; an invisible split in the edit plan cannot pass the hold gate.
- Check every displayed picture against its spoken words, including identity, era and context. Keep the checked frames and verdicts.
- Music present under at least 80%; at least one measured rise per chapter. `measure_sound.py` retains the final-minus-clean diagnostic and uses the independent WF1-r4 rater’s final stem method for the verdict: fit the captured voice/SFX/music stems to the encoded film, find SFX onsets blind, and apply its two audibility tests (6 dB over the two-second non-vocal median, then within 6 dB of concurrent voice plus music). A poor stem fit fails. Never report planned events as audible events. Music downloads stay inside the run.
- Unprompted narration listener word error below 1%, including a second listen to the final encoded mix with music and effects present. Original-voice inserts are excluded from that narration comparison. Match the VO2 independent method on the retained narrator stem: pauses of at least 0.15 seconds, 11.7–17.4/minute, median 0.22–0.28 seconds; voiced p95–p5 pitch range 13.79–15.33 semitones. Preserve rejected takes and diagnostics until fixed; never label an unmeasured target passed.

The workflow stops resumably on failed gates. For persistent spoken-word errors, stop that run’s narrator and use `read_repair.py <run-folder> <diagnosis.json>` with `{"lines":[{"i":zero_based_segment,"evidence":"listener mismatch"}]}`. It rewrites against verified quotes, requires a second factual check, preserves unchanged takes and archives the changed ones. The thirty-take generation budget persists across resumes; the final word gate is unchanged. Diagnose and repair the product, then resume the same run; never hand-edit a customer's project. A completed run delivers once and removes its work folder. Keep film, source/credit ledger and measurement evidence. Run `judge3.py` for signal measurements and make a sixty-second comparison with the best matching long-form reference. A separate Claude Opus-or-above session owns the rubric-v3 quality rating; the builder does not rate itself or claim ALIEN from density alone.

After skill changes, ship the tested revision and run `.valmera/sync_skill.sh`.
