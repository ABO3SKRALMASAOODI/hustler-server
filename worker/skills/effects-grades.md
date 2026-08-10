# effects-grades — color, stylize, enhancement, speed, restraint

RESTRAINT IS THE LOOK. Every device must earn a reason a viewer would notice. ONE aggressive full-frame device per video (a corrupt screen OR flash transitions OR chromatic bursts OR a colour-flash), at most two finishing passes overall. Grain + vignette + glow + chroma + shake is not a look, it is a fault. On a video under ~20s the same device landing twice within seconds reads as broken footage.

"MAKE IT PUNCHY / EXCITING / VIRAL" IS NOT PERMISSION TO STACK EVERY DEVICE. Pace, sound and framing carry a punchy edit: tighter cuts, a punch-in on the emphasis, a bold caption preset. Pick AT MOST ONE aggressive device on top. If you think it needs a second, that is exactly when to ask_user.

WHEN THE USER LISTS SEVERAL DEVICES ("zoom + flash + shake + glow + speed ramp"), THEY ARE ASKING FOR SEVERAL MOMENTS — a shot list, not a stack. Give each device the beat that earns it and let the rest breathe. Firing three into the same half-second delivers one blown-out instant on an otherwise untouched video.

GRADES (set_color_grade: vibrant, warm, cool, bw, vintage, cinematic) — choose FOR the footage: 'cinematic' crushes and desaturates — right for night, drama, moody interiors; wrong for a bright kitchen, food, a sunny gym, a colourful product. 'vibrant' for anything that should look alive, 'warm' for skin and interiors, 'cool' for tech and rain, 'bw'/'vintage' only when asked. A UI/screen recording takes NO grade — it is meant to look like itself.
- set_grade_custom is continuous control (exposure/contrast/saturation/temperature/tint) applied AFTER the preset — 'cinematic but warmer' = preset cinematic + temperature 0.2.

PICTURE QUALITY IS NOT A LOOK. "Make it clearer / sharper / better quality / HD" means enhance_video (sharpen + optional denoise), NOT a grade or contrast bump — and if a grade is already on when they ask for "no filters, just clearer", take the grade off in the same turn. Be straight that it recovers detail, not resolution: 480p stays 480p.

STYLIZE (add_stylize): grain, vignette, glow, chromatic, dream_blur, vhs, flash, shake, stabilize, motion_blur — windowed, intensity 0-1. One or two read as a look; five read as a broken TV. 'stabilize' smooths handheld wobble (crops a few percent; cannot fix a whip or a walk). 'motion_blur' adds real blur on movement.

WRITE YOUR OWN CHAIN (add_custom_filter) — when the user asks for a look NO preset makes (CRT phosphor, posterize, thermal, selective hue rotation, a duotone), write the ffmpeg chain yourself instead of refusing or faking it with the nearest preset:
- ONE chain on the single video stream: filters separated by commas. No ';', no '[labels]' (that is graph syntax — the renderer owns the graph), no file access, and the chain must return the same frame size and rate it receives (reframing is set_frame's job).
- It dry-runs on the real footage BEFORE it stores: a broken chain returns ffmpeg's own error naming the filter at fault — fix the chain, never resend the identical string. An over-heavy chain returns its measured cost — drop the heaviest filter or narrow the window.
- start/end are program seconds and the moment follows its footage through later cuts, exactly like stylize. Give it a short label ('CRT green') — that is what the user sees in the edit summary.
- A chain that parses can still look wrong, and only your eyes can tell: after the next preview, look_at output frames inside the window before you describe the effect. Not the look you meant → remove_custom_filter and write a better chain.
- RESTRAINT APPLIES UNCHANGED: a custom chain counts as your one aggressive device. Presets first — this tool is for looks they cannot say, not a second way to stack grain.
- RECOLOR ONE THING ("make the leaves red", "turn the sky purple", "the car but in black") — a real ask an agent reverted out of on 2026-08-09; the recipe exists, use it: `huesaturation` rotates hue for ONE named color range and holds everything else. Greens toward red/purple: `huesaturation=hue=120:colors=g:strength=8` (hue -180..180 picks the target shade, colors is r/y/g/c/b/m, strength raises selectivity). Desaturate just the car's blues: `huesaturation=saturation=-1:colors=b:strength=10`. Iterate with your eyes: apply → preview → look_at a frame WITH the object and one WITHOUT — the object must shift, skin/road/sky must not. Two failed iterations → say plainly which colors bleed together in this footage (green leaves against green grass cannot be separated) instead of quietly reverting.

SPEED (set_speed / remove_speed): 0.25x-4x on a SOURCE range; audio keeps pitch; everything on the program timeline re-anchors automatically. Slow motion below 0.6x visibly steps (frames are duplicated, not synthesized) — prefer 0.6-0.8x and say the tradeoff. An overlapping span replaces the old one.

LOOKS (apply_look): one call composes a whole aesthetic and reports each component — 'hype' (beast xl captions, vibrant, zoom_punch), 'clean' (podcast captions, ungraded, gentle fades), 'cinematic' (elegant captions, cinematic grade + warmth, dip_black), 'luxury' (luxe captions, warm), 'meme' (impact xl captions, flash cuts, grain). It never touches cuts, music or sfx; refine components with their own tools after.

FADES (set_fades): fade from/to black at the very start/end. A closing fade belongs on long-form and cinematic pieces only — on a reel it plays into the loop. A 1s fade-in on a reel is a second of black at the only moment retention is decided.

RHYTHM (short-form): a pattern interrupt roughly every 4-6 seconds of talking — a punch-in, a b-roll beat, a freeze-frame, a text card — ONE device at a time, chosen by what the sentence is doing.
