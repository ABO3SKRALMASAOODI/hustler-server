# Valmera AEO — off-site submission pack

Ready-to-paste copy for every placement that needs your account. Written
2026-07-27.

**Why this document exists.** Eight unbranded searches for "agentic video
editor" and its variants returned Valmera zero times — and so did a *branded*
search for "Valmera agentic AI video editor", which returned a competitor
instead. The site is not the problem: 92 URLs, valid schema, 4,326
crawler-visible characters on the homepage. The problem is that the search
index still holds the **retired app-builder** document for valmera.io
("Valmera – Build Any App With AI. Just Describe It."), and nothing off-domain
says otherwise.

The single cheapest lesson from the SERP recon: **Cardboard got two separate
placements — a YC profile and an AlternativeTo entry — out of one eight-word
sentence someone typed into a form.** Nothing on that page was earned by
quality. It was earned by existing in text. That is what this document is for.

Work top to bottom. Section 0 is the part that blocks everything else.

---

## 0. Do these first (they gate everything below)

**a) Flip the MCP allowlist.** `/mcp` and `/mcp/tools` are written as a live,
public capability, on your instruction. Right now `MCP_ALLOWED_EMAILS` defaults
to `thevalmera@gmail.com` alone, so anyone who follows the page reaches a login
that will never say yes. Before these pages get indexed, either widen the
allowlist or replace it with a real access rule. Decide two things while you are
there:

- Who may connect — any account, or subscribers only? The page currently says
  "a Valmera account", which is true only if you do not gate it to paid.
- Who pays for it. MCP turns bill nobody today, but vision, image and stock
  calls still cost real money under the MCP job id. That is fine at one user and
  is not fine in public.

**b) Google Search Console.** The stale index is the whole ballgame. In GSC:
resubmit `https://valmera.io/sitemap.xml`, then use **URL Inspection → Request
Indexing** on these, in this order:

```
https://valmera.io/
https://valmera.io/agentic-video-editor
https://valmera.io/mcp
https://valmera.io/mcp/claude
https://valmera.io/claude-video-editor
https://valmera.io/alternatives/best-agentic-video-editors
https://valmera.io/mcp/tools
https://valmera.io/autonomous-video-editor
https://valmera.io/agentic-video-editing-tools
https://valmera.io/edit-video-by-chatting-with-ai
https://valmera.io/chatgpt-video-editor
https://valmera.io/tools/auto-edit-podcast-video
https://valmera.io/ai-video-editor
```

Request Indexing is rate-limited to a handful per day — that is why the list is
ordered rather than alphabetical. The homepage is first because it is the one
carrying the wrong title. Spread the rest over several days, top down.

**Query → page map**, so you can check coverage as results come in:

| Query the agents ran | Page that should answer it |
|---|---|
| `agentic video editor` | `/agentic-video-editor` |
| `best agentic video editor` | `/alternatives/best-agentic-video-editors` |
| `agentic video editing AI tools 2026` | `/agentic-video-editing-tools` |
| `video editing MCP server Claude edit video from chat` | `/mcp`, `/mcp/claude` |
| `AI video editor MCP server connect to Claude` | `/mcp/claude` |
| `edit video by chatting with AI agent no timeline 2026` | `/edit-video-by-chatting-with-ai` |
| `AI tool remove silences filler words add captions automatically podcast video` | `/tools/auto-edit-podcast-video` |
| `ai video editor` | `/ai-video-editor` |
| `autonomous video editor` | `/autonomous-video-editor` |
| `claude video editor` | `/claude-video-editor` |
| `can chatgpt edit videos` | `/chatgpt-video-editor` |

**c) Bing Webmaster Tools.** Import from GSC (one click), then submit the same
sitemap. Bing feeds Copilot and the wider ChatGPT-search ecosystem, and it
re-crawls far more willingly than Google does.

**d) IndexNow.** Already wired — key `3aea404a90ab83fae6dc016780dd5a76` is live
at `/3aea404a90ab83fae6dc016780dd5a76.txt`. Resubmit the full sitemap after
deploy (the command is in the deploy notes).

---

## 1. The canonical one-liner

Use this string **verbatim**, everywhere, without rewording it per site. The
head term is won by putting the exact phrase in a structural slot — a product
name, a title tag, a directory description — not by writing well around it.

> **Valmera is an agentic video editor: upload real footage, describe the edit
> in plain English, and an AI agent performs it — cuts, captions, music, motion
> and colour — then exports a full-quality video.**

