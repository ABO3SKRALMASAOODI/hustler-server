# Podcast editor issue audit — 6 September 2026

Run: `palantir-karp-20260905-132939`.

The confirmed implementation defects have fixes in the worker, MCP API, and Studio draft preview. Several requested capabilities go beyond the reproduced defects; those limits are stated below. These changes do not rewrite the 18 saved candidate edits or their evidence. Deployment status is verified separately from the local validation recorded here.

Persistent Valmera branding and the five-second final outro remain unchanged. Validation used local synthetic media and mocked storage/database services. No production startup, database migration, publishing, or manual deployment was performed for validation.

## Findings and changes

| # | Disposition | Trace and resulting behavior |
| --- | --- | --- |
| 1. Wrong speaker / poor automatic crop | Confirmed unsafe selection; conservative fix | Face selection previously chose the largest face in a multi-person sample and could fall back to image detail as a crop target. Multi-person samples now require review instead of silently choosing someone. Unmeasured/ambiguous shots use a safe fit in automatic mode; measured single-face shots can still crop. Results explicitly distinguish face location from speaker identity. Editors can inspect and override the source-time focus track. **Audio-to-face active-speaker recognition is not implemented by this change.** |
| 2. One global crop | Confirmed access/behavior gap; fixed | The schema and renderer already supported shot-specific focus spans, but `set_frame` did not expose them and auto-reframe rejected track construction with transitions. `set_frame` now accepts validated, non-overlapping `focus_track` spans, and automatic tracks can coexist with transitions. The renderer applies local shot boundaries without rewriting the keep list. Source-time tracks survive timeline trims. Studio waits for the composed preview when its single-position draft player cannot represent the track. |
| 3. Verification includes unused shots | Confirmed; fixed | `_reframe_findings` iterated the full source shot index. It now intersects each shot with the active EDL keep ranges, maps findings to output intervals including speed changes, and checks full active-span track coverage. Ordinary stationary full-frame video covers suppress findings for completely hidden source intervals; small, moving, translucent, or potentially transparent image layers do not. |
| 4. Datetime serialization | Confirmed; fixed | Justification copied records with plain `json.dumps`, and durable database reads can include timestamp objects and an outer record wrapper. Dates and datetimes now serialize as ISO-8601, wrappers are normalized, failed persistence produces a structured tool error, and a repeat of an already-recorded identical justification is idempotent. The original evidence remains attached. |
| 5. Additive caption-fix state | Confirmed; fixed | `set_caption_fixes` now has explicit `replace`, `append`, `clear`, and `list` operations. Replacement is the default and replaces the complete active set, including stale legacy rules. Append upserts the same normalized phrase/scope. Listing exposes both historical and new active rules. Successful edits return the compiled caption audit/preview immediately. |
| 6. Global and word-count-constrained corrections | Confirmed; fixed | New corrections accept optional output-time bounds and replacements with different word counts. They preserve explicit punctuation/capitalization, support merging/splitting display words, and do not cascade through other replacement rules. Scoped edits beat global edits; unrelated occurrences remain unchanged. Changed word counts divide the original spoken interval among the replacement words. Studio draft text follows the same correction contract. Caption-ID addressing and a dedicated visual correction form are not added. |
| 7. Isolated phrase fragments | Confirmed QA/configuration gap; improved | Static phrase families accept `min_words_per_caption`. Phrase grouping strongly penalizes undersized groups and can bridge short hesitation pauses when the minimum is unmet, while preserving sentence/cut boundaries. Audit reports one-/two-word states and flags excessive fragmentation. The minimum is a preference constrained by reading duration, layout and actual boundaries, not a guarantee; there is no new semantic language-model scorer. |
| 8. Intentional title-window muting | Confirmed; fixed | Caption audit now derives effective mute windows from explicit mutes and text layers with `mute_captions`. Its lateness/coverage baseline excludes deliberately muted dialogue. It reports mutes without continuous nonempty text coverage for review. Studio also respects title-owned mutes, including early caption holds. This verifies authored coverage, not whether arbitrary title wording is semantically equivalent to the transcript. |
| 9. Captions past program duration | Confirmed; fixed | Compiled events are clipped to the program duration, with the endpoint floored to the ASS renderer's centisecond precision; invisible events are dropped. Audit reports the program endpoint. Studio caption holds use the same endpoint limit. Transcript-caption cache fingerprints include the compiler revision so previously generated timing is not reused as current evidence. The final outro is separate from the program-caption interval. |
| 10. Low-resolution approval preview | Confirmed limitation; optional approval mode added | `render_preview(quality="approval")` creates a complete preview from the original/full-resolution cleaned source, using the same composition and typography code as final rendering. Portrait HD sources yield 720×1280; smaller sources are not enlarged. Approval jobs cannot join a draft-quality job or reuse a draft asset and do not stitch in low-resolution draft segments. `look_at`/`look_at_asset` can deliver native-resolution review frames from a rendered asset. This extracts selected frames from an existing render; a separate service that renders only one high-resolution EDL frame is not added. |
| 11. Preview/watch state confusion | Confirmed; fixed | An explicit complete-preview request now queues the complete video on its first call. Changed-section checks remain a distinct cheap mode and report their own assets/jobs/ranges, including every proof page. `wait_for_job` returns structured job/render state and distinguishes changed-section preview, complete preview, final export, superseded work, and completion without an asset. `watch_video(render=false)` now identifies existing changed-section/final assets when no complete preview exists and gives the exact next call. |
| 12. Captured images omitted | Confirmed; fixed | The delayed `wait_for_job` MCP path returned tool text but dropped its image blocks. It now returns the actual images, metadata and fallback image URLs. Upload keys are unique, image MIME types are retained, and failures are explicit. Frames carry timestamp labels, capture job and EDL provenance; asset review frames retain the render's version rather than being relabeled as the current edit. Failed uploads retain their original provenance for retry. Client display success cannot be observed, so links are also provided. |
| 13. Static cards reported as freezes | Not reproduced as a Valmera platform defect | Shorts 08/11 record external FFmpeg `freezedetect` results as `reviewed_intentional`, with explanations and caption-change evidence. The traced platform verifier does not emit those external technical failures. No platform freeze classifier was changed based on this evidence. |
| 14. Download provenance | Confirmed; fixed | New renders store an actual file SHA-256, render job, asset and EDL version. Download/watch link issuance gets a durable completed job receipt with expiry. `download_url` can recover an exact historical asset, including changed-section proofs. Completed render assets are retained in project history instead of being deleted when superseded. Receipt status is `link_issued`: the server cannot prove that a client's download finished. Old asset checksums remain unavailable when never recorded. |
| 15. Connector/transport failures | Unconfirmed | Candidate 14 does not preserve the failing endpoint, request ID, response body/status or whether a write was applied. There is insufficient evidence to identify a connector, network, timeout, or server root cause. No blanket write retry was introduced. The explicit job state and durable receipts above improve recovery, but generic transport logging/backoff is not claimed as fixed. |
| 16. External graphic authoring | Existing primitives; broader feature request | `add_text`, `add_title_card`, and `add_vector_graphic` already provide editable text, shapes, typography, placement and motion. The external PNG/SVG workflow does not establish that these primitives are absent. A richer graphic editor and shared cross-project style templates remain product feature requests. |
| 17. ASR confidence | Confirmed dropped metadata; fixed | Deepgram confidence and Whisper word probability now survive in indexed words. `get_words` displays measured confidence and flags low values; `add_captions` surfaces uncertain retained tokens before rendering. Missing confidence is explicitly unavailable, not invented. Tool guidance no longer asserts that ASR is always accurate. Older indexes need normal reanalysis to gain confidence. Proper-name detection and automatic context-based transcript rewriting are not added. |
| 18. External crop overlay desynchronization | Confirmed silent mismatch; warning and internal alternative added | Timeline edits now warn when a program-anchored video overlay's underlying source mapping changes. Source-time `frame.focus_track` provides the internal alternate-crop representation and survives trims without uploading a replacement video. Unknown flattened external videos are not automatically rebuilt or retimed: their source correspondence is not stored, and some overlays intentionally remain program-anchored. |

