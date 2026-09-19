# Execution performance contract

The edit document and a rendered media artifact have separate lifecycles.

## Interactive edits

Studio applies supported timeline commands locally and saves an ordered log
using `operation_id` plus `base_version`. An acknowledgement is committed in
the same transaction as its EDL version. Retrying the same envelope returns
that acknowledgement; reusing its ID with different arguments is a conflict.
Older-version branching remains intentional. Active agent ownership remains
protected. An ambiguous network failure must be reconciled before discarding
the local command.

An organizational split changes no media. `split_keep_boundaries` and insert
`split_parent` preserve subdivisions in the editable document. The shared
render adapter joins unchanged pieces before applying transitions or motion.
Changing a piece's source interval or properties prevents that join.

`preview_mode=on_demand` records an explicit artifact policy in the edit audit.
State recovery must not manufacture a full preview solely because that
revision has no MP4. Explicit preview and export requests still work. A split
preserves an already-running preview of its unchanged picture.

## Media work

Both main-source and canvas rendering use the same bounded insert input plan.
Each input seeks near its selected window, including speed mapping and one
second of preroll. Timestamp rebasing accounts for the main renderer's
`-copyts` mode. Resident immutable bytes take precedence over ranged reads;
large inserted video on Cloudflare can read directly from storage.

Picture identity excludes only known audio controls. If the picture, source,
quality, caption dependencies, renderer stamps and output duration match an
existing artifact, audio can be rebuilt and muxed with its encoded video.
This applies to canvas, draft, approval and final paths. Full-program audio
processing remains necessary for normalization. Draft pixels never become a
final export. Forced renders bypass reuse. Verification failure retains the
existing full-render fallback.

`input_fetch_s`, `input_probe_s`, and `ffmpeg_read_and_render_s` are detail
counters within the existing stage walls; do not sum them with `encode_s`.
FFmpeg time includes network reads when the input is remote.

Preview placement keeps successive project previews on the same bounded
interactive shard pool. Bulk exports keep their distributed placement.
Simple MCP EDL and skill reads use authenticated API reads without reserving
an executor. More involved metadata responses retain the full tool path.

## Verification

Shared command fixtures check browser/server parity. Queue tests cover rapid
edits, lost acknowledgements, reloads, conflicts and explicit discard. Real
FFmpeg tests compare selected decoded frames, bounded audio timestamp
precision, and encoded video hashes after an audio-only revision.

The rollout does not claim a complete browser compositor or universal
incremental effects rendering. Those are separate extensions of this
document/artifact contract. Unsupported local commands still use the server's
authoritative operation semantics; unsupported draft effects retain rendered
preview fallback. Measure whole-task latency and compute cost before claiming
an end-to-end speedup.