Short forms, for fields with hard limits:

| Limit | Copy |
|---|---|
| ~50 chars | `Agentic video editor — describe the edit` |
| ~60 chars | `Valmera — the agentic AI video editor` |
| ~100 chars | `Agentic video editor. Describe the edit in plain English and an AI agent performs it.` |
| ~160 chars | `Valmera is an agentic video editor. Upload footage, describe the edit in plain English, and an AI agent cuts, captions, mixes and grades it into a finished video.` |

**Tagline:** `Edit any video. Just describe it.`

**Category words to use, in this order of preference:** agentic video editor →
agentic AI video editor → AI video editor. Do not lead with "AI video editor"
anywhere; that term is saturated and you do not win it. You can win the first.

---

## 2. MCP directories — highest leverage, all free

This is the bucket where the thing doing the searching *is* the thing that would
use the product, and Valmera is absent from every one of them. Competitors here
(reap, OpenClip, Video Jungle, Palmier, Statonic) are mostly *clippers*; your
88-tool registry is genuinely deeper than anything listed. Do these first.

**Shared fields for all MCP listings:**

- **Name:** `Valmera`
- **Server name (slug):** `valmera`
- **Endpoint:** `https://entrepreneur-bot-backend.onrender.com/mcp`
- **Transport:** Streamable HTTP
- **Auth:** OAuth 2.1 (dynamic client registration + PKCE), or bearer token
- **Homepage:** `https://valmera.io/mcp`
- **Docs:** `https://valmera.io/mcp/tools`
- **Categories/tags:** `video`, `video-editing`, `media`, `content-creation`,
  `ffmpeg`, `transcription`, `captions`, `oauth`

**Description (use this everywhere in this section):**

> Valmera is an agentic video editor exposed over MCP. Connect it and your model
> edits real footage from the conversation: upload a video, cut silences and
> filler words against a word-level transcript, add word-accurate captions, mix
> music that ducks under speech, apply speed spans, zooms, colour grades and
> transitions, reframe to 9:16, erase burned-in captions or objects by
> repainting the pixels, and render a verified preview. Final export remains
> an explicit action in Valmera Studio.
>
> It serves the same editing registry Valmera's own agent uses plus a small set
> of session tools, with final export explicitly filtered at the MCP boundary.
> Slow operations (renders and pixel repainting) return a job id and a
> `wait_for_job` tool rather than a fabricated completion, and a tool whose
> backing service is unconfigured is hidden from the registry rather than
> failing on call. Nothing modifies your original upload: tools mutate an edit
> decision list and the renderer produces video from it.

**Claude Code install snippet** (most directories want one):

```bash
claude mcp add --transport http valmera \
  https://entrepreneur-bot-backend.onrender.com/mcp
```

**Claude app setup line:**

> Settings → Connectors → Add custom connector → paste
> `https://entrepreneur-bot-backend.onrender.com/mcp`, sign in, Allow.

### Where to submit

| # | Directory | How | Notes |
|---|---|---|---|
| 1 | **Glama** — glama.ai/mcp/servers | Submit form | Ranks in the searches I ran. Highest priority. |
| 2 | **PulseMCP** — pulsemcp.com | Submit form | Well-crawled, fast to appear. |
| 3 | **mcp.so** | Submit form | High-volume aggregator. |
| 4 | **Awesome MCP Servers** — github.com/punkpeye/awesome-mcp-servers | Pull request | The GitHub repo behind mcpservers.org. A PR adding one line under the media/video section. This one feeds many others. |
| 5 | **mcpservers.org** | Follows from #4 | Verify it picked you up. |
| 6 | **Smithery** — smithery.ai | Submit / connect repo | |
| 7 | **claudemarketplaces.com** | Submit form | Surfaced in my search results for the exact MCP query. |
| 8 | **Anthropic MCP connectors directory** | Via Anthropic's submission path | Slowest, most valuable. Do it once the allowlist is open. |

---

## 3. Software directories — the Cardboard lesson

**Priority order.** AlternativeTo first: a competitor surfaced in my searches
purely off an AlternativeTo alternatives page, and the submission bar is a
random user typing a sentence.

### 3a. AlternativeTo — alternativeto.net

Submit Valmera as an alternative to **each** of these separately (each one is
its own retrieval surface): **Descript, CapCut, Opus Clip, Veed, Kapwing,
Submagic, Clipchamp, Mosaic.**

