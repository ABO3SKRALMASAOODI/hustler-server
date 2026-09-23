# Taste calibration v7

## Taste is evidence, not an adjective

“Cinematic,” “viral,” “clean,” and “like Instagram” are too ambiguous to drive
a consistent edit. Convert examples into decisions that can be observed in a
timeline.

Ask for links or files the user already likes. There is no prescribed count.
One example may establish one useful trait; additional contrasting examples
help separate stable taste from a one-off gimmick. Negative examples are
optional. For every example, capture only the attributes that matter:

- the first two seconds and how context is withheld or supplied;
- average shot hold and where pace accelerates;
- caption size, position, line length, animation, and emphasis;
- speaker scale, crop, eyeline, and reframing behavior;
- why each B-roll insert exists and how long it stays;
- graphic motifs, type hierarchy, texture, color, and transitions;
- music/SFX role, if the user wants audio design in Valmera;
- how the ending resolves and whether it loops.

Record speech state separately from visual pace: whether dialogue continues
under B-roll, stops for a montage, or starts after a silent action opening.
Record the outer output ratio separately from an inset rounded video card,
its backdrop, margins, corner radius, and shadow. A 4:5 rectangle alone does
not implement a request for rounded corners.

The current brief takes precedence over a reference's observed structure or
an earlier approved profile. Preserve historical profiles as evidence and
create a new run profile for changed preferences. In particular, do not carry
forward an old "return to conversation" rule into a no-speech montage, a
"no B-roll" rule into fast conversation, a different speaker's identity, or a
9:16-only rule into a request for deliberate format choice. Preserve original
observations as observations; label the new directions as user requirements.

Record what the user likes about each example. A reference is not a blanket
instruction to copy everything in it.

## How the user provides references

Accept reference media in this order:

1. files attached to the Codex task or absolute local file paths;
2. uniquely resolvable filenames already inside the current workspace;
3. public URLs that can be accessed without evading authentication or access
   controls; or
4. the single style-reference asset already attached to the Valmera parent.

For local files, prefer an absolute path. A bare filename is acceptable only
when the coordinator can resolve exactly one matching file; otherwise ask for
the path instead of guessing. A convenient optional folder is
`<workspace>/reference-shorts/`, but no particular folder is required. For a
URL, save one verified local working copy when acquisition is permitted and
record the source URL, media hash, and duration. If the URL is inaccessible,
ask for the downloaded file.

Valmera Studio's current Shorts control, **+ style reference**, accepts one
video under five minutes and replaces the prior visible reference. It is a
useful single-example shortcut, not a multi-reference taste library. Do not
repeatedly replace that asset to simulate a set. When the user provides
multiple references, the coordinator analyzes them from the task's attached or
local files and records all of them in the taste profile; uploading the whole
set to Valmera is not required. The Studio card reaching `Reference ready`
means the asset was indexed and made available; it is not evidence that an
agent analyzed it or that its style was copied. Only a completed, checksummed
taste profile proves the v7 analysis stage occurred.

## Who watches and how

The coordinator is the taste analyst. It inspects every supplied example once
before story assignments are dispatched. Editors do not independently browse
for references and do not each repeat the full analysis. They receive the
checksummed profile, the chosen style lane, and only the timestamped reference
observations relevant to their assigned story. An editor may reopen a relevant
example to resolve a specific ambiguity, but cannot silently reinterpret the
whole taste set.

For each example, the coordinator must:

1. verify identity, duration, dimensions, streams, and SHA-256;
2. obtain complete transcript/caption coverage when speech or on-screen words
   are present, retaining uncertainty and ASR gaps;
3. inspect the first two seconds densely, the ending, regular full-duration
   coverage, caption-state changes, and every detected or observed cut;
4. create a timestamped cut/shot map and measure hold times rather than judging
   pace from a thumbnail;
5. record crop, text safe areas, B-roll purpose, transition grammar, energy
   arc, and any visible audio identity or rights information; and
6. write per-reference `transfer`, `adapt`, and `do_not_copy` observations.

Use `watch_video` when the client actually provides usable video input. When it
does not, decode the local file into timestamped contact sheets and boundary
frames and combine them with complete transcript/caption and deterministic
media evidence. Frames are visual evidence, not proof of continuous playback.
An attached audio block or an unmuted player is not proof that the current
model heard it: never claim subjective hearing unless the client explicitly
provides that capability. Use reliable visible track identity, ASR, stream
facts, loudness/silence measurements, and authored timing for audio decisions,
and label inferences as inferences.

After analysis, the coordinator writes `taste/taste-profile.json` and uses it
as the sole batch-wide taste source. During QC, the same coordinator inspects
each final candidate against both its story assignment and this profile. This
creates one accountable taste judgment at intake and one at acceptance instead
of delegating taste to a hidden critic or relying on editor self-approval.

