# MCP editing failure audit — 2026-08-12

This audit separates deterministic product defects from editorial/model
mistakes observed in the long-form MCP editing attempt. “Mixed” means the
agent made a poor choice, but the tool surface made that choice easy and its
recovery unnecessarily hard.

## Classification

| Failure | Class | Root cause and resolution |
|---|---|---|
| First/short captions appeared late or disappeared | Tool | The EDL event list was being treated as proof of burned pixels, while placement could omit words when no band was perfectly empty. Caption Design v2 now chooses the least-obstructed measured band, anchors inside the real foreground, never drops spoken words, and the exact compiled ASS is audited for first-word latency before pixel review. |
| Sequential manual words appeared together | Tool | Manual items were silently stretched to a 0.6 s minimum. Manual timing is now exact; the schema’s documented 0.05 s minimum is the only minimum. |
| Valid 0.05 s items were rejected as shorter than 0.05 s | Tool | Binary-float subtraction after centisecond rounding. Validation now compares integer centisecond ticks. |
| `animation="none"` still popped spotlight/karaoke words | Tool | Dynamic and premium branches ignored the sentinel. Motion is now disabled in every renderer and in the browser draft; active-word colour remains. |
| Captions landed in blurred bars/across faces or clipped long words | Tool | Placement reasoned in whole output-frame bands, not foreground bounds, and classic captions could not use exact anchors. Placement now measures foreground-aware safe zones, writes exact `anchor_y`, stabilizes per shot, and the composition engine shrinks over-wide heroes while preserving hierarchy. |
| `look_at` could not inspect finished captions | Tool | It sampled source/proxy imagery. `rendered=true` now resolves the exact immutable EDL preview and inspects its burned pixels. |
| Nine evenly spaced tiles missed short caption states | Tool | Uniform sampling has low caption coverage. Every caption render now builds a separate sheet from up to 16 real compiled ASS state changes; `audit_captions` exposes those exact output times. |
| Music was heard/reported as voiceover, or “no music” was falsely reported | Mixed | The listener guessed semantic roles from sound while the authored EDL already knew them. Audio review now receives deterministic role/file/offset/gain state; `audit_audio_mix` detects doubled roles and missing beds. The agent still owns the final listening judgement. |
| “Social master” produced unsafe positive true peak | Tool | Single-pass loudnorm could be followed by codec/inter-sample overs. Social mastering now targets −14 LUFS / −2.0 dBTP and adds a latency-compensated hard ceiling; QC and product copy use the same target. |
| A song needed a 51 s source offset, so it was externally trimmed or mis-added | Agent + surface | `add_music(offset_s)` already supported this, but voiceover did not and guidance did not make the distinction sufficiently explicit. Voiceover now supports `source_offset_s`; the mix audit names every offset and role. |
| Music authenticity/licensing was treated as proven | Mixed | Search/title/channel can support identity, never publication rights. Fetch results now persist an unverified-rights warning and explicitly state that download is not a license. Product and MCP copy now distinguish source-supplied metadata from uploader authentication or legal clearance. |
| The same B-roll asset was reused through different source windows | Mixed | The agent treated a different window as different footage and no tool rejected it. Visual adders now reject exact asset reuse across inserts/overlays/takeovers unless `allow_repeat=true` is explicitly intentional. A screen-takeover’s continuous handoff into its own destination insert is exempt. |
| Stock result had no description, yet the agent inferred its content | Mixed/MCP | MCP stock search was text-only because thumbnails were gated to the in-house sight path. MCP now receives labeled thumbnails. A result with neither description nor delivered thumbnail cannot be selected. |
| Shorts began on an isolated quote/answer | Agent + tool | Selection instructions rewarded hooks without enforcing conversational completeness. The planner now prioritizes full setup→development→resolution arcs and deterministically restores a nearby diarized question before an isolated answer. |
| Repetition review confused spoken repetition with duplicated editing | Tool | It compared transcript shingles without source provenance. Repetition is now classified as `edit_duplicate` only when the same source moment is reused; distinct source moments are `spoken_repetition`. Auto-repair is authorized only for edit duplicates. |
| Visual critic alternated between black/missing and fine | Tool | Sparse model observations were promoted to hard repair orders without timestamp/confidence. Black/continuity/insert findings authorize repair only with an exact time and confidence ≥0.90; lower-confidence findings remain explicitly unconfirmed. |
| The agent could identify a defect after its second candidate but could not fix it | Tool | A hard per-turn candidate ceiling blocked defect-driven correction. Optional taste exploration remains capped, but every concrete finding on the latest proof earns exactly one corrective version and one new proof; a still-defective proof can earn the next. |
| `get_edl` returned truncated/invalid JSON | Tool | Output was sliced by character count. It now returns complete JSON when small, a complete compact index when large, and valid section pagination for repair work. |
| Calls could hit the wrong project after switching among parent/short projects | MCP tool | A mutable connection-wide active pointer routed calls. Every editor call and every project-targeting session call (state, upload, status, watch, download, export) now requires an explicit `project_id`, checks ownership, and echoes project id/title—including delayed job results. |
| The agent authored hundreds of one-word captions instead of using transcript captions | Agent | It selected the wrong abstraction and compounded the timing defect. Tool doctrine now says to use `from_transcript` for ordinary speech, reserve manual items for dictated/translated copy, run `audit_captions`, render once, and inspect real caption pixels before claiming success. |
| The agent copied project-scoped asset keys, repeatedly rendered/listened, or used voiceover as a music workaround | Agent | These were planning mistakes, not missing core capabilities. Explicit project scope, asset-reuse guards, deterministic mix audit, valid paginated EDL reads, and defect-linked proof permissions now make those mistakes visible and recoverable rather than silently destructive. |

## Verification contract

Caption QA is no longer a subjective “looks okay” step:

1. The renderer compiles the exact ASS artifact used by ffmpeg.
2. `audit_captions` checks first-word latency, word coverage, nonpositive states,
   and overlap of distinct visual states.
3. Preview rendering samples up to 16 real caption state changes.
4. `look_at(rendered=true, output_times=...)` inspects the exact burned pixels.
5. The browser draft mirrors Caption Design v2 grouping, punctuation, placement,
   exact anchors, and motionless karaoke behavior.

The broad 3×3 sheet remains useful for pacing and continuity; it is no longer
treated as sufficient evidence for caption correctness.
