# beats-emphasis — audio analysis, beat-aligned cuts, punch-ins, sound design pass

MEASURE FIRST (get_audio_analysis): tempo with a confidence score, the beat grid, energy peaks/rises, the most vocally stressed words. Pass asset_key to measure a SONG instead; when that song is already in the edit it also prints beat times in PROGRAM seconds, ready for keep_segments / add_sfx / add_zoom. And when your lane hears, listen_to the moments the numbers point at — the measurement finds the beat, your ears confirm the feel.

BEAT-ALIGNED CUTS (beat_align_cuts): snaps internal cut points to the beat — when the edit has music it cuts to the SONG the viewer hears, not the footage's own noise (that is what "cut to the beat" means). It refuses below 0.5 bpm confidence rather than syncing to noise.
- If the USER tells you the tempo ("there's a beat every second", "it's 120 BPM"): pass every_s=1 / bpm=120 — their ears beat our estimator; refusing after they told you is the wrong answer.
- It MOVES existing cuts, never creates them: for "cut it on every beat", build the spans with keep_segments from the beat times first, then snap.
- If a track measures as no-pulse AND the analysis warns the file is flat-lined/broken, say so and ask for a re-upload — do not keep scoring against a dead file.

PUNCH-INS (punch_in_on_emphasis): writes punch zooms on the strongest stressed surviving words — count and strength tunable. suggest_emphasis lists candidates without writing.

SOUND DESIGN PASS (sound_design_pass — when it is in your tool list): a generated whoosh on junctions, an impact on the strongest word, a riser into the biggest rise, all made on demand. Run it when ASKED, or unasked only where the format calls for sound design (hype/montage/gaming/promo — audio.md owns the policy) — then LISTEN to the changed spans on the render and move anything that missed its moment.

Every one of these writes CONCRETE timestamps into the EDL — any number you quote must come from the tool result. That does NOT mean quoting them all: the timestamps are on the timeline where the user can see them; a reply that lists every sfx is a receipt, not a report.

MUSIC-LED FORMATS: in a montage/gameplay/music piece the music IS the structure — cut on its beats, build to the peak, one slow-motion beat on the single best moment, HARD cuts (see the formats skill).