## Evidence anchors

Evidence is preserved under `Valmera/.tmp/valmera-podcast-shorts/palantir-karp-20260905-132939/candidates`.

- `short-17/candidate.json` records finding `vf_65602e16c0d74e87`, the 290-shot/full-source false positive, and three `Object of type datetime is not JSON serializable` failures. `short-10/technical-probe.txt` and `short-15/technical-probe.txt` independently describe the same unused-shot counting.
- Short 18's EDL/caption evidence and the reported `policy Was` failure match the old additive substitution behavior. Regression fixtures reproduce stale replacement state, scoped phrase replacement, and the 31.10-second endpoint issue.
- `short-08/technical-probe.json` and `short-11/technical-probe.json` identify the static-card freeze events as externally measured and intentionally authored.
- `short-16/candidate.json` explicitly records a direct temporary preview download with no separate download job ID.
- Candidate media/probe records establish the 270×480 preview geometry. The new approval path was rendered locally and decoded at 720×1280.

## MCP usage after these changes are deployed

Include the explicit project ID required by the MCP editing surface. Times in a focus track are **source seconds**; correction scopes are **output seconds**.

```json
{"name":"set_frame","arguments":{"project_id":123,"ratio":"9:16","mode":"crop","focus_track":[{"t0":100,"t1":108,"x":0.3,"y":0.4},{"t0":108,"t1":114,"x":0.7,"y":0.4},{"t0":114,"t1":118,"x":0.5,"y":0.5,"mode":"pad_blur"}]}}
```

