# Refbench

Reference-graded acceptance for every engine track (ROADMAP §4.5; contracts
in `worker/devices/INTERFACES.md` §8). Each test is a small YAML case in
`cases/<track>/<id>.yaml`. Refbench renders it on footage from the Valmera
Footage Library, measures it with `qa_metrics`, runs the standing checks,
builds comparison boards against the reference reel, writes one judge
packet per lens and records a verdict JSON.

**Internal only.** References, fixtures, labels, boards and verdicts never
go into git, product storage or a showcase. Only case specs, `vfl.json` and
code are committed.

## Commands

Run from `worker/` with the worker runtime: ffmpeg on `PATH`, Chromium for
motion items, and `MATTE_RVM_MODEL` for person mattes.

| Command | What it does |
|---|---|
| `python -m benchmarks.refbench validate` | Checks every case spec against the §8.1 schema and `vfl.json`. |
| `python -m benchmarks.refbench run AT-0D-1a [...]` | Renders, measures, writes boards, judge packets and the verdict to `$VALMERA_REFBENCH_OUT/<case>/`. Renders are lossless (`FINAL_CRF=0`) unless `--lossy`. |
| `python -m benchmarks.refbench judge-import <case-dir> <lens> <reply.json>` | Stores one lens judge's reply and recomputes the case's pass. |
| `python -m benchmarks.refbench baseline BASE-1 BASE-2 BASE-3` | Builds the Wave 0 baseline board under `~/Documents/Valmera/.valmera/refbench/boards/wave0/`. |
| `python -m benchmarks.refbench at0d2 [--only 15]` | Scores the detectors against the reference hand labels (AT-0D-2). |
| `python -m benchmarks.refbench vfl build\|verify\|list\|provenance` | Manages the footage library fixtures (sha256-pinned). |
| `python -m benchmarks.refbench catalog list\|verify` | Lists or verifies the house-corpus stand-ins and music fixtures in `<fixtures>/catalog.json`. |
| `python -m benchmarks.refbench labels coverage\|bootstrap\|refine\|export-cvat\|import-cvat\|syllable-template\|syllable-sheet` | Manages the label store (AT-0D-3). |

## How a device is measured

- **Motion graphics are read from the renderer's own clip.**
  - `ink.capture_overlays` records each clip `motion_layer.overlay_clip` places: file, x, y and window.
  - A glyph pixel is clip alpha ≥ 250. It counts as *shown* where the render carries the clip's colour.
  - Visibility, legibility and the landing frame come from that pixel set.
  - Do not diff against a "no graphics" control instead. A graphic mutes captions while it is up, so the control's captions would be counted as type. This was measured on the v5 boards, where the diff method reported APCA Lc 0 for type that reads at Lc 60.
- **Text items are read from a colour probe.** The same EDL is rendered with the item's colour swapped to `#FF00FF`. The pixels that differ are exactly that item's visible ink. A front-drawn twin gives the full glyphs for `glyph_visible`.
- **Legibility (APCA Lc)** is computed per ink colour.
  - A red stat and its white label are two inks; the case scores the weaker.
  - The plate is a ring from 6 % to 25 % of the letter height around the glyphs. It never includes the type's own anti-aliased edge.
- **Landing (`land_err_f`)** is the first frame showing at least 60 % of the settled glyphs in their settled colour. It is compared with the word's spoken onset (window ASR) or the cut frame.
- **The fixture clock is frame-exact.**
  - `vfl.render_source` renders a source-clock EDL on a trimmed fixture so that every boundary starts on the same frame as in the source:
    - with `-copyts`, the fixture is restamped by `file_t0` minus its floor to 0.01 s;
    - without it, a boundary is nudged by 0.01 s, and the nudge is listed.
  - `vfl.unshifted` refuses a move that left a source-clock field behind. Picture-card follow keys were the one that broke the Jobs baseline.
  - Measured result: the Thiel, Jobs and Elon v5 EDLs re-rendered on their fixtures match the v5 files at 50.4, 50.2 and 51.4 dB mean PSNR.

## Standing checks

These run on every case and a spec cannot switch them off.

