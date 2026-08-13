# broll-inserts — cutaways vs splices, inserting media, moving scenes, stock footage

THE CHOICE: does the voice keep going?
- B-ROLL / CUTAWAY (the most human editing move): the speaker mentions something concrete — SHOW it while their voice keeps going. add_overlay(fit='cover', start, duration_s 2-6) switches the PICTURE while the program's audio and captions keep running. Placement: get_kept_transcript gives each sentence's PROGRAM time — start the cover ON the words that mention the thing.
- SPLICE (insert_media): PAUSES the program and adds time. Right for "add this clip at the end", "put it between the scenes", a beat between sentences.
- Taste: 1-3 purposeful cutaways per minute beat wall-to-wall covers; never cover a punchline delivered face-on; tell the user exactly which moments you covered and with what.

INSERTING (insert_media): splices an uploaded clip or image at ANY output position — a mid-take position splits the take at a word edge automatically. For clips longer than ~15s NEVER splice the whole thing: look_at_asset first to find the moment, then pass duration_s (2-8s typical) and clip_start_s. If an insert landed wrong, remove_insert its id BEFORE re-inserting — otherwise both play. Both need a storage_key from list_assets — never invent one. Inserted media is not captioned. NEVER splice a STYLE REFERENCE ("watch this", "like this", "use this song/style", a YouTube they asked you to study). If list_assets marks ROLE=edit_reference, or the studio already dropped that clip on the timeline, remove_insert it and study it with look_at_asset / extract_audio instead.

MANAGING SPLICED SCENES
- MOVE a scene between other scenes: move_insert(id, after_id) — never remove + re-insert.
- Change WHICH PART of the clip plays: set_insert_window(id, duration_s / clip_start_s).
- SPEED a spliced scene in place: set_insert_window(id, rate) — "make that screen recording take 5s instead of 10" is rate 2; nothing cut, audio pitch-corrected.
- Show ONE REGION of a clip as the scene: set_insert_window(id, crop=[x0,y0,x1,y1]) — letterboxed full-width (see the zooms skill for when crop beats zoom).
- Mute a spliced scene: set_insert_window(id, mute=true).
- Un-behead portrait media on a landscape program (or vice versa): set_insert_window(id, fit='pad'/'pad_blur') shows the whole picture on bars instead of cover-cropping to the middle band.
- Cut a span OUT of a spliced scene: cut_output_range (it splits the insert around the span).

OVERLAYS (add_overlay / move_overlay / remove_overlay): an image or clip OVER the footage for a program-time window — picture-in-picture, a corner logo, or fit='cover' for a full-frame cutaway. x/y is the overlay's CENTER in frame fractions (keyframable for a slow drift), scale its width fraction. Honest limits: a video overlay's audio does NOT play; overlays sit above footage but BELOW captions; positions never track objects in the footage.

SOURCING B-ROLL THE EDIT NEEDS (when the tools are listed): the podcast-clip move — the speaker names a concrete person/company/product/event, and the cut SHOWS it while they talk. Route by what the mention is:
- A REAL NAMED SUBJECT ("Elon Musk", "Starship", "the iPhone launch"): find_footage(topic) finds real VIDEO on the web → fetch_url(url, as_kind='clip') downloads the pick (prefer the subject's own channel and SHORT clips — long videos get refused for size); search_stock(kind='photo') finds real PHOTOS of the subject from the web's photo record (Wikimedia/Flickr) — relay each photo's license line when it carries one (credit, or non-commercial-only).
- A GENERIC VISUAL ("busy city", "ocean waves", "someone typing"): search_stock — read the candidate descriptions, pick the one that actually depicts the subject, then add_stock_media(id). Queries short and VISUAL.
- The workflow for a mention-driven pass: get_kept_transcript for each mention's PROGRAM time → source the shot → look_at_asset to find the exact seconds worth showing → 2-6s cover cutaway ON the words (or insert_media). 1-3 cutaways per minute; never cover a punchline delivered face-on.
Fetched/stock media reach the video only when placed (cover overlay or insert); video overlays are SILENT. ALWAYS tell the user which shots were fetched and from where (title + channel/source) — never describe one as something they filmed. QUALITY ADVISORY: corporate-cheesy stock (staged handshakes, watermarked look, 2010 color) often weakens the edit, but it remains the editor's decision. The user's own footage, even rougher, usually beats generic stock; look_at_asset and preview are available to compare palette and junction quality, never prerequisites to placement.

SOURCING ORDER for b-roll: the user's uploads first (list_assets), then whichever of record_website / fetch_url / generate_video / generate_image are in CAPABILITIES; if none fit, say so and ask for a clip instead of faking one. Offer b-roll when the user asks to "make it more engaging / professional".
