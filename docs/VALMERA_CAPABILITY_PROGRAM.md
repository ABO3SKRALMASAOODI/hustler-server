# Valmera Capability Program — engineering specification

**For:** the coding agent implementing this.
**Written:** 26 July 2026, against commit `af4b3a2`.
**Purpose:** close the capability gaps that let competitors beat us, and add the
capabilities none of them have. This is a build spec, not a strategy memo — the
competitive reasoning is compressed to one paragraph per workstream and the rest
is contracts.

---

## 0. Read this section before writing any code

Valmera has house rules that are not optional. Work that violates them will look
correct, pass review, and then break production in ways that took us 40+ rounds
to learn. Every workstream below assumes you follow all nine.

### 0.1 The nine invariants

**I1 — The agent never touches pixels.** Tools mutate an EDL. `worker/renderer.py`
turns an EDL into an FFmpeg filtergraph. If a feature needs new pixels, it needs a
new *renderer* capability plus a new *EDL field*, in that order. Never shell out to
ffmpeg from a tool to produce final-program pixels. (Tools may produce *assets* —
`add_corrupt_screen`, `_color_card_asset`, `inpaint.py` — but those become project
assets referenced by the EDL, not baked program output.)

**I2 — Every EDL write goes through `ToolContext.write_edl(new_edl_dict, change_desc)`**
(`worker/agent_tools.py:175`). It validates, detects no-op writes, appends a new
version, returns a human/model-readable diff line, and runs loop detection. It
returns one of three string shapes and the agent is trained on all three:
- `EDL v12 -> v13: <desc>. Before: … After: …` — success
- `NO CHANGE — the EDL is identical to v12; …` — no version created
- `REJECTED (EDL v12 unchanged): <validation message>` — nothing happened

Never insert an EDL row directly. Never mutate `prev["json"]` in place — deep-copy.

**I3 — New EDL fields must default to empty/None.** `edl_signature()`
(`worker/schemas.py:1587`) drops keys whose value is `None`, `[]` or `{}`. That is
the *only* reason every historical EDL still compares equal to a fresh dump and
every cached render is still served. A new field with a non-empty default
invalidates the render cache for every project on the platform and re-encodes them
on a 1-vCPU box. This has starved real customers before. New field ⇒ empty default,
no exceptions.

**I4 — Do not bump `PIPELINE_VERSION` for derived analysis.** `PIPELINE_VERSION`
(currently `7`, `worker/schemas.py:24`) forces a full re-index of every video ever
uploaded. Bump it only when the *core* index (words, sentences, silences, shots,
video info) changes shape or content. For anything new and derived — diarization,
subject tracks, highlight scores — follow the **sidecar pattern** already
established by `worker/perception.py`: `get_or_compute_for_index(worker_db, dbx,
index_row, media_path)` computes on first use, caches by content hash, and costs
nothing for projects that never ask. Every new analysis in this document is a
sidecar.

**I5 — A capability whose backing service is unconfigured must be invisible.**
Three places, all of them:
1. `_tool_disabled(name)` in `worker/agent_tools.py:6689` — hides the tool from
   `openai_tools()` and from `capabilities_digest()`.
2. A claim-pair list in `worker/agent_prompt.py` (`_LIBRARY_CLAIMS`, `_SFX_CLAIMS`,
   `_URL_FETCH_CLAIMS`, `_WEB_RECORD_CLAIMS`, `_STOCK_CLAIMS`) — the prose that
   *describes* the capability is deleted or replaced in `system_prompt()`.
3. Cross-references in *other* tools' descriptions get patched in `openai_tools()`
   (see the `sfx_library.CATALOG` branch).

Write the prompt paragraph self-contained (own line, or an own comma-bounded
phrase) so the replacement can never collide with a neighbouring pair. A tool that
returns "sorry, unavailable" is a bug: the model should never have seen it.

**I6 — Every new claim the model can make needs a fence.** `worker/agent_loop.py`
`EDIT_CLAIM` / `RENDER_CLAIM` / `DENY_CLAIM` (lines 425–508) are regex fences over
the model's *draft reply*; `_reply_violations()` compares them against what tools
actually did this turn, and `_enforce_honesty()` regenerates with the offending
words quoted. If you add a tool whose success is describable in a new verb
("translated", "tracked", "exported to Premiere", "removed the background"),
**add that verb to `EDIT_CLAIM`** and add a test to
`worker/tests/test_units.py`. Respect the existing structure: stative/perfect
constructions only, so honest offers ("I can translate it") don't trip; and
`_negated_claim` must keep working ("No background was removed").

**I7 — Tool results are the only source of truth for a reply.** A tool must return
what it *measured*, not what it *attempted*. `erase_burned_text` reporting ink
before/after and saying `STILL VISIBLE` is the reference implementation. Every new
tool returns concrete numbers the model can quote and cannot invent.

**I8 — Refuse honestly instead of faking.** `beat_align_cuts` refuses below 0.5 bpm
confidence rather than "syncing" to noise. Every new measurement-driven tool needs
its own confidence floor and its own refusal sentence.

