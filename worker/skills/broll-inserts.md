# broll-inserts — cutaways vs splices, inserting media, moving scenes, stock footage

THE CHOICE: does the voice keep going?
- B-ROLL / CUTAWAY (the most human editing move): the speaker mentions something concrete — SHOW it while their voice keeps going. add_overlay(fit='cover', start, duration_s 2-6) switches the PICTURE while the program's audio and captions keep running. Placement: get_kept_transcript gives each sentence's PROGRAM time — start the cover ON the words that mention the thing.
- SPLICE (insert_media): PAUSES the program and adds time. Right for "add this clip at the end", "put it between the scenes", a beat between sentences.
- Taste: 1-3 purposeful cutaways per minute beat wall-to-wall covers; never cover a punchline delivered face-on; tell the user exactly which moments you covered and with what.

INSERTING (insert_media): splices an uploaded clip or image at ANY output position — a mid-take position splits the take at a word edge automatically. For clips longer than ~15s NEVER splice the whole thing: look_at_asset first to find the moment, then pass duration_s (2-8s typical) and clip_start_s. If an insert landed wrong, remove_insert its id BEFORE re-inserting — otherwise both play. Both need a storage_key from list_assets — never invent one. Inserted media is not captioned.

MANAGING SPLICED SCENES
- MOVE a scene between other scenes: move_insert(id, after_id) — never remove + re-insert.
- Change WHICH PART of the clip plays: set_insert_window(id, duration_s / clip_start_s).
- SPEED a spliced scene in place: set_insert_window(id, rate) — "make that screen recording take 5s instead of 10" is rate 2; nothing cut, audio pitch-corrected.
- Show ONE REGION of a clip as the scene: set_insert_window(id, crop=[x0,y0,x1,y1]) — letterboxed full-width (see the zooms skill for when crop beats zoom).
- Mute a spliced scene: set_insert_window(id, mute=true).
- Un-behead portrait media on a landscape program (or vice versa): set_insert_window(id, fit='pad'/'pad_blur') shows the whole picture on bars instead of cover-cropping to the middle band.
- Cut a span OUT of a spliced scene: cut_output_range (it splits the insert around the span).

OVERLAYS (add_overlay / move_overlay / remove_overlay): an image or clip OVER the footage for a program-time window — picture-in-picture, a corner logo, or fit='cover' for a full-frame cutaway. x/y is the overlay's CENTER in frame fractions (keyframable for a slow drift), scale its width fraction. Honest limits: a video overlay's audio does NOT play; overlays sit above footage but BELOW captions; positions never track objects in the footage.

STOCK B-ROLL (search_stock + add_stock_media — only when listed in CAPABILITIES): footage the user does not have. search_stock returns CANDIDATES — read the descriptions, pick the one that actually depicts the subject, then add_stock_media(id). Queries short and VISUAL ("city traffic at night", not "the pace of modern life"). Stock clips are SILENT and reach the video only when placed (cover overlay or insert). ALWAYS tell the user which shots came from a stock library — never describe one as something they filmed. QUALITY GATE: reject the corporate-cheesy shot (staged handshakes, watermarked look, 2010 color) — the user's own footage, even rougher, usually beats generic stock; look_at_asset the candidate before placing it, and after placing CHECK the junction on the result sheet — an insert whose color/light obviously clashes with the footage reads as pasted; prefer a candidate whose palette sits closer, or note the clash to the user.

SOURCING ORDER for b-roll: the user's uploads first (list_assets), then whichever of record_website / fetch_url / generate_video / generate_image are in CAPABILITIES; if none fit, say so and ask for a clip instead of faking one. Offer b-roll when the user asks to "make it more engaging / professional".
