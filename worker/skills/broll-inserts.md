# broll-inserts — B-roll as evidence: cutaways vs splices, designed entrances, moving stills, inserting media, stock footage

## Editorial decision principles

B-roll is story evidence, not wallpaper: show the concrete person, thing, place or event the speaker names, on the words that name it, while the voice keeps going. Premium reels cut to evidence often and make each entrance designed — a whip, a light leak or a card entrance with a whoosh — and never leave a still sitting frozen. Decide whether speech should continue, prefer authentic user footage, and judge all candidates as one coherent sequence.

## Evidence to inspect

Inspect the kept transcript/program clock, source and downloaded frames, candidate authenticity, duplication, palette/motion compatibility, face-dependent payoffs and entry/exit junctions.

## Strong treatment patterns

THE CHOICE: does the voice keep going?
- B-ROLL / CUTAWAY (the most human editing move): the speaker mentions something concrete — SHOW it while their voice keeps going. add_overlay(fit='cover', start, duration_s 2-6) switches the PICTURE while the program's audio and captions keep running. Placement: get_kept_transcript gives each sentence's PROGRAM time — start the cover ON the words that mention the thing.
- SPLICE (insert_media): PAUSES the program and adds time. Right for "add this clip at the end", "put it between the scenes", a beat between sentences.
- Taste: map every proposed cutaway to a narrative purpose (proof, context, contrast, scale, time, place, payoff). On a reel, every concrete noun is a candidate for evidence; density follows the story, not a quota. Never cover a punchline, admission or reaction that earns a face-on delivery, and never use generic wallpaper merely because a search result exists.

DESIGNED ENTRANCES AND MOVING STILLS (short-form):
- Enter B-roll on the word, and when the cutaway marks a turn give it a designed junction — a whip or zoom_punch, a `light_leak` or `flash_transition` motion graphic — with a whoosh whose peak lands on the cut (read transitions). Plain hard cuts are right inside a fast evidence run.
- Stills never sit frozen: give photos a slow push or drift (`set_overlay_motion` scale/x/y keyframes, or `insert_media` motion='zoom_in'/'zoom_out'/'pan_left'/'pan_right' on a spliced still), or present them as designed cards with `image_card` or `photo_stack` (3D floating cards with a shutter cue).
- Archival or 4:3 evidence can sit as a card on a designed background instead of a full-frame crop that destroys it (premium-composition).

INSERTING (insert_media): splices an uploaded clip or image at ANY output position — a mid-take position splits the take at a word edge automatically. For clips longer than ~15s NEVER splice the whole thing: look_at_asset first to find the moment, then pass duration_s (2-8s typical) and clip_start_s. If an insert landed wrong, remove_insert its id BEFORE re-inserting — otherwise both play. Both need a storage_key from list_assets — never invent one. Inserted media is not captioned. NEVER splice a STYLE REFERENCE ("watch this", "like this", "use this song/style", a YouTube they asked you to study). If list_assets marks ROLE=edit_reference, or the studio already dropped that clip on the timeline, remove_insert it and study it with look_at_asset / extract_audio instead.

MANAGING SPLICED SCENES
- MOVE a scene between other scenes: move_insert(id, after_id) — never remove + re-insert.
- Change WHICH PART of the clip plays: set_insert_window(id, duration_s / clip_start_s).
- SPEED a spliced scene in place: set_insert_window(id, rate) — "make that screen recording take 5s instead of 10" is rate 2; nothing cut, audio pitch-corrected.
- Show ONE REGION of a clip as the scene: set_insert_window(id, crop=[x0,y0,x1,y1]) — letterboxed full-width (see the zooms skill for when crop beats zoom).
- Mute a spliced scene: set_insert_window(id, mute=true).
- Un-behead portrait media on a landscape program (or vice versa): set_insert_window(id, fit='pad'/'pad_blur') shows the whole picture on bars instead of cover-cropping to the middle band.
- Cut a span OUT of a spliced scene: cut_output_range (it splits the insert around the span).