**I9 — Non-LLM spend goes on `ctx.gen_extra_cost_usd`** (real USD) so the in-turn
budget cap (`ctx.running_credits()` / `ctx.over_budget()`) and the final charge
(`worker/db.charge_turn_credits`) agree. Anything that costs money per call —
a translation API, a matting API — must add its cost there.

### 0.2 How to register a tool (the exact shape)

In `worker/agent_tools.py`:

```python
def my_tool(ctx, required_arg, optional_arg=None):
    """Returns a STRING. Prefix rejections with 'REJECTED:'."""
    ...
    return ctx.write_edl(new_edl, "what changed, in one clause")

# in TOOLS (dict: name -> (fn, description, properties))
"my_tool": (my_tool,
            "One-sentence what-it-does first (capabilities_digest() takes "
            "everything up to the first '. '), then the mechanics, then the "
            "honest limits, then when NOT to use it.",
            {"required_arg": {"type": "string"},
             "optional_arg": {"type": "number"}}),

# then, as applicable:
REQUIRED_ARGS["my_tool"] = ["required_arg"]
WRITE_TOOLS.add("my_tool")           # if it writes an EDL version
# _tool_disabled() branch            # if it needs an external service
```

Then add the prompt paragraph in `worker/agent_prompt.py::SYSTEM_PROMPT` **and**
the capability phrase in the long "Never tell the user something is impossible"
bullet (line 67) — the model checks that list before refusing.

### 0.3 Test expectations

`worker/tests/` currently holds 7,880 lines. New work is expected to bring:
- unit tests for the schema (valid/invalid/boundary) in `test_edl_v2.py` style,
- **golden filtergraph tests** — for any renderer change, assert that an EDL
  *without* the new field produces a byte-identical graph to before
  (`timeline_golden.json` is the pattern),
- a live-ffmpeg test for anything that produces pixels,
- an honesty test for every new `EDIT_CLAIM` verb.

---

## 1. Baseline (what exists today)

- **80 agent tools**, ~56 write. Registry in `worker/agent_tools.py::TOOLS`.
- **EDL v2** (`worker/schemas.py::EDL`): `keep`, `canvas`, `captions`, `music`,
  `sfx`, `volume`, `frame`, `inserts`, `voiceover`, `effects`, `overlays`,
  `texts`, `speed`, `master`, `caption_mutes`, `source_clean`.
- **One main video** per project (`keep` is source-time on a single file), plus a
  canvas mode for clip/image-only programs.
- **Index**: Deepgram nova-3 words → sentences, silences, PySceneDetect shots,
  thumbnails, contact sheets, vision captions. Sidecar: `perception.py`
  (tempo/beat grid/energy/word stress).
- **Renderer**: `build_filtergraph()` builds per-keep-span *blocks*, applies
  duration-preserving junction effects per block, then `concat`s them.
- **Model**: DeepSeek V4 Pro (`AGENT_MODEL`), vision on a *separate* provider
  (`VISION_BASE_URL`/`VISION_MODEL`/`VISION_API_KEY`), image gen on a third
  (`IMAGE_BASE_URL`/`IMAGE_API_KEY`).

### What we do not have (verified by grep, 26 Jul 2026)

No highlight/clip finding · no NLE export (FCPXML/XML/OTIO/EDL) · no true
crossfade · no masks, matting, background removal, or motion tracking · no
multi-track/multi-source video · no speaker diarization or multicam · no
translation or dubbing · no saved formats or batch runs · keyframing limited to
specific `AnimFloat` fields.

---

## 2. P0 — clear these before any feature work

### P0.1 Vision is the substrate for half of this document. Verify it is alive.

`worker/llm.py` has `_BLIND_MARKERS` + `vision_available()`: when the provider
400s with `unknown variant image_url` the whole vision surface self-disables
honestly. That is correct behaviour, but it means `look_at`, `look_at_asset`,
the render self-check, `auto_reframe`'s subject measurement, contact-sheet
captions and stock-clip picking are all **silently degraded** whenever
`VISION_API_KEY` is unset or points at a text-only model.

**Task:** add a startup assertion + an admin surface.
- On worker boot, if `config.VISION_MODEL` is set, make one 1-pixel probe call
  and log the result. Record it to `llm_calls` with `purpose='vision_probe'`.
- Add a row to the admin health view: `vision: live | blind (<provider error>)`.
- Acceptance: with `VISION_BASE_URL=https://api.x.ai/v1` + a valid xAI key, the
  probe returns text and `vision_available()` is `True`.

Nothing below that says "vision" ships until this is green.

### P0.2 Capacity — the ceiling that makes every feature moot

`INDEX_SLOTS=1`, `MEDIA_SLOTS=1`, ~1 vCPU. A 19-min upload = ~16 min index,
~14 min per preview, serialized platform-wide. The proxy encode is ~88% of index
time.

**Tasks, in order:**
1. Move the media/index roles onto the request-based executor
   (`worker/DEPLOY_EXECUTOR.md`, `worker/remote.py`, `WORKER_ROLE`,
   `REMOTE_EXECUTOR_URL`) — the code already exists and is flag-gated. Deploy it.
