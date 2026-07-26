# Valmera over MCP — edit video from your own Claude session

**Status: private.** No UI, no marketing, no signup path. Reachable only with a
bearer token that only the admin account can mint, and every request re-checks
the holder's email against `MCP_ALLOWED_EMAILS` (default: `thevalmera@gmail.com`
alone). Nobody else can see it exists.

## What it is

An MCP endpoint at `POST /mcp` that hands **the complete Valmera editor tool
registry** to whatever model you are running in Claude Code — Opus, Fable,
whatever comes next — on your Anthropic subscription. Your model does the
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

On top of that, ten **session tools** the studio UI normally covers and a
headless model cannot: `list_projects`, `open_project`, `create_project`,
`project_state`, `upload_start`, `upload_finish`, `index_status`,
`export_final`, `wait_for_job`, `download_url`.

## Turning it on (once)

**1. Apply the migration.** Render shell on the backend service:

```bash
psql $DATABASE_URL -f backend/migrations/008_mcp.sql
```

It relaxes `video_jobs.type`'s CHECK constraint to accept `mcp_tool`, and adds
`mcp_tokens` + `mcp_catalog`. Until it runs, every MCP call fails at the INSERT.

**2. Deploy** backend + worker (normal push to `main`). The worker publishes
its tool catalog on boot — check the log for `published MCP tool catalog`. If
it says it could not, step 1 has not run.

**3. Mint a token** with your admin JWT (copy it out of the studio's
localStorage):

```bash
curl -sS -X POST https://entrepreneur-bot-backend.onrender.com/mcp/tokens \
  -H "Authorization: Bearer $VALMERA_JWT" \
  -H 'Content-Type: application/json' -d '{"label":"claude-code"}'
```

The response contains the token **once** (only its sha256 is stored) and the
exact `claude mcp add` line to paste.

**4. Connect Claude Code:**

```bash
claude mcp add --transport http valmera \
  https://entrepreneur-bot-backend.onrender.com/mcp \
  --header "Authorization: Bearer vlm_mcp_..."
```

Then `/mcp` inside Claude Code should list `valmera` as connected. Ask it to
"list my Valmera projects".

## Using it

```
list_projects  →  open_project(3)  →  (the whole project state comes back)
  →  cut_silences() / add_captions() / add_music() / ...
  →  render_preview()  →  download_url()
  →  export_final()    →  wait_for_job(N)  →  download_url(kind="final")
```

`open_project` is the one thing to remember: editing tools do not take a
project id, they act on the token's active project. That pointer lives on the
token row, so it survives a Claude Code restart.

**Uploading a local file.** MCP arguments are JSON, so bytes never travel over
the protocol. `upload_start` returns a presigned URL and the exact `curl` to
run; under 64 MB the model does it itself in one command. Bigger files are
multipart (one ETag per part) — run the helper instead:

```bash
export VALMERA_MCP_TOKEN=vlm_mcp_...
python3 scripts/valmera_upload.py ~/Movies/talk.mp4 --project 3
```

A main video then has to be **analyzed** (transcript, shots, silences) before
it can be edited — minutes on a long one. `index_status` reports it.

**Watching it happen.** Every MCP tool call writes an `activity` row into the
project's chat, tagged `source: "mcp"`. Open the project in the studio and you
see your Claude session edit in real time, with the preview updating.

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
  before the surface is ever sold**, not after.
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

```bash
curl -sS -X DELETE .../mcp/tokens/1 -H "Authorization: Bearer $VALMERA_JWT"
```

or clear `MCP_ALLOWED_EMAILS` to shut the whole surface off without a deploy.

## Env

| Var | Where | Default | What |
|---|---|---|---|
| `MCP_ALLOWED_EMAILS` | backend | admin email | who may hold a token |
| `MCP_SYNC_WAIT_S` | backend | 25 | longest a call blocks before ticketing |
| `MCP_INSTRUCTIONS` | backend | `full` | `brief` drops the doctrine |
| `WORKER_MCP_SLOTS` | worker | 2 | concurrent MCP tool calls |
| `WORKER_MCP_POLL_INTERVAL_S` | worker | 0.25 | queue poll for the MCP lane |
| `WORKER_MCP_SESSION_TTL_S` | worker | 1800 | how long a project's cached context lives |
| `WORKER_MCP_MAX_SESSIONS` | worker | 3 | projects holding a cached context |
