# Valmera Workflow Contracts

## Contents

- Tool-catalog preflight
- Selection payload
- Mutation and retry discipline
- Concurrency
- Built-in CTA

## Tool-catalog preflight

The direct MCP workflow requires:

- `create_project(title, kind="shorts")`;
- explicit upload and `index_status` polling;
- topic-mode reference upload using
  `upload_finish(kind="clip", role="shorts_reference", duration_s=...)`;
- exhaustive main-source transcript and shot-page reads, plus dense scheduled
  reference-asset samples and checks at observed boundaries;
- `make_shorts(project_id, clips=[...], style_note=...)` with caller-authored
  story arcs;
- `shorts_status` to obtain immutable child IDs;
- `open_short(child_project_id=...)` and normal direct editor tools.

If the active `make_shorts` schema lacks `clips`, or the catalog still exposes
`edit_shorts` or `export_final`, stop before any new parent selection/planning
mutation. The connector metadata is stale. Optional `count` may still appear
in the current schema; `clips` must exist and be required alongside
`project_id`. Never reinterpret the older count-only tool as an equivalent
workflow. Direct editing of an already resolved child may still proceed when
`open_short` and the normal editor tools are current; that path never calls
`make_shorts`.

Recovery depends on provenance. A developer-mode connection must be refreshed
from ChatGPT Plugins after the server is deployed. A packaged/private/published
remote plugin (including `@created-by-me-remote`) must scan the live MCP server,
submit and publish a new plugin version, and only then be refreshed/reconnected.
Starting a new Codex task or reconnecting by itself cannot update a published
metadata snapshot.

For topic mode, also stop if `upload_finish` does not expose
`role="shorts_reference"` and `duration_s`. Uploading the chosen viral Short as
an ordinary clip is not an acceptable fallback: it can enter the editable asset
pool and may not be inherited as a protected style reference.

`edit_shorts` delegates to Valmera's separate in-house agents and is unavailable
for this direct Codex workflow. `export_final` is deliberately unavailable over
MCP. Leave verified children ready for manual Studio export.

## Selection payload

Pass only these fields to `make_shorts`:

```json
{
  "start": 120.0,
  "end": 178.0,
  "title": "...",
  "hook": "...",
  "score": 92,
  "story": {
    "setup": "...",
    "development": "...",
    "payoff": "..."
  }
}
```

Ranges must be 10-120 seconds, near sentence boundaries, inside the source,
and non-overlapping. A source under one minute should be edited directly rather
than passed through the multi-clip workflow. Pass every validated qualifying
range in the single logical `make_shorts` call. Do not truncate the selection
to eight or another arbitrary editorial quota; source duration and the
non-overlapping minimum-duration rule are the natural limit.

## Mutation and retry discipline

- Creation and `upload_start` are not idempotent. Persist their returned IDs
  immediately.
- `upload_finish` may be resumed only with the same storage key and multipart
  result.
- `make_shorts` must be called once. Poll the returned job or `shorts_status`.
- Add operations such as music, overlays, and captions are not safe to repeat
  after an ambiguous timeout. Reread `get_edl` and assets first.
- Prefer setters for repair and `apply_edit_recipe` for two or more compatible,
  already-planned EDL changes.
- Generated children are ordinary projects. Pass the immutable child ID on
  every tool call; never trust an active-project pointer or a title.
- The coordinator owns the exact clip payload. `make_shorts` may snap its
  boundaries to nearby word timing while seeding raw child EDLs, but it may not
  select, rank, style, or creatively edit them. Compare every returned child
  range with its approved range before assignment.

## Concurrency

Different child project IDs are safe ownership boundaries, but they do not give
production-capacity isolation. The production MCP endpoint shares three
synchronous API workers with Studio, and MCP previews share the customer media
queue. A durable queued job is safe from loss, but its HTTP request and render
can still delay users.

Keep one sidebar-visible, user-owned Codex task per child and an unlimited FIFO
of assignments, but allow only one outstanding Valmera MCP request globally,
including `wait_for_job`. Create each task just in time, keep it visible after
completion, and do not start the next Valmera-calling task until the current one
is terminal or safely paused with no ambiguous mutation and no outstanding job.
The coordinator communicates with the task automatically; the user may open and
talk to it directly. Never substitute an inaccessible subagent for an editor.

Enforce this through the canonical run registry. Every request requires an
atomic `begin-run-call`/`end-run-call` or `begin-call`/`end-call` permit;
read-only lease checks are not authorization. Never close or rotate a lease
while its call permit remains in flight.

Codex slots, MCP worker slots, and cached project-context count are not
safe-capacity signals. A larger advertised capacity is not permission to exceed
this skill's one-request invariant. Raise it only by deliberately revising the
skill after production MCP/API/render isolation is deployed. This concurrency
safeguard must never limit the total number of shorts processed.

## Built-in CTA

The five-second `Edited by Valmera AI` MP4 is already bundled in Valmera and
appended by the final-render pipeline. It is video-only, outside the EDL, and
normally absent from previews.

Never upload or insert a user-provided copy. An editor can only establish that
the EDL is `tail_eligible` and renderer carry is `configured`: the music item
reaches the exact final program frame, is unmuted, has an audio stream, has no
authored end fade or whole-program fade-out, and either loops or has proven
remaining source coverage through program end plus the full five-second CTA.
Those facts establish renderer carry eligibility/configuration only. They do not
prove actual signal continuity or perceived sound through the card. Renderer
outro contract v9 is configured to exclude dialogue/voiceover/SFX from the tail
and fade a qualifying carried mix over the card's final 0.75 seconds. Ordinary
previews cannot verify the appended tail. Finals rendered before v9
must be exported again; never fabricate preview evidence or duplicate the card
as a workaround.