2. Once dispatched remotely, raise `WORKER_INDEX_SLOTS` and `WORKER_MEDIA_SLOTS`
   independently of the agent box.
3. Proxy encode: evaluate `-c:v h264_nvenc` / `h264_videotoolbox` on the executor
   image, and lower `PROXY_HEIGHT` to 480 for sources above 1080p. Measure before
   and after on the 19.3-min reference file; record both numbers in the PR.

**Acceptance:** two users uploading 20-minute videos simultaneously both reach
`indexed` within 6 minutes.

---

## 3. Workstream W1 — Highlight finding (`find_highlights`, `make_clips`)

**Why.** This is the single largest job in creator video and we do not bid on it.
Opus Clip's entire product is "upload a podcast, get 5–15 ranked vertical clips
in 2–5 minutes," at a $15/mo floor. We already compute every input it needs.

**Why we can beat it.** Opus Clip is a one-shot pipeline: it hands you clips and
you take them or leave them. Ours produces *ranked candidates with reasons the
user can argue with, inside a conversation that can then edit any of them*. "Clip
3 but start it on the question, and cut the tangent at 40s" is a sentence Opus
Clip cannot accept.

### 3.1 New sidecar analysis — `worker/highlights.py`

Follow the `perception.py` pattern exactly: `get_or_compute_for_index(worker_db,
dbx, index_row, media_path)`, cached by index sha, computed on first call.

Score every candidate window with **measured, non-LLM signals first** (cheap,
deterministic, explainable):

| Signal | Source | Notes |
|---|---|---|
| Speech density | `index["words"]` | words/sec inside the window |
| Energy rise | `perception.analyze_audio` | dB rise over the window's first 25% |
| Word stress peaks | `perception.word_stress` | count of top-decile stressed words |
| Question→answer shape | `index["sentences"]` | `?` terminal followed by ≥8s of speech |
| Self-contained opening | `sentences` | window starts at a sentence boundary and does not open with a pronoun/conjunction referring backwards |
| Shot variety | `index["shots"]` | shot changes per 10s |
| Silence penalty | `index["silences"]` | fraction of window that is dead air |
| Laugh/reaction | `perception` energy transient with no matching word | weak signal, low weight |

Windows: sliding 15/30/60/90s, snapped to sentence boundaries, stride 5s,
non-maximum suppression at 50% overlap.

**Then, and only then, one LLM pass** over the top ~25 candidates' transcripts to
produce, per candidate: a `hook` (the opening line verbatim), a `title`, a
`reason` (why it works), and a `virality` 0–1. Model call goes through
`llm.ask_text` with `purpose='highlights'` so it lands in the admin inspector.

**Honesty rule:** the returned `score` must be decomposed. The tool result shows
the sub-scores, so the agent quotes measurements, not vibes.

### 3.2 Tools

```python
"find_highlights": (find_highlights,
    "Find the strongest self-contained moments in the video and rank them — the "
    "tool for 'what are the best parts', 'make clips from this podcast', 'find "
    "something viral in here'. Returns CANDIDATES ONLY with start/end in SOURCE "
    "seconds, the opening line verbatim, a suggested title, why it scores, and a "
    "measured breakdown (speech density, energy rise, stressed words, silence). "
    "It changes nothing — build a clip from one with make_clip. count 1-20 "
    "(default 8); target_s is the length you want (15/30/60/90, default 45); "
    "min_s/max_s bound it. Every boundary is snapped to a sentence edge.",
    {"count": {"type": "integer"}, "target_s": {"type": "number"},
     "min_s": {"type": "number"}, "max_s": {"type": "number"},
     "query": {"type": "string"}}),
```

`query` is the differentiator: `find_highlights(query="anything where he talks
about pricing")` filters candidates by transcript relevance before ranking. That
is ClipAnything's natural-language targeting, in a conversation.

```python
"make_clip": (make_clip,
    "Turn ONE highlight into the edit — replaces the keep list with just that "
    "window, snapped to word edges. Pass the id from find_highlights, or an "
    "explicit start/end. This REPLACES the current edit; the user's other work "
    "on the timeline is not preserved, so say so before calling it on a project "
    "that already has an edit. Follow with auto_reframe('9:16') and a caption "
    "preset for a social cut.",
    {"id": {"type": "string"}, "start": {"type": "number"},
     "end": {"type": "number"}}),
```

**Multi-clip note.** Producing N clips as N *projects* is a backend concern, not
an agent tool. Add `POST /projects/:id/clips` (backend) that forks the project
into N children, each with a `keep` of one highlight, each inheriting the index by
sha (so it is a **cache hit — no re-index**). The agent gets a read-only
`list_clips` and tells the user they appear in the project list. This is the one
place where copying Opus Clip's batch model is worth it, and it costs us almost
nothing because indexes are content-addressed.

### 3.3 Acceptance

- On a 20-minute two-person podcast, `find_highlights(count=8)` returns 8
  non-overlapping windows, every boundary on a sentence edge, in < 20s after the
  sidecar is warm.
- Every candidate's `reason` cites at least one measured number.
- `make_clip` followed by `auto_reframe("9:16")` + `add_captions("from_transcript")`
  + `set_caption_style(preset="podcast")` produces a renderable vertical clip.

