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
worker publishes its live registry on boot and the backend serves it verbatim,
so every tool the in-house agent has, your model has: same names, same JSON
schemas, same descriptions, same honest-off gating (a tool whose backing
service has no key is hidden from both). Execution happens in the worker, in
the same `ToolContext` an agent turn uses. There is nothing to keep in sync
because there is no copy — `worker/tests/test_mcp_surface.py` fails if one
ever appears.

On top of that, eleven **session tools** the studio UI normally covers and a
headless model cannot: `list_projects`, `open_project`, `create_project`,
`project_state`, `upload_start`, `upload_finish`, `index_status`,
`export_final`, `wait_for_job`, `download_url`, `watch_video`.

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
catalog`. Verified live on 2026-07-27 serving **87 tools** (10 session + 77
editor).

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
  →  cut_silences() / add_captions() / add_music() / ...
  →  render_preview()  →  download_url()
  →  export_final()    →  wait_for_job(N)  →  download_url(kind="final")
```

`open_project` is the one thing to remember: editing tools do not take a
project id, they act on the connection's active project. That pointer is stored
server-side (on the OAuth grant, or on the static token), so it survives a
reconnect, a token refresh and a client restart.

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
watch_video()                                  the current edit, whole
watch_video(kind="source")                     the raw uploaded footage
watch_video(start=12, end=30)                  just that window of the program
watch_video(delivery="inline", max_mb=8)       shrunk until it fits in the reply
watch_video(kind="asset", asset_key="clips/…") one uploaded clip
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

**How it comes back: A LINK, unless you explicitly ask otherwise.** The reply
is a text block — what it is, how long, and **which clock its seconds are on**
— carrying a plain unauthenticated URL to an ordinary MP4. Fetch it and watch
it. `delivery="inline"` additionally embeds the file as an MCP `resource`
block (`mimeType: video/mp4`, base64), capped at `MCP_VIDEO_INLINE_MAX_MB`.

**Only ask for `inline` if your client decodes video content blocks
natively.** This defaulted to embedding-when-it-fits for about half an hour on
2026-08-03, on the assumption that a client which cannot render a video block
would ignore it. Grok **stringified** it: a 2.9 MB preview arrived as **4
million characters** of base64 and ended the session ("the conversation is too
long"), while the tool call reported success. Being wrong that way costs the
whole conversation; being wrong the other way costs one extra argument. So
embedding is opt-in, enforced independently in the worker (which decides) and
the backend (which carries the bytes) — neither alone can put a file in a
reply. `MCP_VIDEO_DELIVERY=inline` flips the default for a deployment whose
client is known to handle it.

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
| `MCP_VIDEO_INLINE_MAX_MB` | backend | 12 | biggest video `watch_video` may embed in a reply |
| `MCP_VIDEO_DELIVERY` | backend | `auto` | default for `delivery` (`auto`/`inline`/`url`) |
| `MCP_VIDEO_HEIGHT` | worker | 540 | ceiling a `watch_video` re-encode aims at (never up-scales) |
| `MCP_VIDEO_FPS_CAP` | worker | 30 | frame-rate cap on a re-encode |
| `MCP_VIDEO_MAX_ENCODE_S` | worker | 1800 | longest window one call will re-encode |
| `MCP_VIDEO_URL_MAX_MB` | worker | 512 | above this even a link gets a shrunk copy instead |
| `MCP_VIDEO_DOWNLOAD_MAX_MB` | worker | 2048 | biggest file that may be pulled onto the box to shrink |
| `WORKER_MCP_SLOTS` | worker | 2 | concurrent MCP tool calls |
| `WORKER_MCP_POLL_INTERVAL_S` | worker | 0.25 | queue poll for the MCP lane |
| `WORKER_MCP_SESSION_TTL_S` | worker | 1800 | how long a project's cached context lives |
| `WORKER_MCP_MAX_SESSIONS` | worker | 3 | projects holding a cached context |
