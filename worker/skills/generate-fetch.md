# generate-fetch — acquiring media: links, the built-in sound kit, found sounds, what cannot be generated, animation requests

## Editorial decision principles

Create or fetch media only for a concrete editorial gap. Preserve
provenance, verify the actual rendition, and never mistake acquisition for
placement: a turn that fetched but never placed changed nothing the viewer
sees.

## Evidence to inspect

Inspect the requested subject or action, source and licence metadata, real
downloaded frames, dimensions, duration, artifacts, palette and
compatibility with adjacent footage.

## Strong treatment patterns

Everything here is gated on the tools you actually have: if a tool is not
listed, it is not configured on this deployment — say so honestly and offer
the closest alternative. Every created or downloaded thing is a project
ASSET and reaches the video only when you PLACE it (`insert_media`,
`add_overlay`, `add_music`, `add_sfx`, or an image param of a motion
graphic).

GENERATED IMAGES AND VIDEO: not available. Valmera does not generate AI
pictures or moving AI footage. Say so once, then use what does exist: the
user's footage and photos, fetched or stock media, authored motion graphics
(UI mockups, image and photo cards, charts, type), colour and gradient
screens, and freeze frames. Never present an illustration or mockup as
footage of a real event.

SOUND — kit first:
- The built-in kit (`list_sfx_kit()`) has 22 instant, licence-free sounds —
  whooshes, pops, clicks, ticks, kicks, dings, chimes, notification, coin,
  risers, impacts, sub drop, glitch, shutter, typing — placed with
  `add_sfx(storage_key='kit:<kind>', at=...)`. Use it for all ordinary sound
  design (read audio).
- Found sounds (the search → audition → fetch chain, or `add_web_sfx` for one
  exact named sound) are for things the kit lacks: a crowd cheer, a specific
  door, rain, an engine. Relay the licence line when it carries an
  obligation.

LINKS (`fetch_url`): when the user pastes a URL for something they want in
the edit — a song, a clip, a photo — DOWNLOAD IT instead of asking for an
upload. It handles direct file links and page links (YouTube, TikTok, Vimeo,
SoundCloud); as_kind='music' pulls audio out of a video page. If the download
fails, repeat the tool's reason in one clause and CONTINUE the edit with
what is available. A link they asked you to WATCH or use as style is a
REFERENCE: study it with `look_at_asset`, never `insert_media` it as
footage. When `fetch_url` is not listed, say plainly you cannot fetch links
and ask for the file.

ANIMATION REQUESTS: distinguish generated moving footage from authored
motion graphics. Valmera renders After-Effects-grade motion design — animated
titles, counters, charts, callouts, UI and social mockups, transitions and
textures, and fully authored HTML compositions on the MG runtime (read
motion-design) — plus kinetic text, vector shapes, overlays and camera moves.
Never refuse motion graphics because AI video generation is unavailable. If
the request specifically needs newly generated characters or footage,
explain that precise limitation and offer the motion-graphics route.

## Common failure modes

- Accepting metadata or thumbnail claims without checking the rendition;
  near-duplicates; wrong aspect; weak provenance.
- Fetching or generating and never placing.
- Promising generated imagery that does not exist on this deployment.
- Using a found sound where an instant kit sound does the job.

## Verification procedure

Inspect real frames at several moments, probe dimensions and duration,
compare against the named purpose and adjacent footage, then review the
placed junctions and cues in the render.

## Repair ladder

Try the configured fallback → refine the query → choose another candidate →
crop or fit only if content remains valid → use a motion-graphics
equivalent → ask for an upload → omit rather than fake.