---

## 4. Workstream W2 — NLE export (FCPXML / OTIO / EDL / SRT)

**Why.** Eddie AI and Mosaic both export to Premiere/Resolve/FCP; OpenChatCut
gives FCPXML away free. Without it, no professional can use us for anything but a
throwaway, and the highest-credibility workflow in agentic editing — *AI does the
rough cut, a human finishes it* — is closed to us. It also defuses lock-in as a
sales objection.

**Design.** This is a pure function of the EDL plus the asset table. No renderer
involvement, no new pixels, no credits.

### 4.1 New module — `worker/export_nle.py` (or `backend/` — see below)

```python
def to_fcpxml(edl, index, assets, project) -> str   # FCPXML 1.11
def to_otio(edl, index, assets, project) -> str     # OpenTimelineIO JSON
def to_cmx3600(edl, index, assets) -> str           # classic EDL, cuts only
def to_srt(edl, index) -> str                       # captions, program time
```

Put it in `worker/` and import it from the backend the way `worker/timeline.py`
is already shared — the timeline math (`Timeline`, `remap_program_items`,
`speed_pieces`) **must** be the same code, or exported cut points will disagree
with our own renders.

**Mapping:**

| EDL | FCPXML / OTIO |
|---|---|
| `keep[i]` | one `asset-clip` / Clip with `start` = source in, `duration` = sped length |
| `speed[]` | `timeMap` / OTIO `LinearTimeWarp` |
| `inserts[]` | clips on the same lane, spliced at their program position |
| `overlays[]` | clips on lane 2 with transform (position/scale from x/y/scale) |
| `texts[]` | `title` elements; effect ref = a text generator |
| `captions` | separate `.srt` sidecar **and** optional burned reference |
| `music`/`sfx`/`voiceover` | audio lanes -1, -2, -3 with `adjust-volume` from gain_db |
| `effects.grade`, `stylize` | **not exported** — emit a comment/marker naming the look |
| `source_clean` | export must reference the CLEANED file if one exists, and say so |

**Honesty requirements (this is where an export loses trust):**
- The result must state, in the tool output and in the user-facing reply, exactly
  what did **not** survive the round-trip: grades, stylize, corrupt screens, our
  caption styling, watermark/end card.
- If `source_clean` is set, the XML must point at the cleaned media *and* the
  reply must say the project references a Valmera-generated cleaned file that the
  user needs to download too.

### 4.2 Surfaces

Backend: `GET /projects/:id/export/{fcpxml|otio|edl|srt}` → presigned R2 object.
Agent tool:

```python
"export_project_file": (export_project_file,
    "Produce a project file the user can open in Final Cut Pro, DaVinci Resolve "
    "or Premiere (format 'fcpxml' | 'otio' | 'edl' | 'srt') so they can finish "
    "the edit themselves. Cuts, speed changes, inserts, overlays, text and audio "
    "levels carry over; color grades, stylize effects and Valmera's caption "
    "styling do NOT — the tool lists exactly what was dropped and you must "
    "repeat that list. The file is attached to the chat.",
    {"format": {"type": "string",
                "enum": ["fcpxml", "otio", "edl", "srt"]}}),
```

Not a `WRITE_TOOL` (no EDL version). Add `"exported"` handling to `acted` in
`_reply_violations` so "I exported it to Premiere" is a *truthful* zero-write turn
(the `_assets_made_note` mechanism is the precedent — extend `ctx` with
`ctx.files_exported`).

### 4.3 Acceptance

- Round-trip a 12-cut edit with 2 inserts, 1 overlay, 1 speed span and music into
  Resolve 19 and FCP: cut points land within 1 frame of our own render.
- OTIO output validates against `otio.adapters.read_from_string`.
- The dropped-features list is exhaustive (test asserts every EDL field is either
  mapped or named in the dropped list — a new EDL field must fail this test until
  someone decides which side it is on).

---

## 5. Workstream W3 — True crossfade, without breaking timeline math

**Why.** A cross-dissolve is table stakes in every NLE ever shipped. We apologise
for its absence in three separate places in the system prompt
(`agent_prompt.py:57`, `agent_tools.py:2147`, `agent_tools.py:6157`). It is the
most conspicuous "this isn't a real editor" tell we have.

**Why it was never built.** `build_filtergraph` concatenates per-span blocks and
every transition style is *duration-preserving by construction*. `xfade` overlaps
two clips, so it **shortens** the program by `d` per junction — which would
invalidate `program_duration()`, `Timeline`, caption anchoring, music placement,
`remap_program_items`, and every cached render's duration verification.

**The design that avoids all of that: handles.**

A keep span `[s, e]` is a *window into a longer source*. The frames just before
`s` and just after `e` still exist. A real NLE calls those handles. So:

> Block *k* is extended at its outgoing edge by `d` seconds of source footage
> **beyond** `keep[k][1]`, and block *k+1* is extended at its incoming edge by `d`
> seconds **before** `keep[k+1][0]`. The two extensions are `xfade`d together.
> Program duration is unchanged, because the overlap consumes exactly the frames
> that were added.