| Check | What it requires |
|---|---|
| `render_ok` | The render decodes and has a program. |
| `brand_integrity` | End-card frames are md5-identical to the control. The robot's opaque pixels are identical to the control's on every sampled program frame, device windows included (the mark is composited after every creative stage, so a device can change what lies under it, never the mark), and the control must show the robot there. The whole watermark ROI is unchanged outside device windows. Both renders are lossless finals: a preview carries neither the mark nor the end card (refbench renders previews without the mark, as production does), and a `--lossy` final cannot be compared bit-exactly, so both report `None`. A check that compared no watermark pixel reports `None`, never `True`. |
| `no_collisions` | No OCR text box over a face core. With a control, the graphic's *ink* in the face core counts, not the box: type behind a head is not a collision. Faces are found on the control frame. |
| `loudness` | The program (not the end card) measures −14 LUFS ±0.5. |
| `owner_rules` | No music, SFX or zoom unless the case's `allow` lists it, and ≤ 3 flashes/s unless `flash_optin`. A zoom is any push-in the EDL asks for: `effects.zooms` (cut-step zooms included and named), a `zoom_punch` transition, a zooming frame shift. |
| `ledger` | Within `taste.ledger` budget. Reports `None` until 0C's ledger lands. |

## Judges

Refbench never calls a model.

- `judges/<lens>.md` are the prompts for the motion, craft and owner-rules lenses.
- `judge_<lens>.json` packets carry the filled prompt plus the board images and clip.
- Any judge that reads images (a person, an agent, a VLM call) answers with `{lens, verdict, major_defect, notes}`.
- Until every lens has answered, the case's pass is `None`, never a silent `True`.

## AT-0D-2: detectors against hand labels

- **Labels.** They live in `<fixtures>/labels/refs/<ref>/`. Each was made frame by frame on numbered contact sheets before the detector it scores was tuned. Each file states its method, scope and don't-care spans.
- **Held-out set.** Reference 08 has its own shot and negative labels (`*_heldout.json`). They were labelled after the first blind run and are never part of AT-0D-2. Every detector change since was checked on 08, so a gain on 15 that costs 08 is rejected.
- **Rejected example.** A finer 48-bin histogram raised ref 15 but lost precision on 08 (see `refsig._hist`).

## Labels (AT-0D-3)

The layout is `<fixtures>/labels/<vfl>/<window>/<label>/` (INTERFACES §8.4).

- **Person masks.** Each window has 30 frames (33 on the 60p stage window).
  - **Bootstrap:** RVM with a cut-aware warm-up, then a u2net second opinion.
  - **Review:** by the 0D agent (Claude), not a human annotator. Every frame was reviewed on overlay sheets, and every frame where the two models disagree by ≥ 1 % of the frame was viewed at native resolution with a pixel grid.
  - **Corrections:** made with polygons, colour-seeded GrabCut and u2net-gated additions (81 of 363 frames carry at least one correction in their `history`). Examples are lecterns, armchairs and hands that RVM got wrong.
  - **Void masks:** `person_void/` marks pixels excluded from IoU: RVM's own soft band (alpha 0.15–0.85: hair, motion blur), plus hand-marked regions such as motion-blurred hands, shadowed chair lips and crowds at the frame edge. Read them with `labels.read_void` and pass them as `qa_metrics.matte_iou(void=...)`.
  - **Bias warning.** The masks start from RVM and the void band is RVM's own uncertainty, so RVM scored against them is optimistic. `matte_iou` also reports `mean_without_void_exclusion`; a 1A acceptance that scores RVM should quote both, and a human CVAT pass (`export-cvat` / `import-cvat`) is the way to remove the bias.
  - **Record:** every operation is in `manifest.json` (`history`).
  - **Round trip:** `export-cvat` / `import-cvat` exchange the masks with CVAT for later corrections.
- **Syllable onsets** (`onsets.json`) are a listener's job.
  - `syllable-template` lists the 25 chosen words per window and leaves their onsets empty with `status: todo`.
  - `syllable-sheet` writes spectrogram panels and per-word WAVs to fill them from.
  - Coverage counts a window only once its status is `reviewed`.
- **Fingertips** (`points.json`) are a MediaPipe bootstrap with `status: bootstrap_unreviewed`. They are needed by AT-3A-2 (Wave 3) and do not count toward coverage until reviewed.

## Fixtures

| Store | Where it lives | What it holds |
|---|---|---|
| VFL | `benchmarks/footage/vfl.json` (in git) | Windows as source seconds, the clock (`file_t0`) and sha256 pins. Files are stream copies in `<fixtures>/<VFL id>/`, each source with a `provenance.json`. |
| HC stand-ins, owner-named music | `<fixtures>/catalog.json` | Written by the fixture acquisition pass. Read with `catalog.py`. |
