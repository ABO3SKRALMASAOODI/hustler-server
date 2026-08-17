# effects-grades — color, stylize, enhancement, speed, restraint

## Editorial decision principles

Treat effects and grade as a coherent visual language. Use them to clarify mood, hierarchy or a real story turn; restraint is a valid complete treatment.

## Evidence to inspect

Inspect representative frames across every scene, skin tones, white balance, exposure, palette, motion energy, source defects and the rendered result at effect boundaries.

## Strong treatment patterns

RESTRAINT OFTEN IS THE LOOK. Every device should earn a reason a viewer would notice. A single aggressive full-frame device and a small finishing palette are often enough, while grain + vignette + glow + chroma + shake can read as a fault. These are taste heuristics, not limits: use any number of devices or finishing passes when the brief and result support them.

"MAKE IT PUNCHY / EXCITING / VIRAL" can be carried by pace, sound and framing: tighter cuts, punch-ins on emphasis, and bold caption rhythm. Aggressive devices are available whenever your judgment says they strengthen the edit; preview their combined result rather than following a fixed count.

WHEN THE USER LISTS SEVERAL DEVICES ("zoom + flash + shake + glow + speed ramp"), THEY ARE ASKING FOR SEVERAL MOMENTS — a shot list, not a stack. Give each device the beat that earns it and let the rest breathe. Firing three into the same half-second delivers one blown-out instant on an otherwise untouched video.

GRADES (set_color_grade: vibrant, warm, cool, bw, vintage, cinematic) — choose FOR the footage: 'cinematic' crushes and desaturates — right for night, drama, moody interiors; wrong for a bright kitchen, food, a sunny gym, a colourful product. 'vibrant' for anything that should look alive, 'warm' for skin and interiors, 'cool' for tech and rain, 'bw'/'vintage' only when asked. A UI/screen recording takes NO grade — it is meant to look like itself.
- set_grade_custom is continuous control applied AFTER the preset. Exposure,
  temperature, tint, shadows and highlights use 0 as neutral. Contrast and
  saturation accept SMALL SIGNED DELTAS too: `contrast=0.08` means 1.08x,
  `saturation=-0.08` means 0.92x, and **0 clears the axis**. An explicit final
  multiplier >=0.5 still works (`saturation=1.15`). Prefer signed deltas for
  natural corrections. For monochrome use the explicit `bw` preset; never try
  to restore ORIGINAL colour with contrast/saturation zero and then compensate
  with another filter.

PICTURE QUALITY IS NOT A LOOK. "Make it clearer / sharper / better quality / HD" means enhance_video (sharpen + optional denoise), NOT a grade or contrast bump — and if a grade is already on when they ask for "no filters, just clearer", take the grade off in the same turn. Be straight that it recovers detail, not resolution: 480p stays 480p.

STYLIZE (add_stylize): grain, vignette, glow, chromatic, dream_blur, vhs, flash, shake, stabilize, motion_blur — windowed, intensity 0-1. One or two read as a look; five read as a broken TV. 'stabilize' smooths handheld wobble (crops a few percent; cannot fix a whip or a walk). 'motion_blur' adds real blur on movement.

WRITE YOUR OWN CHAIN (add_custom_filter) — when the user asks for a look NO preset makes (CRT phosphor, posterize, thermal, selective hue rotation, a duotone), write the ffmpeg chain yourself instead of refusing or faking it with the nearest preset:
- ONE chain on the single video stream: filters separated by commas. No ';', no '[labels]' (that is graph syntax — the renderer owns the graph), no file access, and the chain must return the same frame size and rate it receives (reframing is set_frame's job).
- It dry-runs on the real footage BEFORE it stores: a broken chain returns ffmpeg's own error naming the filter at fault — fix the chain, never resend the identical string. An over-heavy chain returns its measured cost — drop the heaviest filter or narrow the window.
- start/end are program seconds and the moment follows its footage through later cuts, exactly like stylize. Give it a short label ('CRT green') — that is what the user sees in the edit summary.
- A chain that parses can still look wrong, and only your eyes can tell: after the next preview, look_at output frames inside the window before you describe the effect. Not the look you meant → remove_custom_filter and write a better chain.
- A custom chain participates in the same visual palette as every other effect. Presets are a fast starting point, while custom chains remain available for combinations or looks they cannot express; judge the composite result instead of counting devices.
- RECOLOR ONE THING ("make the leaves red", "turn the sky purple", "the car but in black") — the recipe exists, use it: `huesaturation` rotates hue for ONE named color range and holds everything else. Greens toward red/purple: `huesaturation=hue=120:colors=g:strength=8` (hue -180..180 picks the target shade, colors is r/y/g/c/b/m, strength raises selectivity). Desaturate just the car's blues: `huesaturation=saturation=-1:colors=b:strength=10`. Iterate with your eyes as much as useful: apply → preview → look_at a frame WITH the object and one WITHOUT. If colors cannot be separated cleanly in this footage, say plainly what bleeds together instead of quietly reverting.

SPEED (set_speed / remove_speed): 0.25x-4x on a SOURCE range; audio keeps pitch; everything on the program timeline re-anchors automatically. Slow motion below 0.6x visibly steps (frames are duplicated, not synthesized) — prefer 0.6-0.8x and say the tradeoff. An overlapping span replaces the old one.

LOOKS (apply_look): one call composes a whole aesthetic and reports each component — 'hype' (beast xl captions, vibrant, zoom_punch), 'clean' (podcast captions, ungraded, gentle fades), 'cinematic' (elegant captions, cinematic grade + warmth, dip_black), 'luxury' (luxe captions, warm), 'meme' (impact xl captions, flash cuts, grain). It never touches cuts, music or sfx; refine components with their own tools after.

FADES (set_fades): fade from/to black at the very start/end. A closing fade belongs on long-form and cinematic pieces only — on a reel it plays into the loop. A 1s fade-in on a reel is a second of black at the only moment retention is decided.

RHYTHM (short-form): reserve pattern interrupts for meaningful changes in information, emotion, speaker energy, musical phrase or visual legibility. A punch-in, B-roll beat, freeze-frame or text card should make that change clearer—not merely reset a timer. Let strong faces, reactions and suspense hold; compress lists or escalations when their internal rhythm earns it. ONE dominant device at a time, chosen by what the moment is doing.

## Common failure modes

- Repetitive pattern interrupts, crushed highlights/blacks, damaged skin tones, scene-inconsistent grade or effects masking information.

## Verification procedure

Review representative frames from every distinct scene plus all effect boundaries and motion windows; compare subject, skin, text/UI and palette continuity.

## Repair ladder

Reduce intensity → scope to the intended scene/color → correct custom controls → choose a restrained preset → remove the effect → render every affected scene again.