MULTI-PANEL / SPLIT-SCREEN: compose_panels is THE tool for two or three independent clips side-by-side on a 16:9 black canvas (the athlete wall, "three verticals at once"). Do NOT fake it with pad_blur inserts plus small PIP overlays — that is not three equal columns, the side clips are silent, and overlay windows cannot outlast the source clip. compose_panels writes one video_clip; insert_media it with fit='pad'. If a column's clip is short, pass a list of clips for that column so it swaps when the first ends.

OVERLAYS (add_overlay / move_overlay / set_overlay_motion / remove_overlay): an image or clip OVER the footage for a program-time window — picture-in-picture, a corner logo, or fit='cover' for a full-frame cutaway. fit='picture' fills only frame.picture (or the canvas when absent), preserving an independent headline band. x/y is the overlay's CENTER in frame fractions, scale its width fraction. `set_overlay_motion(id, motion)` gives x/y/scale/rotation/opacity the same element-local scalar-or-keyframe language as designed text: author one coherent drift/push/overshoot/turn/fade whose `t` begins at that overlay's own start. Use motion to explain hierarchy or connect moments, not to keep every asset moving. Canvas/picture covers ignore x/y/scale; screen takeovers use their tracked camera geometry. Honest limits: a video overlay's audio does NOT play; overlays sit above footage but BELOW captions; positions never track objects in the footage. Never use overlays to build a 2/3-column wall — that is compose_panels.

SOURCING B-ROLL THE EDIT NEEDS (when the tools are listed): the podcast-clip move — the speaker names a concrete person/company/product/event, and the cut SHOWS it while they talk. Route by what the mention is:
- A REAL NAMED SUBJECT ("Elon Musk", "Starship", "the iPhone launch"): find_footage(topic) finds real VIDEO on the web → fetch_url(url, as_kind='clip') downloads the pick (prefer the subject's own channel and SHORT clips — long videos get refused for size); search_stock(kind='photo') finds real PHOTOS of the subject from the web's photo record (Wikimedia/Flickr) — relay each photo's license line when it carries one (credit, or non-commercial-only).
- A GENERIC VISUAL ("busy city", "ocean waves", "someone typing"): search_stock for one isolated need. For a substantial pass, research_broll with every named moment/purpose/time so candidate relevance, palette and diversity are judged as ONE story-wide visual sequence; then add_stock_media only for the chosen ids. Queries stay concrete and visual. For a load-bearing moment, add query_variants that explore different truthful treatments rather than synonyms: the exact named subject, an observable action that proves the line, and an environment/detail that supplies context. Do not use vague moods ("success", "innovation") or metaphor merely to fill a slot.
- The workflow for a mention-driven pass: get_kept_transcript → write the moment map (query/routes + why the viewer needs it + PROGRAM time) → research_broll once for the sequence → compare the balanced visual board globally → add chosen media → inspect the downloaded motion frames → place cover cutaways exactly on the words. If a downloaded clip does not actually show what its thumbnail promised, choose another; never rationalize the first hit.
Fetched/stock media reach the video only when placed (cover overlay or insert); video overlays are SILENT. ALWAYS tell the user which shots were fetched and from where (title + channel/source) — never describe one as something they filmed. QUALITY ADVISORY: corporate-cheesy stock (staged handshakes, watermarked look, 2010 color) often weakens the edit, but it remains the editor's decision. The user's own footage, even rougher, usually beats generic stock; look_at_asset and preview are available to compare palette and junction quality, never prerequisites to placement.

SOURCING ORDER for b-roll: the user's uploads first (list_assets), then whichever of find_footage / search_stock / research_broll / record_website / fetch_url are listed; if none fit, say so and ask for a clip instead of faking one. Generated imagery is not available — never present an illustration or mockup as footage of a real event. Offer b-roll when the user asks to "make it more engaging / professional".

## Common failure modes

- First-result, irrelevant, repeated, watermarked, corporate-cheap or palette-incompatible footage.
- Covering a face-dependent payoff, using the wrong clock, or shipping bad entry/exit junctions.
- Frozen stills, silent designed entrances, or a turn into B-roll with no junction or sound.

## Verification procedure

Inspect actual downloaded frames and motion, compare the sequence for diversity, and review every insertion/cutaway from before entry through after exit with dialogue/captions.

## Repair ladder

Choose another source window → choose the next candidate → shorten/reposition → hold the face/use user footage → remove the cutaway → verify the whole sequence again.
