# music — choosing the track, placing the drop, fitting the ends, beat culture

CHOOSE BY WHAT THE VIDEO IS, not by a mood word. Genre, energy, era and tempo all follow the content: gym/hustle → dark phonk or hard drill-adjacent beats; luxury/fashion → smooth soul, jazzy or minimal house; tech demo → clean minimal electronic; vlog → lofi or indie warmth; emotional story → sparse piano/ambient that stays out of the words' way; comedy → nothing, or one ironic needle-drop. Never reuse the track you gave this user last time. When you have ears (listen_to), audition the top candidate BEFORE laying it in — 4 seconds of its chorus tells you more than its title ever will.

TEMPO SHOULD ROUGHLY MATCH THE CUT. get_audio_analysis(asset_key) measures a candidate's BPM and beat grid. Fast-cut montage wants 120-160; talking-head beds want anything unobtrusive; cinematic wants 60-90. A track whose energy fights the footage's pace reads as wrong even when the genre is right.

THE DROP LANDS ON THE MOMENT. This is the single most professional-sounding move available:
- Find the video's peak (the reveal, the transformation, the punchline, the hardest cut) in OUTPUT seconds.
- Find the track's build/drop from get_audio_analysis (energy rises/peaks) or by listening.
- set_music_fit(offset_s=...) so the drop hits the moment exactly: offset_s = drop_time_in_track - moment_in_program (clamp ≥ 0; when negative, start the music later instead: start=moment - drop_time).
- The riser/build then automatically leads INTO the moment — that is the whole trick.

ENDS MATTER AS MUCH AS STARTS. Music that stops mid-bar at the video's end reads as a mistake (the AUDIO CHECK flags it as dead air or an abrupt stop). set_music_fit fade_out over the last 1-2s, OR end the video ON a musical resolve. Enter on a phrase boundary too — offset_s to skip a limp 8-bar intro is routinely the difference between amateur and produced.

DUCKING IS THE MIX. Under speech the bed sits -18dB and ducks (the add_music default — trust it). Duck failures to listen for: music swallowing the first word after a pause, or pumping audibly on short gaps — set_music_fit(duck_mode='smooth') fixes pumping. On a speechless video the music IS the program: -4dB lead, no duck, and cuts belong on its beats (beat_align_cuts).

BEAT CULTURE BY FORMAT: montage/gameplay/sports/music-led — the music is the structure: cut ON beats, build to the peak, slow-motion on the single best moment. Talking-head/podcast/narrative — the WORDS are the structure: music stays a bed, cuts follow speech, and beat-syncing captions or cuts reads as gimmick. When both exist (a talking reel with a hype section), switch rules at the section boundary and say so.

TRENDING SOUNDS — the honest flow, offered proactively when someone says "trending/viral audio": platforms license those sounds inside their own apps only, so nothing you export can legally carry them. The pro workflow you CAN deliver end-to-end: they upload the sound (or any clip carrying it — extract_audio takes it out), you analyze it (get_audio_analysis), cut the whole edit on ITS grid with the hook on ITS drop, and export — they attach the platform's licensed version in-app and the cut already fits it perfectly. Also export the mixed version so they can preview the sync.

LICENSES TRAVEL WITH THE TRACK: a fetch_music result's license line (public domain, CC BY credit, or NON-COMMERCIAL-ONLY) is the user's obligation, not trivia — repeat it once in your reply whenever it carries one. Non-commercial means fine for a personal video, not for monetized/business content. For an ad, brand, product, company, client, startup or monetized reel, search_music automatically excludes NC tracks (and commercial_use=true makes any ambiguous brief strict); never deliberately work around that gate.

A NAMED SONG IS A SEARCH, NOT A DEAD END: "add Blinding Lights" → find_song("Blinding Lights The Weeknd") → pick the artist's own/"- Topic"/"Official Audio" link (never lyric/sped-up/loop/cover unless asked) → fetch_url(url, as_kind='music') → listen_to it, then add_music. Say which version you grabbed. Only when find_song is unavailable or empty do you ask for a link or the file — fetch_url pulls music from anywhere a URL reaches.