## Taste profile

Persist this shape in `taste/taste-profile.json`:

```json
{
  "version": "taste-profile-v1",
  "approval": "user|autopilot",
  "invariants": ["verifiable editing rules shared by every short"],
  "style_lanes": [
    {
      "id": "intimate-conversation",
      "best_for": ["confession", "personal turning point"],
      "pace": "measured with acceleration at the realization",
      "captions": "quiet two-line hierarchy with selective emphasis",
      "visuals": "speaker-led; B-roll only for memory or consequence",
      "sound": "source-first",
      "references": ["URL or local reference ID"]
    }
  ],
  "forbidden": ["generic motivational stock", "unwanted third-party watermarks"],
  "reference_notes": [
    {
      "id": "liked-hook-01",
      "source": {
        "kind": "local_file|url|valmera_asset",
        "locator": "absolute path, URL, or asset ID",
        "sha256": "verified media hash",
        "duration_s": 42.1
      },
      "user_likes": ["cold-open hook; not the music"],
      "evidence_mode": "direct_video|decoded_frames_transcript",
      "observations": [
        {
          "id": "liked-hook-01-o1",
          "range_s": [0.0, 2.0],
          "transfer": "open on the consequence before supplying context",
          "confidence": "high"
        }
      ],
      "do_not_copy": ["footage", "caption wording", "branding", "music"]
    }
  ],
  "approved_at": "ISO-8601 or null"
}
```

After approval, save the reusable source profile under
`<workspace>/.valmera/podcast-shorts/taste-profiles/<profile-id>.json` and copy
it into each run. The run copy is immutable evidence; revisions create a new
profile ID or require the user's explicit request to replace the prior profile.
Attach the run copy with `run_state.py taste` so later tasks verify its SHA-256
rather than trusting a familiar filename.

Keep production-quality invariants separate from creative choices. Translate
“premium” into observed source detail, clean glyph rendering, reading comfort,
accurate timing, coherent hierarchy, and intentional motion. Check safe placement
against the actual composition. Font, palette, scale, phrase length, layout, and
animation belong to the selected treatment; they may differ between stories and
must not become permanent requirements merely because a previous repair worked.

Use two to five style lanes so a batch varies without becoming random. Choose
the lane from the story, not round-robin decoration. Useful lane families are:

- intimate conversation;
- cinematic aspiration;
- evidence-led explainer;
- archival retrospective;
- tension/comedy.

The profile may reject any lane. These names are starting points, not presets.

For the styles in `style-lanes-v7.md`, use their stable lane names rather
than reference upload order. Store the lane's dialogue behavior, hold ranges,
opening duration, caption behavior, and acceptance rules. Store the planned
output ratio and frame treatment separately. Assignments and candidate bundles
add exact output-time silence intervals and music-placement cues. Do not
mislabel user-specified timing as a measurement of a reference.

For the user's current four-style brief, load the three added references
through `headline-conversation-references-v7.md`. Separate the encoded 9:16
canvas from the wider visible picture within black margins. Record the
persistent topic headline separately from changing dialogue subtitles. The
September 14 correction removes the default lane: compare each story's spoken
structure and visual potential across all four lanes using `style-lanes-v7.md`.
Record selection reasons and review dominant distributions without imposing
equal shares. The black-canvas composition remains a layout preference and
does not favor `headline-conversation` over the other styles.

Record bold/heavy headline typography with at least the visible stroke weight
of dialogue captions, and preservation of the native Valmera corner mark and
complete branded ending. Unwanted reference/asset watermarks are distinct
from this required user-owned branding. Include geometry-aware placement and
native ending duration in the new profile; never inherit a blanket watermark
ban, automatic Style 4 preference, or end-card removal from an older run.

## Pilot calibration when examples are missing

If no approved profile and no usable reference examples exist, select one
strong story and create three low-resolution pilot previews:

1. speaker-led and restrained;
2. kinetic evidence/explainer;
3. cinematic story treatment.

Keep the story range identical so the user is judging treatment, not content.
Show the three previews together and ask one compact question: which treatment
is closest, and what should be borrowed from the others? Convert the answer to
the taste profile. Do not edit the whole batch first.

If the user explicitly chooses autopilot, the coordinator analyzes current
topic-appropriate references and records `approval: "autopilot"`. Autopilot is
not permission to use one style for every story.

## Legal and creative boundaries

References teach relationships: pacing, hierarchy, framing, transition logic,
and narrative rhythm. Do not download or reuse a reference creator's footage,
music, logo, captions, or branded graphics unless the user owns or licenses
them. Use clean source material or licensed stock for the actual edit.

Do not chase novelty at the expense of coherence. Variety belongs between
shorts; within one short, typography, color, and motion must feel like one
system.
