# music — choosing the track, placing the drop, fitting the ends, beat culture

Treat a music request as part of the brief. `add_music` may proceed from the
brief, metadata, an attached or named track, search context, measured audio
analysis, or editorial judgment. It may also be included in an atomic recipe.
If one source or URL fails, choose another route or candidate. Never claim to
have heard unreviewed seconds. When review_audio or an audition result carries
an ACTUAL LISTENING comparison, use only those labeled excerpts as subjective
evidence; otherwise say the choice is based on measurements and context.

CHOOSE BY WHAT THE VIDEO IS, not by a mood word. Genre, energy, era and tempo all follow the content: gym/hustle → dark phonk or hard drill-adjacent beats; luxury/fashion → smooth soul, jazzy or minimal house; tech demo → clean minimal electronic; vlog → lofi or indie warmth; emotional story → sparse piano/ambient that stays out of the words' way; comedy → nothing, or one ironic needle-drop. For a substantial edit use research_music: it returns the provider-diverse licensed search page and acoustically compares several plausible candidates in ONE evidence pass. Use search_music alone only for a quick/low-stakes lookup; audition_music_candidates remains useful when deliberately comparing a different subset from the cached slate. The comparison measures actual tempo confidence, dynamics, brightness, bass and dialogue-band masking risk against the creative blueprint. When the bounded listener is available it also compares actual candidate excerpts; otherwise the main model still does not hear the tracks. Combine both kinds of evidence with identity/license/context instead of turning either score into a taste lock. Reuse or vary tracks as the edit benefits.

TEMPO SHOULD ROUGHLY MATCH THE CUT. get_audio_analysis(asset_key) measures a candidate's BPM and beat grid. Fast-cut montage wants 120-160; talking-head beds want anything unobtrusive; cinematic wants 60-90. A track whose energy fights the footage's pace reads as wrong even when the genre is right.

THE DROP LANDS ON THE MOMENT. This is the single most professional-sounding move available:
- Find the video's peak (the reveal, the transformation, the punchline, the hardest cut) in OUTPUT seconds.
- Find the track's build/drop from get_audio_analysis (energy rises/peaks).
- set_music_fit(offset_s=...) so the drop hits the moment exactly: offset_s = drop_time_in_track - moment_in_program (clamp ≥ 0; when negative, start the music later instead: start=moment - drop_time).
- The riser/build then automatically leads INTO the moment — that is the whole trick.

ENDS MATTER AS MUCH AS STARTS. Music that stops mid-bar at the video's end reads as a mistake (the AUDIO CHECK flags it as dead air or an abrupt stop). set_music_fit fade_out over the last 1-2s, OR end the video ON a musical resolve. Enter on a phrase boundary too — offset_s to skip a limp 8-bar intro is routinely the difference between amateur and produced.

DUCKING IS THE MIX. Under speech the bed sits -18dB and ducks (the add_music default — trust it). If playback feedback says music swallows the first word after a pause or pumps on short gaps, set_music_fit(duck_mode='smooth') is the direct correction. On a speechless video the music IS the program: -4dB lead, no duck, and cuts belong on its beats (beat_align_cuts).

BEAT CULTURE BY FORMAT: montage/gameplay/sports/music-led — the music is the structure: cut ON beats, build to the peak, slow-motion on the single best moment. Talking-head/podcast/narrative — the WORDS are the structure: music stays a bed, cuts follow speech, and beat-syncing captions or cuts reads as gimmick. When both exist (a talking reel with a hype section), switch rules at the section boundary and say so.

TRENDING SOUNDS — the honest flow, offered proactively when someone says "trending/viral audio": platforms license those sounds inside their own apps only, so nothing you export can legally carry them. The pro workflow you CAN deliver end-to-end: they upload the sound (or any clip carrying it — extract_audio takes it out), you analyze it (get_audio_analysis), cut the whole edit on ITS grid with the hook on ITS drop, and export — they attach the platform's licensed version in-app and the cut already fits it perfectly. Also export the mixed version so they can preview the sync.

LICENSES TRAVEL WITH THE TRACK: a fetch_music result's license line (public domain, CC BY credit, or NON-COMMERCIAL-ONLY) is the user's obligation, not trivia — repeat it once in your reply whenever it carries one. Non-commercial means fine for a personal video, not for monetized/business content. For an ad, brand, product, company, client, startup or monetized reel, search_music automatically excludes NC tracks (and commercial_use=true makes any ambiguous brief strict); never deliberately work around that gate.

A NAMED SONG IS A SEARCH, NOT A DEAD END: "add Blinding Lights" → find_song("Blinding Lights The Weeknd") → pick a suitable candidate → fetch_url(url, as_kind='music') → add it. Say which version you grabbed. If search is unavailable or empty, ask for a link/file or use another valid route.