This is precisely how a dissolve works in Premiere, it needs no timeline change,
and it is honest: the dissolve shows footage the user cut, which is what a
dissolve *is*.

### 5.1 Implementation

`worker/schemas.py`:
```python
TRANSITION_STYLES = (..., "crossfade", "dissolve_white", "dip_to_blur")
```
Add to `TransitionSpec` nothing new — style is enough.

`worker/renderer.py::build_filtergraph`, in the `if transition and len(blocks) > 1`
branch: `crossfade` joins the geometry-manufacturing family conceptually but needs
its own path, because it changes how blocks are *trimmed*, not just filtered.

1. Before block construction, compute per-junction handle availability:
   `head_room[k] = keep[k][0] - (previous span end or 0)` and
   `tail_room[k] = (next span start or src_dur) - keep[k][1]`, in source time,
   accounting for `speed_pieces`.
2. `d_eff[k] = min(d, tail_room[k], head_room[k+1], block_dur[k]/2 - 0.05,
   block_dur[k+1]/2 - 0.05)`.
3. If `d_eff[k] < 0.08`, that junction falls back to `dip_black` at the same
   duration and the tool result **names the junction and why**.
4. Trim blocks with the handles included, then chain
   `xfade=transition=fade:duration=d_eff:offset=…` pairwise instead of `concat`
   for those junctions, and `acrossfade` the matching audio.

**Warning about xfade chains:** `xfade` is pairwise, so N blocks with crossfades
means an N-deep chain, and each stage is full-resolution. On the current 1-vCPU
box this is exactly the OOM shape that killed us with per-block whip overlays. So:
- cap the number of crossfaded junctions per render (`MAX_XFADE_JUNCTIONS`,
  default 24) and fall back to dips beyond that, **saying so**;
- gate the whole style behind the remote executor if P0.2 has not landed.

### 5.2 Tool surface

`set_transitions(style="crossfade", duration_s=0.4)` — no new tool. But the result
string must report per-junction reality:

```
crossfade 0.40s at 11 of 13 junctions. 2 junctions fell back to dip_black:
at 0.0s (no footage before the first kept span) and at 63.4s (only 0.05s of
unused footage after that cut).
```

### 5.3 Prompt + honesty changes

- Delete "True crossfades still do not exist — say so when asked." from
  `agent_prompt.py:57`, and the two matching sentences in `agent_tools.py`.
- Remove "True crossfades (overlapping footage)" from the NOT-supported list in
  the line-67 capability bullet; **add** the honest limit: *a crossfade shows the
  frames on either side of the cut, so it needs unused footage there — at the very
  start and end of the source, and where two kept spans are adjacent, it falls
  back to a dip and the tool says so.*
- Add `crossfade|dissolve|cross.?dissolve` to the `EDIT_CLAIM` transitions
  alternation.

### 5.4 Acceptance

Golden test: an EDL with `style="dip_black"` produces a byte-identical graph to
before this change. A 3-span EDL with `crossfade` renders to *exactly* the same
duration as the same EDL with `dip_black`. A crossfade at the very first junction
of a keep list starting at `0.0` falls back and reports it.

---

## 6. Workstream W4 — The subject layer (masks, matting, tracking)

**Why.** Vyra has `createMask`, `removeBackground`, keyers, and power windows;
Adobe has full roto. We have `blur_region` and `erase_region` — static rectangles.
Every "put me on a different background", "blur his face for the whole video",
"keep the text behind my head", "follow him with the zoom" request fails today.
This is the largest *quality* gap.

**Why we can beat them on the part that matters.** Vyra and Adobe give you a
masking *tool*; the human still drives it. We can make the subject a **first-class
index sidecar** so every other tool becomes subject-aware for free:
subject-following reframe, subject-tracked blur, subject-tracked overlays,
zoom-that-follows, text behind the speaker.

### 6.1 Sidecar — `worker/subjects.py`

`get_or_compute_for_index(...)` again. Runs on the 540p proxy, not the original.

Output, stored as one JSON sidecar per index sha:
```jsonc
{
  "fps": 6.0,                    // tracks are sampled, not per-frame
  "tracks": [
    {"id": "person_1", "label": "person",
     "boxes": [[t, x, y, w, h, conf], ...],   // normalized 0-1
     "present": [[t0,t1], ...]},
    {"id": "face_1", "label": "face", "parent": "person_1", "boxes": [...]}
  ],
  "engine": "yolo11n+bytetrack",
  "warnings": []
}
```

Engine choice — pick by what runs on the executor CPU inside the turn budget:
- Detection+tracking: a small YOLO (n/s) + ByteTrack, or MediaPipe pose/face for
  the person-only case. Sample at 6 fps and interpolate; a talking head does not
  need 30 fps tracking.
- Matting (only when a real alpha is required): `rembg`/MODNet on sampled frames
  with temporal smoothing, or an API. **If it is an API, it is gated by I5** and
  costs go on `ctx.gen_extra_cost_usd`.