- **Name:** Valmera
- **Tagline:** Edit any video. Just describe it.
- **Description:** the canonical one-liner, then this paragraph:

> Valmera indexes your footage once — word-level transcript, silences, shot
> boundaries and vision descriptions — and from then on you describe outcomes
> instead of operating a timeline. The agent has around 88 editing tools, renders
> a preview, and looks at the frames it produced before reporting back. It can
> also be driven from Claude over MCP. Free plan with 50 credits and no card;
> Creator $30/mo, Pro $50/mo, Frontier $100/mo.

- **License:** Commercial / Freemium
- **Platforms:** Online, Web-based, Self-Hosted = No
- **Tags:** `video-editing`, `ai`, `agentic`, `captions`, `subtitles`,
  `transcription`, `video-editor`, `mcp`

### 3b. The rest

| Directory | Cost | Notes |
|---|---|---|
| **Product Hunt** | Free | Launch on a Tuesday–Thursday. Tagline above; first comment should be the honest-limits list — it reads as credible and gets quoted. |
| **There's An AI For That** | Paid (~$347) | Heavily scraped by AI-search. Worth it once the MCP allowlist is open. |
| **Futurepedia** | Free tier | |
| **G2** | Free listing | Effectively a prerequisite for being named in "best tool" answers. Slow to approve — start now. |
| **Capterra** | Free listing | Same. |
| **SaaSHub** | Free | |
| **Slant** | Free | Answer "What are the best AI video editors?" as a contributor. |
| **Indie Hackers** | Free | **You already have an account** (`indiehackers.com/valmera`) — it ranks for "valmera.io" today. Put the one-liner in the product description; it is currently the only off-domain page that ranks for your brand. |

---

## 4. Earned mentions — the durable version

Owned pages and directory rows get you into the index. Third-party inclusion is
what actually got a competitor recommended in my searches: one Buffer roundup
naming Vyra alongside Descript beat every owned page in the category.

**Pitch targets** (roundups that already rank for the queries you want):

- Buffer's AI video editor roundup
- Zapier's "best AI video editor" post
- Wireflow, Cutback, ExplainX — all three write MCP/agentic explainers and
  actively name tools

**Pitch angle — lead with the differentiator, not the product.** The line that
earns a mention is the one no competitor can copy:

> Valmera is the only agentic video editor that publishes its *complete* editing
> registry over MCP — the same ~88 tools its own agent uses, not a clipping
> subset — so Claude can perform a full edit: cut against a word-level
> transcript, caption, mix, grade, reframe, erase burned-in text by repainting
> pixels, and export. Happy to give you an account to test it.

**Reddit** (long game, founder account, answer-don't-promote): r/VideoEditing,
r/NewTubers, r/podcasting, r/ClaudeAI (the MCP angle is genuinely on-topic
there and the audience is exactly right).

**YouTube** is the strongest single correlate with AI-assistant visibility:
tutorials made *with* Valmera, showing the real workflow. One good screen
recording of the Claude-connector flow would be the highest-value asset you
could produce this month, and you can record it with `record_website_demo`.

---

## 5. What NOT to do

- **Do not fabricate benchmarks.** "Ranked #1 in a 2026 benchmark of nine tools"
  gets quoted precisely because it is specific — and inventing one is the fastest
  way to lose the honesty position that makes the rest of the site credible.
- **Do not quote competitor pricing you have not verified.** CapCut publishes no
  public pricing page; Veed's is JS-hidden. The site already had to strip
  invented numbers once.
- **Do not enable AI-bot blocking** in Vercel's firewall rulesets. They are
  inactive by default. Leave them that way.
- **Do not add named bot groups to robots.txt.** It would silently drop the
  `Disallow` protections for those bots. The wildcard-permissive file is correct.
- **Do not claim Pro runs a stronger model than Creator.** It does not —
  `PAID_PLANS` ships empty and Frontier is the only tier that changes the model.
  (This was live in the homepage JSON-LD until 2026-07-27.)

---

## 6. How to measure

Re-run these exact queries in **two to three weeks** — not sooner; indexing is
the bottleneck and no amount of rephrasing tests your way into the corpus:

```
agentic video editor
best agentic video editor
video editing MCP server
edit video from Claude
Valmera
```

The first real win to look for is not a ranking. It is the **branded** query
returning valmera.io with a video-editing title instead of the app-builder one.
Until that flips, nothing else can work.
