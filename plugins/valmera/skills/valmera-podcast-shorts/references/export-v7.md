# Export and handoff v7

## Export gate

Export only a child in `ready`, using the exact EDL version named by its passed
coordinator QC. If the live EDL changed, return it to QC.

`export_final` is a delivery action, not an editing shortcut. When Valmera
advertises the session tool, call it with:

- explicit `project_id`;
- explicit reviewed `edl_version`;
- `confirmed: true`, because the user's request to run this workflow is the
  durable confirmation to create deliverables.

Queue at most three final jobs. Record every returned job ID. Wait for terminal
results without a scheduled automation, then obtain `download_url(kind="final",
edl_version=...)` and save the file under `exports/`.

If `export_final` is not advertised, do not call a stale name and do not claim
the workflow exported anything. The safe options are:

1. use an explicitly user-authorized, authenticated Valmera Studio session to
   press Download for each reviewed version; or
2. stop at `ready` and report `export_capability_unavailable`, including the
   child IDs and EDL versions.

Never use browser automation against an unknown account or infer that a preview
is a final export.

## File verification

For the user's current four-style brief, apply the branding contract in
`style-lanes-v7.md`. The native Valmera corner watermark and complete native
"Edited by Valmera AI" ending are required deliverable content. Budget the
actual native ending duration in advance and preserve it unchanged. Never
trim it from a download, cover/crop a corner mark, disable branding, or deliver
a preview/local unbranded re-encode as a final. If duration needs correction,
revise the editorial portion, obtain fresh QC, and export again.

For each downloaded final verify:

- child ID and expected EDL version from the final asset metadata;
- decodable MP4 with one video stream and an audio stream when source speech
  exists;
- the assigned output ratio and actual pixel dimensions; do not require 9:16
  when the user selected another shape. Check rounded-card composition
  separately from dimensions;
- duration within 0.10 seconds of the approved preview or explained by a known
  final-only end card; any such card must also satisfy the user's total runtime
  and ending requirements;
- for the current brief, the complete native Valmera ending visibly present
  through its last frame and the corner watermark correctly placed for this
  picture geometry using the suitable admin mode from `style-lanes-v7.md`.
  Verify the actual native placement, legibility, safe margins, and text/face
  clearance; exact conformity to earlier corner examples is not a gate;
- no black/frozen/silent corruption using the same deterministic gates as QC;
- SHA-256 checksum and nonzero byte count.

Recheck planned silence windows and music cues in the final's program time.
In a no-music hook-to-montage export, every post-hook editorial audio source
must be silent. Inspect inserted clips as well as the podcast track. In an action
opener, silence ends at the authored dialogue start. Never classify these
planned intervals as corruption or fill them with sound.

Inspect final-only branding separately from the editorial preview, without
applying no-B-roll, persistent-topic-headline, or accidental-tail rules to the
native branded ending. Check its actual duration and appearance rather than
assuming a metadata stamp or a successful job proves it exists. Use the admin
placement choices to resolve ordinary placement issues; do not turn an exact
corner difference into a capability failure. Only the coordinator changes
shared placement settings. Keep compatible renders in one mode and download
them before a mode switch, following `style-lanes-v7.md`; editors keep working.
Missing or damaged branding still needs repair. If a real access/render issue
remains, record it once and continue the other work instead of repeating
failure reports for an unavailable cosmetic coordinate.

Every downloaded final must start with the assigned style-lane slug, followed
by `__`. Use the exact stable lane ID from the reviewed assignment, not an
ordinal like `Style 2` or a guessed visual label. For example:

```text
hook-to-silent-montage__01-short-slug__project-123__edl-v7.mp4
```

The four current prefixes are `fast-conversation__`,
`hook-to-silent-montage__`, `silent-action-to-conversation__`, and
`headline-conversation__`. Other approved profiles use their own stable lane
IDs. Download to a temporary name if necessary, then rename the verified file
within `exports/` before `run_state.py export`. Preserve uniqueness with the
short ID/project ID/EDL version; do not overwrite another final. The run-state
export command rejects a missing or wrong style prefix, and the manifest
records `style_lane` alongside the absolute path.

If delivering multiple ratios, review and save each version separately before
changing its frame settings. Do not overwrite an already verified final.

## CRM handoff manifest

Create `exports/manifest.json` with this minimum shape:

```json
{
  "version": "valmera-shorts-export-v1",
  "run_id": "...",
  "source": "...",
  "generated_at": "ISO-8601",
  "items": [
    {
      "short_id": "short-01",
      "child_project_id": 123,
      "edl_version": 7,
      "title": "...",
      "style_lane": "hook-to-silent-montage",
      "file": "/absolute/path.mp4",
      "sha256": "...",
      "duration_s": 48.2,
      "caption": "optional publishing copy",
      "status": "exported"
    }
  ],
  "exceptions": []
}
```

The manifest is the boundary with a downstream publisher. Do not inspect,
configure, or operate a CRM unless the user separately places it in scope.

`run_state.py finalize` writes the base manifest shown above. After it runs,
enrich each exported item from the matching reviewed assignment, QC report,
and measured final: `style_lane`, `source_ranges`, `output_ratio`, `width`,
`height`, `frame_treatment`, `intentional_silence_spans`, `music_cues`, and
`qc_report`. Keep IDs, file paths, checksums, versions, and exceptions intact.
For persistent-headline conversation, retain the reviewed `picture_region`
and `headline` text/window/placement/font/weight as well. For the current brief,
also retain `style_choice_reason`, `closest_alternative`, and `branding` with
the selected admin mode, verified corner anchor/bounds, native end-card start/end,
and final evidence.
Use exact program seconds for cue positions, state that no music is included,
and do not claim synchronization to an unselected song. Recheck the enriched
manifest before handoff; finalization alone does not validate these additions.

An export is complete only after the local file and manifest entry pass. A
Valmera job reaching `done` without a verified download is not delivery.