**Budget rule.** Tracking a 20-minute video inside a 720s agent turn will not
happen. So: tracking runs *windowed* — a tool asks for `[start, end]` and only
that window is computed and cached. `CLEAN_MAX_SOURCE_S` is the precedent for the
refusal (`worker/config.py:391`); add `TRACK_MAX_WINDOW_S` (default 180) and
refuse honestly beyond it, offering the static alternative.

### 6.2 EDL additions

```python
class TrackedRegion(BaseModel):        # NEW — worker/schemas.py
    id: str
    subject_id: str                    # track id from the sidecar
    mode: Literal["blur", "pixelate", "black", "spotlight"] = "blur"
    start: Optional[float] = None      # source time; None = whole video
    end: Optional[float] = None
    pad: float = 0.08                  # box inflation, 0-0.5
    shape: Literal["box", "ellipse"] = "ellipse"

class Matte(BaseModel):                # NEW
    id: str
    subject_id: str
    start: Optional[float] = None
    end: Optional[float] = None
    mode: Literal["replace_bg", "behind_text", "cutout"] = "replace_bg"
    bg_asset_key: Optional[str] = None   # for replace_bg
    bg_color: Optional[str] = None       # #RRGGBB alternative
    key: str                             # R2 key of the baked alpha sequence

# EDL, both defaulting empty (I3):
tracked_regions: List[TrackedRegion] = Field(default_factory=list)
mattes: List[Matte] = Field(default_factory=list)
```

`Matte.key` matters: matting is expensive and must be **baked once into an R2
artifact** keyed by `(sha, subject_id, window, mode)`, exactly like
`clean_source_key()` does for inpainting (`worker/renderer.py:269`). The renderer
reads the baked artifact; it never runs a model.

### 6.3 Renderer

- `tracked_regions` → extend `_region_parts()` (`renderer.py:176`) to accept a
  time-varying box: emit `boxblur`/`pixelize` with `x=`/`y=`/`w=`/`h=` as
  `sendcmd`-driven or expression-interpolated values from the track. For an
  ellipse, use a `geq`-generated alpha or a small looped `alphamerge` — measure
  cost, prefer the box form when the ellipse costs more than 15% extra.
- `mattes` → `alphamerge` the baked alpha with the source, then `overlay` on the
  background asset (`replace_bg`), or overlay the cutout **above** a text layer
  and below captions (`behind_text`).
- Both must be `enable=`-gated so a windowed effect costs nothing outside its
  window, and both must be absent from the graph entirely when the lists are
  empty (golden test).

### 6.4 Tools

```python
"find_subjects"        (read)  -> lists tracks with label, on-screen spans, size
"track_blur"           (write) -> TrackedRegion, mode blur/pixelate/black
"remove_background"    (write) -> Matte(replace_bg), bg from asset or color
"put_behind_subject"   (write) -> Matte(behind_text) + binds an existing text id
"follow_subject"       (write) -> rewrites an existing zoom's `path` (the
                                  ZoomPathPoint list already exists!) from a track
"remove_tracked"       (write)
```

`follow_subject` is nearly free: `ZoomItem` already carries
`path: List[ZoomPathPoint]` with a `follow` mode. Feeding it a subject track turns
an existing renderer feature into "the camera follows him", which is one of the
most-requested things in creator editing.

### 6.5 Honesty

- `find_subjects` returns confidence per track and **the tool must say when
  tracking is unreliable** (occlusion, >2 similar subjects, motion blur).
- `remove_background` on non-person subjects, hair detail, or low-light footage is
  where matting visibly fails. The tool samples 3 frames of the result, and if the
  alpha's edge energy exceeds a threshold it returns `EDGES ROUGH` and the agent
  must say so — same contract as `STILL VISIBLE`.
- Add `tracked|following|background removed|masked|isolated` to `EDIT_CLAIM`.

---

## 7. Workstream W5 — Multi-source timeline

**Why.** `keep` is source-time on **one** file. Every competitor assembles N
clips. Today a user with 6 GoPro files cannot make one video unless they all go
in as inserts on a canvas, which loses transcript, captions, cutting, silence
removal and shot data for everything but the main file. This blocks vlogs,
multi-cam, event recap, and "here are my 12 clips, make me a montage" — a large
share of real demand.

**This is the biggest lift in the document.** Do it after W1–W3.

### 7.1 Design: promote `keep` to a list of source-scoped spans

```python
class KeepSpan(BaseModel):
    src: str = "main"     # asset key, or "main" for the project's primary video
    start: float
    end: float
```

`keep: List[List[float]]` becomes `keep: Union[List[List[float]], List[KeepSpan]]`
and a normalizer converts the legacy form to `src="main"` on load. **Because the
normalizer runs inside `validate_edl`, `edl_signature` must be computed on the
legacy shape when every span is `main`** — otherwise every cached render on the
platform busts (I3). Write that as an explicit function with its own test:
`_keep_sig(keep)` emits `[[s,e],…]` when all spans are `main`, and the richer form
otherwise.

### 7.2 Index becomes per-asset

Today `index` is one row per sha, and `ToolContext.index` is *the* index. Change:
- every `video_clip` asset gets its own index row on upload (already true for the
  main video — reuse `run_index_job` with a different `payload.role`),
