# Safari player checklist (manual)

The studio's timing/seeking bugs reported in production reproduced in Safari
specifically (iPhone Safari included). The *class* of bug is fixed by three
mechanisms, two of which are verified automatically on every integration run:

| Mechanism | Automated check |
|---|---|
| Every preview encodes with `-g 48 -keyint_min 24` (≤ ~1.6s between keyframes) so scrubbing lands precisely | integration: keyframe timestamps of a rendered preview, max gap ≤ 2.2s |
| Every rendered asset is `+faststart` (moov atom first) so duration is known before the file finishes loading | integration: moov-before-mdat byte check on the final |
| One insert-aware source↔output mapper (worker/timeline.py, mirrored line-for-line in the studio page) used by the transcript panel, the strip, and the playhead readback | unit tests: both directions, with cuts and inserts |

## Manual pass (run once per player-affecting release, in Safari on macOS + one iPhone)

Setup: open a project with (a) at least 3 cuts, (b) one image insert, (c) a
9:16 frame, and a rendered preview loaded (chrome badge says PREVIEW).

1. **Duration immediately** — the player shows the correct total duration
   within ~1s of the preview attaching (no `0:00`/`NaN` flash). [faststart]
2. **Scrub precision** — drag the native scrubber to 5 different positions;
   the frame shown matches the position (no snap-back to an earlier frame
   of more than ~1s). [keyframe density]
3. **Transcript click, preview mode** — click a word that survives the cut:
   playback jumps to that word (the word is spoken within ~0.3s). Click a
   struck-through (cut) word: playback lands at the nearest kept moment,
   not at a wrong scene. [mapper]
4. **Transcript click across the insert** — click a word AFTER the image
   insert's position: the word plays (shifted correctly past the insert).
5. **Strip click, both modes** — click the same strip position once with the
   proxy loaded (SOURCE badge) and once with a preview loaded (PREVIEW):
   both land on the same content.
6. **Playhead readback** — let the preview play through the image insert:
   the white strip cursor holds at the splice boundary during the insert,
   then continues; the highlighted transcript sentence tracks speech.
7. **Consistency** — the strip label ("program X · keeping Y of Z"), the
   player's total duration, and the export's duration agree to 0.1s.
8. **iPhone Safari** — repeat 1–3 in portrait; also verify the 9:16 preview
   letterboxes inside the player without horizontal overflow.

Status log:

| Date | Safari version | Result | Notes |
|---|---|---|---|
| 2026-07-08 | (pending user run) | automated items green in CI; manual pass not yet executed | run after this deploy |
