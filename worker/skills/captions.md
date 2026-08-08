# captions — presets, families, placement, composition, fixes, pre-captioned footage

BASICS
- add_captions("from_transcript") burns word-timed captions for everything that survives the cut — timing always from the real transcript, never invented. add_captions('off') removes captions WE added.
- To change how EXISTING captions look ("make it red", "move to the top"), use set_caption_style with just the fields to change — never re-add.
- Manual caption items are only for text the user dictates.

PRESET FAMILIES — default to a premium preset whenever the user asks for captions without specifying a plain look. Choose by the video:
- Fast, punchy, hype, motivational → 'spotlight': ONE glowing word at a time, big caps, dead centre — the modern single-word look.
- Music/lyric edits, montage reels, "lyrics on screen", motivational quote-over-footage → 'lyric': heavy lowercase phrases dead CENTRE, words landing as spoken, and the stressed word ~2x in a white ITALIC SERIF on its own line ("we gotta be / excited"). Gold accent tints occasional whole phrases. Mid-frame is this look's signature (it and 'spotlight' are the only two allowed there).
- Talking-head reels, podcasts, interviews, education → a DYNAMIC multi-word preset at the BOTTOM: 'podcast' (bold white words land as spoken, keywords glow/box/serif-italic, numbers HUGE — the safe default for reels), 'beast' (loud ALL-CAPS Anton impact — hype), 'karaoke' (accent box tracks each word — clean), 'elegant' (calm serif lower third — interviews, luxury, education).
- Luxury/fashion/editorial footage → 'luxe' (Playfair serif, gold accents), 'editorial' (light Instrument Serif, airy), 'fashion' (wide Archivo caps).
- STACK presets compose the phrase across independently-placed lines whose SIZES differ hard (small connector above a huge hero word): 'stacked' (flagship, all-white, emphasis is pure SIZE), 'iridescent' (RGB fringe), 'chrome' (liquid metal), 'impact' (Bebas caps, sports/hype).
- 'classic' = plain legacy look, only when asked for simple/plain.

PLACEMENT LAW: multi-word captions sit in the BOTTOM area, never across the face (presets default there — do not pass position='middle' for them). Only 'spotlight' (one word at a time), 'lyric' (the mixed-face lyric edit — centre IS the look), or max_words_per_caption=1 may hold the centre.

EMPHASIS: when enabling ANY preset, ALSO pass emphasis_words — read the transcript and pick 10-25 impact words VERBATIM as spoken (numbers, money, outcomes, superlatives, emotional peaks, names — roughly 1-2 per sentence). Words containing digits are emphasized automatically.

COMPOSITION — every preset is a starting point you can override per field:
- font: exact bundled family name (Inter Display Black/ExtraBold/Bold, Anton, Bebas Neue, Archivo Black, Poppins Black, Syne ExtraBold, Playfair Display Black, Instrument Serif, DM Serif Display, Montserrat). Honour a specific font request instead of deflecting.
- emphasis: 'big' (size only — the reference look), 'accent'/'pop' (colour too), 'box' (marker highlight), 'serif' (accented serif-italic), 'script' (WHITE italic display serif — the lyric-edit hero word), 'chrome'/'glow'/'chroma' (layered effects). emphasis_scale 1.0-3.0 (2.0+ is the dramatic reel look).
- layout 'stack' turns ANY preset into the per-line composer; leading below 1.0 makes lines deliberately OVERLAP (0.85-0.95 is the interlock sweet spot).
- animation (entrance): fade, pop, punch, blur_in, whip, flash, rise, drop. highlight_color sets the accent. uppercase and position override preset defaults.
- Non-preset styling: color (#RRGGBB), size (s/m/l/xl), size_scale (0.5-3.0), position (bottom/top/middle), dynamic (legacy karaoke), max_words_per_caption (1-16).

SIZE COMPLAINTS: "too small" / "big TikTok captions" → with a preset go size 'l' or 'xl'; without one, size 'xl' + dynamic:true. If they say "too small" a second time they mean MUCH bigger. "Captions look basic/boring/cheap" → switch to 'podcast' (or 'beast' for hype) with fresh emphasis_words — do not just bump size.

READABILITY IS THE CRAFT — the details that separate produced captions from burned subtitles:
- SHORT GROUPS READ, SENTENCES DON'T: modern short-form runs 1-4 words on screen at a time — max_words_per_caption 3-4 for reels (the dynamic presets already chunk); a full sentence at once is the documentary look, right only for 'elegant'/'editorial' calm.
- CONTRAST IS NON-NEGOTIABLE: white text dies on a bright sky; check the verify frames at 2-3 caption moments — if a caption fights its background, add the preset's box/glow emphasis, move position, or pick the frame's clear zone. Never ship a caption you haven't seen against its actual background.
- EMPHASIS WORDS ARE THE MESSAGE: pick the 1-2 words per sentence that carry the meaning (numbers, names, the verb that lands) — not random nouns. Wrong emphasis reads worse than none.
- NEVER over a face's mouth/eyes, never under the platform UI band (the bottom ~13% on 9:16), never covering the thing the speaker is pointing at.

CAPTIONS OFF FOR PART OF THE VIDEO: set_caption_mutes(spans=[[start,end],...]) in PROGRAM seconds — replaces the whole list; spans=[] turns all back on. Whole caption lines vanish (a line running into a window disappears entirely), so keep windows tight. Inserted media and title cards are never captioned — no mute needed there.

SPELLING: set_caption_fixes corrects the spelling/capitalization of burned captions without touching timing ('dios' → 'Dios', a misheard name → the right one). Always fix names in sermons/interviews.

PRE-CAPTIONED FOOTAGE (the most common request on footage the user did not shoot): when the filmstrip shows captions burned into the source, or the user says so — NEVER silently burn new captions on top; stacked caption soup is the #1 "this looks broken" complaint. The answer is REMOVAL: erase_burned_text() finds every burned caption band and repaints those pixels, then add_captions puts your own on a clear frame. That is also the answer to "change the caption font" / "make the subtitles bigger" on burned-in text — you are removing theirs and writing new ones from the transcript; say exactly that. Fallbacks when the erase measurement says ink survived OR your eyes say it ghosts — animated/boxed caption bands can ghost even when ink measures gone, so always look_at(output_times=[...]) inside the erased window on the next preview: cover the band (blur_region), crop it out (auto_reframe / set_frame) if it hugs an edge, or place new captions elsewhere (position 'top'). Re-erasing the same band with a nudged rectangle never improves quality — escalate a rung instead. And style the NEW captions so they cannot be mistaken for the old ones: if the burned captions were yellow boxes, do not pick a yellow highlight — the user cannot tell your emphasis pops from the ghosts they asked you to remove (project 382, 2026-08-07).
"Remove the captions": get_edl FIRST and say which case theirs is — captions WE added turn off with add_captions('off') (or set_caption_mutes for stretches); captions burned into the footage are the erase_burned_text case.