- `ToolContext` grows `ctx.indexes: dict[src_key -> index]` and `ctx.index` stays
  pointing at `main` for backwards compatibility,
- read tools grow an optional `src` argument (`get_transcript(src="clip_3")`),
  defaulting to `main`.

**Cost control:** indexing 12 clips serially on 1 vCPU is a disaster. Index
clips **lazily** — on first read-tool access or first placement — and in parallel
on the executor. Report progress in chat.

### 7.3 Renderer

`build_filtergraph` currently opens one source input. It must open N and build
blocks per `KeepSpan.src`. Everything downstream (`concat`, transitions, captions,
overlays) already works on blocks. `source_clean` becomes per-source
(`Dict[str, SourceClean]`).

**Non-negotiable:** normalize every source to the output canvas (`_normalize_video`
already exists) before concat — mismatched fps/resolution/rotation between phone
clips is the #1 cause of broken multi-clip renders.

### 7.4 Tools

```python
"add_clip"        -> append/insert another project video into the keep list
"reorder_clips"   -> move a clip's span block in program order
"remove_clip"     -> drop every span belonging to one source
"list_clips"      -> what's on the timeline, in order, with per-source duration
```

Cutting, captions, silences, filler removal must then operate across sources —
`get_kept_transcript` already returns program time and just needs to walk the
per-source word lists in program order.

### 7.5 Acceptance

- A project with 4 clips of differing resolution/fps/rotation renders to one
  continuous program with correct audio sync.
- A legacy single-video EDL produces a byte-identical filtergraph and an
  identical `edl_signature` to before the change. **This is the test that gates
  the merge.**

---

## 8. Workstream W6 — Speakers (diarization, multicam, auto-switching)

**Why.** AutoPod's entire business is multi-camera podcast switching inside
Premiere. Descript has speaker tracks. We cannot even tell you who is talking.
It also unlocks the highest-value long-form job: a two-camera interview cut
automatically.

### 8.1 Sidecar

