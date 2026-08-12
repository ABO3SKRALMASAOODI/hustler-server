# Valmera over MCP — edit video from your own Claude session

**Status: private.** No UI, no marketing, no signup path. Two ways in — an
OAuth login from claude.ai, or a static token only the admin account can mint —
and BOTH re-check the account's email against `MCP_ALLOWED_EMAILS` (default:
`thevalmera@gmail.com` alone) on every single request. Anyone else who finds
the URL reaches a login screen that will never say yes.

## What it is

An MCP endpoint at `POST /mcp` that hands **the complete Valmera editor tool
registry** to whatever model you are running — Claude in the claude.ai app, or
Opus/Fable in Claude Code — on your Anthropic subscription. Your model does the
thinking; Valmera does the editing. That is exactly the trade the `mcp` plan
was always written around ("brings its own model").

**There is no second editor.** The tools are not re-declared for MCP. The
worker publishes its live registry on boot; the backend clones each live
schema only to add the transport-level required `project_id`, title and MCP
behavior hints. Every actual editor argument, description and honest-off gate
still comes from the same registry (a tool whose backing service has no key is
hidden from both). Execution happens in the worker, in the same `ToolContext`
an agent turn uses. There is no capability copy to keep in sync —
`worker/tests/test_mcp_surface.py` fails if one appears.

On top of that, twelve **session tools** the studio UI normally covers and a
headless model cannot: `list_projects`, `open_project`, `open_short`,
`create_project`, `project_state`, `upload_start`, `upload_finish`,
`index_status`, `shorts_status`, `wait_for_job`, `download_url`, `watch_video`.
Final export is intentionally absent: MCP prepares and verifies the edit, and
the user creates the deliverable from Valmera Studio.

## Turning it on (once)

**1. Apply both migrations** — DONE on production 2026-07-27, do not re-run
(they are idempotent, but the CHECK swap takes a brief ACCESS EXCLUSIVE lock on
`video_jobs`; if you ever do re-run them, keep the `SET LOCAL lock_timeout`).

```bash
psql $DATABASE_URL -f backend/migrations/008_mcp.sql
psql $DATABASE_URL -f backend/migrations/009_mcp_oauth.sql
```

008 relaxes `video_jobs.type`'s CHECK to accept `mcp_tool` and adds
`mcp_tokens` + `mcp_catalog` — without it every call fails at the INSERT. 009
adds the four OAuth tables claude.ai needs.

**2. Deploy** backend + worker (push to `main`) — DONE. The worker publishes
its tool catalog on boot and, if that fails (migration not applied yet), keeps
retrying from the reaper until it lands: look for `[mcp] published tool
catalog`. The backend adds the session tools above and explicitly filters final
export even if an old worker catalog or connected client still remembers it.

**Capabilities currently OFF on the worker**, so they are hidden from the
connector rather than failing when called: `search_stock` / `add_stock_media`
(needs `PEXELS_API_KEY` or `PIXABAY_API_KEY`) and `generate_image` (needs
`IMAGE_API_KEY` — an xAI key, since DeepSeek publishes no image model). Vision
(`look_at`) IS on.

**3a. Connect from claude.ai** (the Claude app — web, desktop, mobile):

> Settings → Connectors → Add custom connector → URL:
> `https://entrepreneur-bot-backend.onrender.com/mcp`

Claude discovers the authorization server from the 401, registers itself, and
opens a Valmera login page. Sign in with **your email + password** (the same
one you use on valmera.io) and press Allow. That is the whole setup — no token
to copy anywhere. If your account signed up with Google it has no password
yet; set one with "Forgot password" on valmera.io first.

