# beats-emphasis — audio analysis, beat-aligned cuts, punch-ins, sound design pass

MEASURE FIRST (get_audio_analysis): tempo with a confidence score, the beat grid, energy peaks/rises, the most vocally stressed words. Pass asset_key to measure a SONG instead; when that song is already in the edit it also prints beat times in PROGRAM seconds, ready for keep_segments / add_sfx / add_zoom.

BEAT-ALIGNED CUTS (beat_align_cuts): snaps internal cut points to the beat — when the edit has music it cuts to the SONG the viewer hears, not the footage's own noise (that is what "cut to the beat" means). A low-confidence detected grid is committed with a quality advisory rather than used as a permission gate.
- If the USER tells you the tempo ("there's a beat every second", "it's 120 BPM"): pass every_s=1 / bpm=120 — their ears beat our estimator; refusing after they told you is the wrong answer.
- It MOVES existing cuts, never creates them: for "cut it on every beat", build the spans with keep_segments from the beat times first, then snap.
- If a track measures as no-pulse AND the analysis warns the file is flat-lined/broken, say so and ask for a re-upload — do not keep scoring against a dead file.

PUNCH-INS (punch_in_on_emphasis): writes one coherent emphasis-motion pass. Omit count/strength to let program length + the creative motion brief determine a restrained density and varied magnitude; selection combines real vocal stress, semantic weight and timeline spacing so adjacent loud words do not become adjacent camera bumps. Explicit count/strength remain available. suggest_emphasis lists candidates without writing.

SOUND DESIGN BY HAND: fetch sounds (search_sfx/fetch_sfx — a whoosh for junctions, an impact for the strongest word, a riser into the biggest rise) and place each with add_sfx where it serves the edit. Spacing and the audio skill are taste guidance, not hard authorization. Use the render, measured timing, AUDIO CHECK, or your judgment to refine anything that misses its moment.

Every one of these writes CONCRETE timestamps into the EDL — any number you quote must come from the tool result. That does NOT mean quoting them all: the timestamps are on the timeline where the user can see them; a reply that lists every sfx is a receipt, not a report.

MUSIC-LED FORMATS: in a montage/gameplay/music piece the music IS the structure — cut on its beats, build to the peak, one slow-motion beat on the single best moment, HARD cuts (see the formats skill).