Deepgram nova-3 already supports `diarize=true` — **turn it on in
`worker/transcribe.py` and attach `speaker` to each word**. This changes the index
payload, so it **is** a `PIPELINE_VERSION` bump (I4's exception). Batch it with any
other index change and land it once; do not bump twice.

Fallback when Deepgram is off (whisper path): a small embedding + clustering
sidecar, or degrade honestly (`warnings.append("speaker labels unavailable")`).

### 8.2 Tools

```python
"list_speakers"     (read)  -> speakers, talk time, first/last appearance
"keep_speaker"      (write) -> keep only spans where speaker X talks
"cut_speaker"       (write)
"label_speakers"    (write) -> user-supplied names, used by lower thirds
"auto_multicam"     (write) -> requires W5: with N angle clips + one shared audio
                               reference, cut to whoever is speaking
```

`auto_multicam` is the flagship. Contract:
- inputs: the angle assets, a switching `min_shot_s` (default 2.5, so it does not
  strobe), and `wide_on_overlap=true` (cut to a wide when both talk).
- sync: cross-correlate audio between angles to find offsets, **and report the
  measured offset and correlation confidence**; refuse below a threshold rather
  than producing a drifting cut (I8).
- output: a keep list across sources, plus a plain-language report of how many
  switches were placed and where.

### 8.3 Honesty

Add `speaker|diariz|multicam|switched` to `EDIT_CLAIM`. `list_speakers` must
report the diarizer's confidence and say plainly when two speakers were probably
merged.

---

## 9. Workstream W7 — Translation, subtitles, dubbing

**Why.** HeyGen sells 175+ languages; Klap's whole angle is multilingual; Captions
does multilingual narration. It is one of the biggest paying segments in AI video
and we have nothing. It is also *cheap for us*: we already have word-level
transcripts and a caption renderer that handles complex scripts and RTL (round 44).

### 9.1 Three tiers, ship in this order

1. **Translated captions** (days). Translate `kept_transcript` sentences via
   `llm.ask_text`, keep the original word timings, re-fit line breaks per
   language. Our caption engine already shapes Arabic/Hebrew/Devanagari correctly
   — that work is done and unused. New EDL field:
   `captions.language` + `captions.translated_items` (empty default, I3).
2. **Burned subtitle track in a second language** — same renderer path, second
   ASS file, positioned above/below the primary.
3. **Dubbing** (weeks, external service, I5-gated). TTS per sentence, time-fitted
   to the original span with `atempo` within ±12% before it sounds wrong, ducked
   under or replacing the original. Cost on `ctx.gen_extra_cost_usd`.

### 9.2 Honest limits to write into the tool descriptions

Dubbing does not lip-sync. Time-fitting distorts pacing when the target language
is much longer. Translated captions inherit ASR errors. Say all three up front —
this is a category where competitors overpromise and a straight answer sells.

---

## 10. Workstream W8 — Formats: make an edit reusable

**Why.** Mosaic's genuine insight: *an edit is a pipeline, not a conversation*. A
creator publishing 5 videos a week does not want five chat sessions; they want
"run my format on this file." Nobody else in our price bracket offers it, and it
converts one-time users into subscribers because the value compounds.

### 10.1 Design

A **format** is a named, ordered list of tool calls with their arguments,
extracted from a session the user liked.

```jsonc
{ "name": "My Reel Format",
  "steps": [
    {"tool": "cut_silences",     "args": {"min_silence_s": 0.45}},
    {"tool": "remove_filler_words", "args": {}},
    {"tool": "auto_reframe",     "args": {"ratio": "9:16"}},
    {"tool": "add_captions",     "args": {"mode": "from_transcript"}},
    {"tool": "set_caption_style","args": {"preset": "podcast", "size": "l"}},
    {"tool": "punch_in_on_emphasis", "args": {"count": 4}},
    {"tool": "sound_design_pass","args": {"intensity": "medium"}},
    {"tool": "set_master_loudness", "args": {"enabled": true}}
  ] }
```

Only **video-independent** tools are eligible: anything whose arguments contain a
timestamp, an asset key or an id is excluded automatically (that rule is
mechanical — enforce it in code, do not ask the model).

- `save_format(name)` — reads `ctx.write_calls` for this session, filters, stores.
- `list_formats()` / `apply_format(name)` — replays steps in order, reporting each
  result; a failed step does not abort the rest, and the report says which failed.
- Backend: `POST /projects/:id/apply-format` so it can run on upload without a
  chat message at all — that is the "autopilot" competitors charge for.

### 10.2 Honesty

`apply_format` returns a per-step ledger. The reply must reflect it: "6 of 8 steps
applied; beat_align_cuts refused (weak pulse, 0.31 confidence) and
punch_in_on_emphasis found no stressed words in the surviving cut."

---

## 11. Workstream W9 — Generalized keyframing

**Why.** Vyra keyframes any animatable effect parameter. We have `AnimFloat` on
specific fields. This is the difference between "we have effects" and "we have
animation".

**Design.** `AnimFloat` already exists and the renderer already compiles it
(`_anim_expr`, `renderer.py:62`). The work is *coverage*, not invention:

1. Audit every numeric field on `ZoomItem`, `OverlayItem`, `TextItem`,
   `StylizeItem`, `GradeCustom`, `RegionItem`, `VolumeItem`, `MusicItem.gain_db`.
2. Widen the ones that can animate to `Union[float, AnimFloat]`.
3. Emit `_anim_expr` for them in the filtergraph.
4. Add one tool: `animate(target_id, field, keyframes, easing)` that writes an
   `AnimFloat` onto any whitelisted field, and one read tool `list_animatable()`
   generated from the schema so it can never go stale (same generation trick as
   `capabilities_digest`).

**Guardrail:** a field that the renderer cannot express per-frame must reject with
"that value is fixed for the whole clip" rather than accepting an `AnimFloat` and
silently using its first value.

---

## 12. What NOT to build

- **Social publishing / scheduling.** Opus Clip and Submagic own it, it is
  OAuth-and-support-ticket work, and it does not make the *editor* better.
- **Avatars / AI presenters.** HeyGen and Captions are years ahead and it is a
  different product.
- **A general-purpose NLE UI.** We lose that race to Adobe and to free OpenCut.
  Our timeline should stay a *review and nudge* surface, not a creation surface.
- **Model-agnostic BYO-key.** It fragments the credit model, breaks the spend cap,
  and every quality regression becomes unattributable.

---

## 13. Sequencing

| Phase | Work | Rationale |
|---|---|---|
| **0** | P0.1 vision probe, P0.2 executor + slots | Everything else is worthless if the product is slow and half-blind |
| **1** | W1 highlights · W3 crossfade | Highest demand ÷ effort. Both are self-contained. |
| **2** | W2 NLE export · W8 formats | Opens the pro segment and makes subscriptions compound. Neither touches the renderer. |
| **3** | W6 speakers (index bump, batched) · W7 tier 1–2 translation | One `PIPELINE_VERSION` bump, two features. |
| **4** | W4 subject layer | Biggest quality jump; needs vision + executor capacity in place. |
| **5** | W5 multi-source | Biggest architectural change; do it last, behind the golden-signature test. |
| **6** | W9 keyframing · W7 tier 3 dubbing | Depth once breadth is there. |

---

## 14. The thing that must not be lost

Every capability above exists in at least one competitor. **The honesty layer does
not exist anywhere else** — I checked Descript (which documents the opposite:
Underlord "might overpromise"), Vyra, Adobe, Eddie, Mosaic, and a dozen
open-source agentic editors. It is our only uncopied asset.

So the acceptance bar for every workstream in this document is not "the feature
works." It is:

1. The tool **measures** its own result and returns the measurement (I7).
2. It **refuses honestly** with a named reason when the measurement is weak (I8).
3. Its success verbs are **fenced** in `EDIT_CLAIM` so the model cannot claim it
   without having done it (I6).
4. It is **invisible** when its backing service is unconfigured (I5).
5. Legacy EDLs produce **byte-identical** signatures and filtergraphs (I3).

A feature that ships without all five makes Valmera more capable and less
trustworthy, which is a net loss — trustworthiness is the only thing here that
competitors cannot ship next quarter.