**3b. Or connect Claude Code**, which can carry a static token instead. Mint
one with your admin JWT (copy it out of the studio's localStorage):

```bash
curl -sS -X POST https://entrepreneur-bot-backend.onrender.com/mcp/tokens \
  -H "Authorization: Bearer $VALMERA_JWT" \
  -H 'Content-Type: application/json' -d '{"label":"claude-code"}'
```

The response contains the token **once** (only its sha256 is stored) plus the
exact line to run:

```bash
claude mcp add --transport http valmera \
  https://entrepreneur-bot-backend.onrender.com/mcp \
  --header "Authorization: Bearer vlm_mcp_..."
```

Either way, ask it to "list my Valmera projects" to check.

## How the claude.ai connection actually works

Its connector UI has no header field, so a static token is unusable there. The
sequence — all of it implemented in `backend/routes/mcp_oauth.py`, all of it
tested in `backend/tests/test_mcp.py`:

```
POST /mcp with no token
  → 401 + WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource"
GET  /.well-known/oauth-protected-resource      which authorization server? (RFC 9728)
GET  /.well-known/oauth-authorization-server    its endpoints + PKCE (RFC 8414)
POST /mcp/oauth/register                        Claude enrols itself (RFC 7591)
GET  /mcp/oauth/authorize                       Valmera login + consent screen
POST /mcp/oauth/authorize                       → 302 back to claude.ai with ?code=
POST /mcp/oauth/token                           code + PKCE verifier → access + refresh
POST /mcp with the access token                 editing
```

Properties worth knowing:

- **PKCE (S256) is mandatory.** These are public clients holding no secret.
- **Registration is open, and grants nothing.** The client registers before any
  human is involved; authorization still needs your password *and* an address
  on `MCP_ALLOWED_EMAILS`. A stranger who registers gets a login that will
  never say yes.
- **An unregistered `redirect_uri` dead-ends on our own page** rather than
  redirecting — an authorization server that bounces errors to an unvalidated
  URI is an open redirector.
- **Refresh tokens rotate**, and a **replayed authorization code revokes the
  whole grant** (a code used twice may have been stolen).
- **The open project lives on the grant**, not the token, so a refresh — or a
  reconnect — resumes on the same project.
- Access tokens last 8h (`MCP_ACCESS_TTL_S`), refresh 90d
  (`MCP_REFRESH_TTL_S`). Disconnecting in Claude calls `/mcp/oauth/revoke`,
  which kills the grant.

## Using it

```
list_projects  →  open_project(3)  →  (the whole project state comes back)
  →  cut_silences(project_id=3) / add_captions(project_id=3) / ...
  →  render_preview(project_id=3)  →  download_url(project_id=3)
  →  tell the user the verified edit is ready to export in Valmera Studio
```

`open_project` loads the state and preserves a navigation pointer for legacy
clients. Every normal editor tool—and every project-targeting session tool such
as upload, status, watch, and download—requires an explicit
`project_id`. The backend verifies account ownership and echoes the id/title in
results, including delayed `wait_for_job` replies. It never guesses an editing
or review target from mutable connection state. This is intentionally
redundant: a long session can hop among a parent and many shorts without one
missed switch sending a valid edit or review to the wrong project.

### Podcast to Shorts over MCP

The batch workflow is first-class rather than something the connector has to
approximate with individual cut calls:

```
create_project(title="My podcast", kind="shorts")
  → upload_start(project_id=ID) / upload_finish(project_id=ID)
  → index_status(project_id=ID) until done
  → shorts_status(project_id=ID) until the child projects are ready
  → open_short(parent_project_id=ID, card=N) → edit/render/watch with its child ID
  → tell the user which verified edits are ready for Studio export
```

The Shorts planner starts automatically after a `kind="shorts"` project's
main video finishes analysis. On an existing normal long-video project,
`make_shorts` starts the same pipeline and returns its job id; poll that with
`wait_for_job` or use `shorts_status`. `list_projects` labels each generated
short with its parent so a caller never has to guess which new project belongs
to which podcast. A source under one minute is already a direct short and is
edited normally rather than rejected by the multi-clip extractor.

`open_short(parent_project_id=BOARD, card=N)` (or
`open_short(child_project_id=ID)`) is the explicit direct-edit path: it switches the MCP
connection to that generated child, after which the complete live editor tool
registry operates on the child's EDL. No Valmera agent is called. By contrast,
`edit_shorts` is deliberately a delegation tool: it forwards one prompt into
each selected child's chat and starts Valmera's in-house agent there. An MCP
model must not use it when the user asked that outside model to perform the
edits itself.

**Uploading a local file.** MCP arguments are JSON, so bytes never travel over
the protocol. `upload_start` returns a presigned URL and the exact `curl` to
run; under 64 MB the model does it itself in one command. Bigger files are
multipart (one ETag per part) — run the helper instead:

```bash
export VALMERA_MCP_TOKEN=vlm_mcp_...     # a static token; mint one as in 3b
python3 scripts/valmera_upload.py ~/Movies/talk.mp4 --project 3
```

(Or just upload it in the studio as usual and `open_project` it from Claude —
the connector does not care how the footage arrived.)

A main video then has to be **analyzed** (transcript, shots, silences) before
it can be edited — minutes on a long one. `index_status` reports it.

**Watching it happen.** Every MCP tool call writes an `activity` row into the
project's chat, tagged `source: "mcp"`. Open the project in the studio and you
see your Claude session edit in real time, with the preview updating.

## `watch_video` — for a model that can actually watch video

Every other way an MCP caller can "see" the footage ends in *our* words:
`look_at` decodes frames, runs them through **Valmera's** vision model, and
what crosses the wire is a paragraph. That was the only option while every
model on the far end read images at best. It is the wrong one for a model that
takes video input — Grok, and whatever comes next — because a lossy summary
written by a smaller model is standing between the editor and the material.

`watch_video` hands over the **file**. Pixels, audio, timing.

```
watch_video(project_id=3)                                  the current edit, whole
watch_video(project_id=3, kind="source")                   the raw uploaded footage
watch_video(project_id=3, start=12, end=30)                just that program window
watch_video(project_id=3, delivery="inline", max_mb=8)     shrunk for the reply
watch_video(project_id=3, kind="asset", asset_key="clips/…") one uploaded clip
```

**It usually costs nothing.** The artifacts already exist and are already the
right shape: the assembled program is the **preview render** of the current
EDL (~480p H.264+AAC — literally what the studio player streams), and the raw
footage is the **540p index proxy** every `look_at` and render already reads.
So the normal answer is a presigned link to an object that already exists — no
decode, no encode, no wait. Re-encoding happens for exactly two reasons, both
of them things the caller asked for: a `start`/`end` **window**, or `delivery:
"inline"` / `max_mb`, because a 60 MB file cannot travel inside a JSON-RPC
reply. Both say so in the answer.

`kind="timeline"` **renders the current edit first** if it has never been
rendered — a render can outrun the request, in which case you get the usual
`STILL RUNNING — job N`, and calling `watch_video` again after `wait_for_job`
picks up the finished file instead of starting a second render. Pass
`render=false` to watch the last render that exists; it then tells you which
EDL version that was and that everything since is missing from what you are
watching.

**It hands over the SOUND.** Asked "can you hear the music?", a model
downloaded the MP4 and built a **spectrogram** — because the reply carried
frames and no audio, while its own text said "H.264 + AAC" and so invited the
model to claim it had heard something it was never sent. MCP has an `audio`
content type. Every `watch_video` reply now carries the window's **complete,
continuous audio** as a mono mp3 (`audio/mpeg`, 48 kbps, ~180 KB for 30s).

Sound is cheap where picture is not — 30s of audio is ~180 KB against 2.9 MB
of video — which is exactly why the whole track can ride in the reply when the
video cannot. Past about **85 seconds** the budget can no longer pay for a
listenable bitrate, so the reply attaches **no** audio and says to narrow the
window; it never ships a silently truncated or unlistenable track.
`MCP_AUDIO_OUT=0` turns it off.

**It hands over the PICTURES with the link.** A link on its own is homework:
asked what was in a 28s program, Grok downloaded the MP4, shelled out to
ffmpeg, extracted 29 frames and built a spectrogram — to answer a question the
tool should have answered. So every `watch_video` reply carries a **contact
sheet of the window** (default 12 tiles, `MCP_WATCH_FRAMES`, labelled in
timeline seconds) as MCP `image` content. One picture, ~60 KB, ~1.5k tokens.
`frames=false` turns it off.

**And `look_at` now returns the frames themselves.** Over MCP it used to run
*our* vision model over them and send back a paragraph — second-hand, billed
to us, unarguable. The protocol always allowed image content in a tool result;
the plumbing just never did it (`ctx.sight_out`, round 83e). Image is the one
non-text block worth trusting: it is the most widely implemented type, it is
what the in-house agent already receives, and it degrades to plain text if a
client drops it — unlike a video blob, which cost two sessions.

**How the FILE comes back: A LINK, unless you explicitly ask otherwise.** The reply
is a text block — what it is, how long, and **which clock its seconds are on**
— carrying a plain unauthenticated URL to an ordinary MP4. Fetch it and watch
it. `delivery="inline"` additionally embeds the file as an MCP `resource`
block (`mimeType: video/mp4`, base64), capped at `MCP_VIDEO_INLINE_MAX_MB`.

**Embedding is OFF unless the operator turns it on** (`MCP_VIDEO_ALLOW_INLINE
=1`), and the model cannot override that. It took two dead sessions on
2026-08-03 to get this right:

1. It first embedded whenever the file fit, assuming a client that cannot
   render a video block would ignore it. Grok **stringified** it — a 2.9 MB
   preview arrived as **4 million characters** of base64 and ended the session
   ("the conversation is too long"), while the tool call reported success.
2. So embedding became an opt-in argument. Grok passed it. Of course it did:
   it had just been asked whether it could hear the music, the tool offered
   the actual file, and the only caveat was *"if your client decodes video
   content blocks"* — a fact about the CLIENT, which the model cannot check
   and will assume is yes.

**An opt-in is only honest when whoever takes it can evaluate the
consequence.** Here the model cannot and the consequence is unrecoverable, so
the switch belongs to the operator, who can see what their client does with a
resource block. With it off, `inline` is not in the `delivery` enum at all
(honest-off gating, same as an editor tool with no API key) and a stale client
that asks anyway gets the link plus a plain sentence saying why. With it on,
the rule is still enforced independently in the worker and the backend, so
neither alone can put bytes in a reply.

**The trap it is written to avoid:** a watched *program* runs on OUTPUT
seconds, and most editing tools take SOURCE seconds — after one cut the two
clocks disagree everywhere. Every reply names the clock and the tools that
speak it (`cut_output_range`, `look_at(output_times=…)`, the scene map in
`project_state`). `look_at` is still the better tool for reading exact
coordinates off a frame: it burns a tenths grid onto what it captures, which
is where zoom aims and text boxes get their numbers.

Shrunk copies are written to `media/{project_id}/mv_*.mp4` — the same prefix
renders use, so deleting a project reclaims them — and are keyed by their
encode settings, so asking for the same window twice encodes once.

## Things worth knowing

- **Slow tools answer with a ticket, not a lie.** A render or a burned-text
  erase outruns `MCP_SYNC_WAIT_S` (25s), so the reply is `STILL RUNNING — job
  N` and the model calls `wait_for_job(N)`. It is never reported as failed.
- **Two editors are refused, both ways.** An MCP call is rejected while an
  in-house agent turn is live on that project, and a studio chat message is
  rejected while an MCP call is in flight. Racing EDL writes are how you get
  an edit that contains half of each idea.
- **Editor calls are immutable-project scoped.** Every catalog schema requires
  `project_id`, ownership is checked before enqueueing, and every result starts
  with `PROJECT <id> — <title>`. A stale active-project pointer cannot redirect
  a caption, cut, render, or read call.
- **Nothing is charged.** An MCP call runs none of our agent model, so no
  credits are deducted. But vision (`look_at`), image/video generation and
  stock fetches are real money on real providers, recorded to `llm_calls`
  under the MCP job id — visible in admin, billed to nobody. **Decide this
  before the surface is ever sold**, not after. `watch_video` costs no
  provider anything, but a shrunk copy is CPU on the dispatcher and every
  fetch of the link is R2 egress — cheap per call, unbounded per session.
- **The instructions are the whole doctrine.** `initialize` returns the agent's
  44 KB system prompt + the generated capability list + an MCP workflow note,
  so your model edits the way Valmera edits rather than merely reaching its
  tools. Set `MCP_INSTRUCTIONS=brief` on the backend to drop the doctrine and
  keep only the capability list — that is the A/B for "how much of the quality
  is the prompt and how much is the model".
- **Capacity.** The worker runs MCP on its own lane (`WORKER_MCP_SLOTS`, 2), so
  a tool call never queues behind a customer's agent turn and never takes a
  slot from one. Renders still share the single media lane with everyone else.
- **Before this gets real traffic**, move gunicorn to
  `--worker-class gthread --threads 8` in `start.sh`. It deploys with 3 *sync*
  workers today, so a 25s wait ties up a third of the API. That is fine for one
  founder and not fine for customers — raise `MCP_SYNC_WAIT_S` only after.

## Revoking

- **claude.ai**: disconnect the connector (it calls `/mcp/oauth/revoke`), or
  `UPDATE mcp_oauth_grants SET revoked_at = NOW()` for a specific connection.
- **Claude Code**: `curl -X DELETE .../mcp/tokens/1 -H "Authorization: Bearer $VALMERA_JWT"`
- **Everything at once**: set `MCP_ALLOWED_EMAILS` to an address nobody holds.
  It is re-checked per request, so every live session dies on its next call —
  no deploy, no token hunt.

## Env

| Var | Where | Default | What |
|---|---|---|---|
| `MCP_ALLOWED_EMAILS` | backend | admin email | who may connect at all |
| `BACKEND_URL` | backend | the onrender URL | the OAuth `issuer` — must be this server's real public origin |
| `MCP_ACCESS_TTL_S` | backend | 28800 | access-token lifetime |
| `MCP_REFRESH_TTL_S` | backend | 7776000 | refresh-token lifetime |
| `MCP_SYNC_WAIT_S` | backend | 25 | longest a call blocks before ticketing |
| `MCP_INSTRUCTIONS` | backend | `full` | `brief` drops the doctrine |
| `MCP_AUDIO_OUT` | worker | on | attach the window's sound to `watch_video` |
| `MCP_AUDIO_MAX_KB` / `MCP_AUDIO_MAX_KBPS` / `MCP_AUDIO_MIN_KBPS` | worker | 256 / 48 / 24 | the audio budget, and the floor below which a long window gets no sound rather than an unlistenable one |
| `MCP_AUDIO_MAX_MB` | backend | 4 | outer bound on audio carried in a reply |
| `MCP_WATCH_FRAMES` | worker | 12 | tiles on the contact sheet `watch_video` returns |
| `MCP_MAX_IMAGES` | worker | 4 | runaway guard on pictures per tool call |
| `MCP_IMAGE_MAX_MB` | backend | 6 | biggest single picture carried in a reply |
| `MCP_VIDEO_ALLOW_INLINE` | backend | **off** | may `watch_video` embed video bytes in a reply at all — the model cannot override this |
| `MCP_VIDEO_INLINE_MAX_MB` | backend | 12 | biggest video it may embed, once allowed |
| `MCP_VIDEO_DELIVERY` | backend | `auto` | default for `delivery` (`auto`/`url`, plus `inline` when allowed) |
| `MCP_VIDEO_HEIGHT` | worker | 540 | ceiling a `watch_video` re-encode aims at (never up-scales) |
| `MCP_VIDEO_FPS_CAP` | worker | 30 | frame-rate cap on a re-encode |
| `MCP_VIDEO_MAX_ENCODE_S` | worker | 1800 | longest window one call will re-encode |
| `MCP_VIDEO_URL_MAX_MB` | worker | 512 | above this even a link gets a shrunk copy instead |
| `MCP_VIDEO_DOWNLOAD_MAX_MB` | worker | 2048 | biggest file that may be pulled onto the box to shrink |
| `WORKER_MCP_SLOTS` | worker | 2 | concurrent MCP tool calls |
| `WORKER_MCP_POLL_INTERVAL_S` | worker | 0.25 | queue poll for the MCP lane |
| `WORKER_MCP_SESSION_TTL_S` | worker | 1800 | how long a project's cached context lives |
| `WORKER_MCP_MAX_SESSIONS` | worker | 3 | projects holding a cached context |