```json
{"name":"add_captions","arguments":{"project_id":123,"mode":"from_transcript","style":{"preset":"clean"},"min_words_per_caption":3,"max_words_per_caption":4}}
{"name":"set_caption_fixes","arguments":{"project_id":123,"operation":"replace","replacements":[{"from":"better than the future","to":"better in the future","start":4,"end":7}]}}
{"name":"set_caption_fixes","arguments":{"project_id":123,"operation":"list"}}
```

`replace` replaces the complete correction set; use `append` to upsert one rule while keeping the others. Reconfiguring captions preserves active corrections. Every correction response includes caption text/timing evidence without a full encode.

```json
{"name":"render_preview","arguments":{"project_id":123,"complete":true,"quality":"approval"}}
{"name":"wait_for_job","arguments":{"job_id":456}}
{"name":"watch_video","arguments":{"project_id":123,"render":false,"delivery":"url"}}
{"name":"look_at","arguments":{"project_id":123,"rendered":true,"output_times":[1.2],"native_resolution":true}}
{"name":"download_url","arguments":{"project_id":123,"kind":"preview","asset_id":789}}
```

Use the IDs returned by the actual calls. `complete=false` produces changed-section proof; download that asset with `kind="preview_check"`. Logical proof pages share one combined proof asset and render job. For typography review, use the approval asset and URL delivery so a client embedding budget does not request a smaller transcode.

## Validation and limits

- **2,288 tests passed** on fresh copies of the current remote branches: 1,761 worker tests, 433 backend tests, and 94 Studio library tests. Three optional worker media tests were skipped because their fixture video is unavailable. The standalone worker assertion suite also passed during initial validation.
- Worker regression/compatibility tests cover source-range verification, correction precedence and scope, datetime persistence normalization, phrase grouping, title mutes, endpoint clamps, confidence, focus tracks, overlay sync warnings, image delivery, preview quality and rendering.
- Backend MCP tests use mocked PostgreSQL/storage and cover the public image/status/download transport.
- Studio library tests cover correction display, phrase minimums, mute windows, endpoints and the per-shot-render fallback. A clean install from the committed lockfile, the production Next.js build, and the local production smoke test passed. The smoke test checked health, public pages, and signed-out auth redirects. IndexNow publishing was explicitly skipped during the local build; generated sitemap files were unchanged.
- Both repositories passed their tracked-secret scans, and the frontend lockfile audit passed. The port preserves current main's batched proof rendering, MCP mutation/delivery receipts, and execution-provider configuration.
- A real local FFmpeg approval render decoded at **720×1280**. Its extracted frame was visually inspected: the Valmera robot/wordmark and rendered caption remain present. The existing final-outro setting remains **5 seconds**.
- Local Python is 3.12 rather than the project's exact 3.11 pin. Required media/test dependencies were installed only into the existing local virtual environment; repository dependency pins were not changed.
- Retaining historical previews/proof reels increases storage use. Assets already deleted by the old cleanup path cannot be recovered by this code change.
- No live end-to-end connector test or rerender of the 18 production candidates was performed. Provider speaker recognition, semantic ASR correction and pixel-perfect client display are not inferred from local tests.

Unrelated existing repository changes were preserved. Editorial choices concerning headlines, caption aesthetics, B-roll energy, story selection, or stock-footage usage were not classified as platform defects.
