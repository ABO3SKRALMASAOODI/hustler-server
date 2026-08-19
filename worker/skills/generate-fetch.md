# generate-fetch — AI images/video/sfx, downloading from links

## Editorial decision principles

Create or fetch media only for a concrete editorial gap. Preserve provenance, verify the actual rendition and never mistake acquisition for placement.

## Evidence to inspect

Inspect the requested subject/action, source/license metadata, real downloaded/generated frames, dimensions, duration, artifacts, palette and compatibility with adjacent footage.

## Strong treatment patterns

Everything here is gated on CAPABILITIES — if the tool is not listed there, it is not configured on this deployment: say so honestly and offer the closest alternative. Every created/downloaded thing is a project ASSET and reaches the video only when you PLACE it (insert_media / add_overlay / add_music) — a turn that generated but never placed changed nothing the viewer sees.

GENERATED IMAGES (generate_image): from a text prompt alone; by restyling a FRAME of the main video (from_video_time_s — "give this character a ponytail" repaints that exact frame); or by restyling an uploaded image (from_asset_key). It lands as a full-frame STILL moment (a freeze-frame cutaway) when inserted — typically 2-4s with a Ken Burns motion so it doesn't sit frozen. It does NOT modify or track the moving footage — say that. Flow for "change X about a character/object": find the moment (filmstrip / look_at), restyle that frame, look_at_asset the result to confirm the change actually shows, insert at that moment, render. If the generation fails or doesn't show the requested change, say so — never insert a bad image silently.

GENERATED VIDEO: not available. You cannot generate moving AI footage. Say so once, then edit the uploaded clips or generate a still image and insert it.

FETCHED SOUND (search_sfx → fetch_sfx): any one-shot the edit needs, found as a REAL recording on the web ("whoosh", "camera shutter", "keyboard click") and downloaded ready for add_sfx. Placement policy lives in the audio skill: every sound on a nameable visible moment, unrequested ones only where the format calls for sound design.

LINKS (fetch_url): when the user pastes a URL for something they want in the edit — a song, a clip, a photo — DOWNLOAD IT instead of asking for an upload. Handles direct file links (Dropbox, Drive, CDN) and page links (YouTube, TikTok, Vimeo, SoundCloud); works out video/audio/image itself; as_kind='music' pulls audio out of a video page. If the download fails the tool says why (YouTube bot wall, private video, too big, dead link) — repeat that reason in one clause and CONTINUE the edit with already-attached music or clips. A failed fetch is not a reason to freeze the picture or wait for an MP3. Never claim you added something you could not fetch. A link they asked you to WATCH / use as style is a REFERENCE: look_at_asset if you already have the file, do not insert_media it as footage. When fetch_url is NOT listed: say plainly you cannot fetch links and ask for the file (the paperclip in chat).

ANIMATION REQUESTS ("animate it", "make it an animated video"): you cannot generate cartoons or motion graphics. Say so once, then deliver real motion with what exists: stacked 1-2 word fade captions, eased/Ken Burns zooms, dip transitions, Ken Burns motion on inserted images, fades.

## Common failure modes

- Accepting metadata/thumbnail claims without checking the rendition, repeated near-duplicates, artifacts, wrong aspect, weak provenance or placing media before review.

## Verification procedure

Inspect real frames at several moments, probe dimensions/duration, compare against the named purpose and adjacent footage, then review its placed junctions.

## Repair ladder

Try the configured fallback → refine prompt/query → choose another candidate → crop/fit only if content remains valid → ask for an upload → omit rather than fake.
