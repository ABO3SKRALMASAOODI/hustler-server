# formats — name the format first, then edit for THAT format

## Editorial decision principles

Choose the format from footage, audience, platform and promise—not from a reusable effect bundle. Hybrid formats are valid when their relationships are explicit.

## Evidence to inspect

Inspect duration, source grammar, transcript arc, performance, shot variety, platform constraints, references and the user's actual objective.

## Strong treatment patterns

FIRST, READ THE FOOTAGE AND NAME THE FORMAT before touching the EDL. Aspect + duration + words-per-minute + the filmstrip already tell you what this is. The fastest way to look like a machine is to give a plant timelapse, a gameplay montage and a preacher the same cinematic-grade-plus-vignette-plus-punch-zooms treatment.

For a whole-program creative build, use the blueprint's `sequence_map` to design the viewer's journey before decorating it. Each meaningful beat names the exact phrase/scene/card anchor, why it exists, what the picture should contribute, what sound or deliberate silence should contribute, and its relative energy. This is not a cut/effect quota: one restrained podcast beat may remain a clean face and voice, while a product proof beat may earn a cutaway, motion and SFX. The point is that every department serves the same beat and the beats form an intentional progression.

Source time is file-local. Omit `source_asset_key` when a beat cites the main upload. When it cites an uploaded clip, use that clip's exact `storage_key`, its CLIP seconds, and exact sentence/shot IDs from `get_editorial_map(asset_key=...)`. Filenames and phrases such as "shot 2" are not IDs, and auxiliary seconds must never be interpreted as main-video seconds.

When several uploaded clips or images may contribute, use `compare_uploaded_media` once with all relevant storage keys. Read every comparison page before casting the order; choose exact moments for their distinct story purpose and leave weak/redundant media unused. Do not inspect the same library through one sequential `look_at_asset` call per file.

WHAT AN ELITE EDIT LOOKS LIKE, BY FORMAT:
- Talking-head reel: hook first, cut measured filler sounds and dead pauses, premium captions with keyword emphasis, punch-ins on the argument's turns, purposeful b-roll or freeze-frame beats, clean joins, ends on the last word. For a broad polished/professional social brief, `remove_filler_words` and `set_master_loudness` are useful first-pass options. Music is optional; choose it from the brief, metadata, measured evidence and judgment without a keyword permission gate.
- Sermon / speech / motivational: same spine, plus a freeze-frame "pearl" (add_freeze_frame with blur + darken + a big line) on the two or three strongest sentences, captions muted under the pearl, and correct spelling of every name (set_caption_fixes). 'spotlight' single-word captions fit when the delivery is punchy.
- Screen recording / product demo: pad_blur or a screen frame, cursor enhanced, one travelling zoom that follows the action (add_zoom_path), dead loading time cut; usually preserve the UI's native color rather than imposing a cinematic grade. Click sounds are an editorial option; measured real clicks are the strongest timing source, but the editor may choose them without a keyword grant.
- Montage / gameplay / sports: the music IS the structure. Cut on its beats, build to the peak, use slow motion where it earns emphasis, and prefer HARD cuts over an effect on every join. Sound design is an editorial option, not a keyword-gated capability. A Short / 9:16 / TikTok / crop brief FILLS the phone (mode='crop'); only pad_blur when they asked to keep the HUD or the whole frame.
- Music video / performance: cut on the phrase, not the bar line; speed ramps into the energy rises; keep the artist's face on frame at the chorus; no captions unless asked.
- Timelapse / nature / architecture: slow eased moves only, no punches, no whips, no captions, let shots breathe 3-5s, music leads.
- Vlog / lifestyle: keep the personality — jump cuts are fine, warm grade, captions optional, energy over polish.
- Podcast / interview clip: find the ONE self-contained exchange with a hook line, including the nearby question/setup when the answer needs it; open mid-energy and keep the resolution. Use 'podcast' or 'karaoke' captions in a measured clear band, cut word-safe filler sounds and genuinely dead pauses, and master social delivery in the first build. Clean is the luxury look: transitions/SFX are normally unnecessary, but a genuinely earned section turn may use one rather than obeying a blanket ban. Music optional around -20dB or absent. Preserve natural hesitations when the user asks for raw/uncut delivery.

Deliberately breaking one of these is fine — say why in your reply. What is never fine is applying the same six tools to every video and calling it an edit. For anything meant to get views, the opening and ending have their own craft — read_skill hooks-retention; for anything with music, read_skill music.

## Common failure modes

- Choosing a format from platform labels alone or applying the same caption/zoom/B-roll/SFX bundle to every source.
- Mixing visual languages without a dominant spine.

## Verification procedure

Check the blueprint against source evidence, then screen whether opening, pacing, hierarchy, sound and ending behave like the chosen format without formulaic filler.

## Repair ladder

Recast the format → simplify to one spine → revise department relationships → remove unsupported devices → verify the full treatment.
