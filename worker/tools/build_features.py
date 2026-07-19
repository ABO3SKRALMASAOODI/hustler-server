#!/usr/bin/env python3
"""Turn raw measurements into tags / not_for, by deterministic rules.

Every tag traces either to a measured threshold or to documented provenance
(the album a track belongs to, per SOURCE_NOTES.md / LICENSES.md). Nothing
is guessed from listening, because nothing was listened to.
"""
import json, sys

RAW = sys.argv[1]
OUT = sys.argv[2]

# ---- documented provenance -> genre vocabulary -------------------------
# Keyed by the FMA album in the source_url. These are DOCUMENTED facts
# (album/collection names and the artist's own track titles), not guesses.
ALBUM_TAGS = {
    "power-pop": ["power-pop", "pop", "rock", "band", "energetic", "retro"],
    "public-domain-lofi": ["lofi", "lo-fi", "chill", "chillhop", "relaxed",
                           "study", "mellow", "beat"],
    "Kevelin_and_Chestnuts_Adventures": ["soundtrack", "adventure", "score",
                                         "storybook", "whimsical", "quirky"],
    "background-music": ["background", "underscore", "corporate", "bed",
                         "neutral", "unobtrusive"],
    "urban-warrior": ["urban", "dramatic", "beat", "gritty", "tension",
                      "action", "street"],
    "beats-from-the-crypt": ["hip-hop", "hiphop", "beat", "instrumental-beat",
                             "spooky", "halloween", "horror", "dark", "eerie"],
    "enchanted-valley": ["ambient", "atmospheric", "calm", "fantasy",
                         "peaceful", "soft", "pad"],
    "an-ocean-in-outer-space": ["ambient", "space", "video-game", "retro",
                                "synth", "dreamy"],
    "cute-melodies": ["cute", "playful", "light", "inspiring", "gentle",
                      "kids", "friendly", "melodic"],
}

# Per-track extras justified by the artist's own documented title text.
TITLE_TAGS = {
    "corporate-a-small-town-on-pluto-music-box": ["music-box", "chimes",
                                                  "delicate", "toy", "lullaby"],
    "chill-bubbles-lofi-bright-relaxed": ["bright", "relaxed"],
    "chill-calm-currents-lofi-relax-calm": ["calm", "relax"],
    "chill-canon-event-lofi-sad-reflection": ["sad", "reflective",
                                              "melancholy", "emotional"],
    "ambient-calm-before-the-storm": ["calm", "suspense"],
    "cinematic-after-midnight": ["night", "late-night", "nocturnal"],
}


def album_of(url):
    for key in ALBUM_TAGS:
        if f"/{key}/" in url:
            return key
    return None


# ---- measured trailer bar ---------------------------------------------
# What "epic cinematic movie-trailer music" actually measures like:
# a quiet sparse open, a monotone rise, and huge peaks over a low floor.
TRAILER_CRITERIA = {
    "dynamic_range_db_min": 20.0,
    "arc_build_correlation_min": 0.70,
    "arc_range_db_min": 12.0,
    "low_end_fraction_below_200hz_min": 0.50,
    "crest_factor_db_min": 18.0,
    "final_segment_is_loudest": True,
}


def trailer_report(v):
    arc = v["energy_arc_db"]
    checks = {
        "dynamic_range_db": v["dynamic_range_db"] >= 20.0,
        "arc_build_correlation": v["arc_build_correlation"] >= 0.70,
        "arc_range_db": v["arc_range_db"] >= 12.0,
        "low_end_fraction_below_200hz":
            v["low_end_fraction_below_200hz"] >= 0.50,
        "crest_factor_db": v["crest_factor_db"] >= 18.0,
        "final_segment_is_loudest": arc.index(max(arc)) >= 6,
    }
    return {
        "criteria_passed": sorted(k for k, ok in checks.items() if ok),
        "criteria_failed": sorted(k for k, ok in checks.items() if not ok),
        "n_passed": sum(checks.values()),
        "n_criteria": len(checks),
        "qualifies": all(checks.values()),
    }


def build(slug, v):
    tags, notfor = set(), set()
    bpm = v["bpm"]
    conf = v["bpm_confidence"]
    amb = v["bpm_octave_ambiguous"]
    cen = v["spectral_centroid_hz_mean"]
    low = v["low_end_fraction_below_200hz"]
    dr = v["dynamic_range_db"]
    arcr = v["arc_range_db"]
    corr = v["arc_build_correlation"]

    # --- provenance
    album = album_of(v["source_url"])
    if album:
        tags.update(ALBUM_TAGS[album])
    tags.update(TITLE_TAGS.get(slug, []))
    tags.add(v["manifest_mood"])

    # A word taken from the artist's title is still the artist's opinion.
    # Where measurement contradicts it, measurement wins and the word goes.
    # ("Bubbles (Lofi, Bright, Relaxed)" measures at 1186 Hz centroid --
    # below this catalogue's median -- so it does not get to say "bright".)
    if cen < 1800 and "bright" in tags:
        tags.discard("bright")
        v["_tag_overrides"] = v.get("_tag_overrides", []) + [
            "dropped title-derived 'bright': measured centroid "
            f"{cen} Hz is below the 1800 Hz bright threshold"
        ]

    # --- tempo (only when the estimate is worth anything)
    # Gate tempo REJECTIONS more conservatively than the reported ambiguity
    # flag: if the half/double lag carries >55% of the chosen peak, calling
    # the track "never fast" (or "never slow") could be flatly wrong.
    ah, ac_, ad = (v["bpm_acf_at_half"], v["bpm_acf_at_bpm"],
                   v["bpm_acf_at_double"])
    tempo_uncertain = bool(amb or max(ah, ad) > 0.55 * max(ac_, 1e-9))
    v["bpm_octave_uncertain_for_tagging"] = tempo_uncertain
    amb = tempo_uncertain

    if bpm and conf >= 0.40:
        if bpm < 70:
            tags.update(["very-slow", "slow"])
        elif bpm < 90:
            tags.update(["slow", "downtempo", "laid-back"])
        elif bpm < 110:
            tags.update(["mid-tempo", "moderate-tempo"])
        elif bpm < 140:
            tags.update(["uptempo", "driving"])
        else:
            tags.update(["fast", "uptempo"])
        # tempo rejections, but never when the metrical level is ambiguous
        if not amb:
            if bpm < 90:
                notfor.update(["fast", "frantic", "high-tempo", "drum-and-bass",
                               "hardstyle", "double-time"])
            if bpm > 125:
                notfor.update(["slow", "sleepy", "very-slow", "ballad"])
    else:
        tags.add("unsteady-tempo")
        notfor.update(["beat-synced", "dance", "club", "workout",
                       "rhythmic-cutting"])

    # --- brightness (magnitude-weighted spectral centroid)
    if cen < 1100:
        tags.update(["dark", "warm", "muted"])
        notfor.update(["bright", "sparkly", "airy", "shimmering", "crisp"])
    elif cen < 1800:
        tags.update(["warm", "mid-focused"])
    elif cen < 2400:
        tags.update(["bright", "clear"])
        notfor.update(["dark", "murky"])
    else:
        tags.update(["bright", "very-bright", "crisp", "present"])
        notfor.update(["dark", "murky", "muffled", "warm-and-dull"])

    # --- low end
    if low >= 0.80:
        tags.update(["bass-heavy", "heavy-low-end", "sub-heavy", "weighty"])
        notfor.update(["thin", "airy-light", "no-bass"])
    elif low >= 0.55:
        tags.update(["full-low-end", "grounded"])
    elif low >= 0.20:
        tags.update(["light-low-end"])
        notfor.update(["bass-heavy", "sub-bass", "808", "trap", "drill",
                       "hard-hitting", "boomy"])
    else:
        tags.update(["thin", "no-bass", "airy", "sparse-low-end"])
        notfor.update(["bass-heavy", "sub-bass", "808", "trap", "drill",
                       "hard-hitting", "boomy", "heavy", "banger"])

    # --- dynamics
    if dr < 6.0:
        tags.update(["flat-dynamics", "steady", "consistent", "bed",
                     "background", "loopable"])
        notfor.update(["dynamic", "dramatic-swells", "climax", "crescendo",
                       "big-drop", "explosive"])
    elif dr < 10.0:
        tags.update(["moderate-dynamics", "steady"])
        notfor.update(["huge-dynamics", "orchestral-swell"])
    else:
        tags.update(["dynamic", "varied"])

    # --- arc shape
    if corr >= 0.60 and arcr >= 5.0:
        tags.update(["builds", "rising", "progressive"])
    elif corr <= -0.50 and arcr >= 3.0:
        tags.update(["fades", "winds-down", "decaying"])
        notfor.update(["builds", "rising", "climactic"])
    if arcr < 3.0:
        tags.update(["flat-arc", "even", "loopable", "bed"])
        notfor.update(["builds", "rising", "climactic", "crescendo"])

    # A track with a measured rising arc is not a neutral loop bed, even if
    # its moment-to-moment dynamics are flat. Arc shape wins this conflict.
    if "builds" in tags:
        tags -= {"loopable", "bed", "background", "flat-arc", "even",
                 "consistent"}

    # --- the trailer bar (measured, applied to every track)
    tr = trailer_report(v)
    if not tr["qualifies"]:
        notfor.update(["epic", "movie-trailer", "trailer", "epic-trailer",
                       "blockbuster", "orchestral-hit", "braam", "riser-hit",
                       "epic-cinematic", "hollywood", "monumental",
                       "heroic-climax"])

    # --- genre exclusions grounded in the documented album genre
    if album in ("public-domain-lofi", "background-music", "enchanted-valley",
                 "cute-melodies", "an-ocean-in-outer-space"):
        notfor.update(["metal", "aggressive", "distorted-guitar", "dubstep",
                       "hardcore", "war-drums", "intense"])
    if album in ("public-domain-lofi", "background-music", "cute-melodies",
                 "enchanted-valley"):
        notfor.update(["orchestral", "symphonic", "choir"])
    if album == "beats-from-the-crypt":
        notfor.update(["happy", "cheerful", "wedding", "corporate-positive",
                       "orchestral", "symphonic"])
    if album == "cute-melodies":
        notfor.update(["dark", "sinister", "horror", "menacing", "gritty"])
    if album == "enchanted-valley":
        notfor.update(["percussive", "beat-driven", "danceable"])

    # never contradict ourselves: a measured tag always beats a rule-set
    notfor -= tags
    return sorted(tags), sorted(notfor), tr


def main():
    raw = json.load(open(RAW))
    manifest = {m["slug"]: m for m in json.load(
        open("/Users/muslimshmary/Documents/hustler-server/worker/music/manifest.json"))}

    out = {}
    for slug, v in raw.items():
        v["source_url"] = manifest[slug]["source_url"]
        v["license"] = manifest[slug]["license"]
        tags, notfor, tr = build(slug, v)

        # vocal verdict: the probes below did NOT separate this catalogue
        # (all values sit on a smooth continuum, no bimodality), so no
        # instrumental claim is made for any track.
        p = v["vocal_probe"]
        probe_usable = p["side_to_mid_rms_ratio"] >= 0.10
        v["vocal_presence"] = "unknown"
        v["vocal_presence_confidence"] = 0.0
        v["vocal_probe"]["centre_cancellation_test_usable"] = bool(probe_usable)
        v["vocal_probe"]["method"] = (
            "Two probes were computed and BOTH failed to separate this "
            "catalogue. (1) Centre-channel cancellation: side=(L-R)/2 vs "
            "mid=(L+R)/2, comparing 300-3400 Hz band energy -- a centred "
            "vocal should collapse in the side channel. (2) Syllabic "
            "modulation: fraction of the 300-3400 Hz envelope's modulation "
            "energy falling in 2-8 Hz (speech syllable rate). Across all 24 "
            "tracks both metrics form a smooth continuum with no bimodal "
            "split (centre dominance 0.34-0.97, syllabic ratio 0.24-0.68), "
            "and percussion modulates at the same 2-8 Hz rate as syllables, "
            "so neither probe can distinguish a sung line from a lead "
            "instrument. Verdict is therefore 'unknown' for every track."
        )
        v["vocal_presence_note"] = (
            "NOT MEASURED as absent. SOURCE_NOTES.md records that candidates "
            "came from FMA's only-instrumental=1 filter, which is "
            "uploader-declared provenance, not verified audio analysis. "
            "Treat as unverified: do not answer a request that requires "
            "guaranteed no-vocals from this field alone."
        )
        v["trailer_suitability"] = tr
        v["tags"] = tags
        v["not_for"] = notfor
        out[slug] = v

    header = {
        "_about": {
            "generated_by": "objective ffmpeg/numpy measurement of the "
                            "decoded audio; no listening, no subjective tags",
            "sample_rate_hz": 22050,
            "stft": {"n_fft": 2048, "hop": 512, "window": "hann"},
            "measurement_notes": [
                "LICENSES.md records that every file was loudness-normalised "
                "to about -16 LUFS and trimmed of leading/trailing silence. "
                "That makes rms_dbfs_mean near-constant across the catalogue "
                "(-22.2 to -15.4 dBFS) and therefore NOT a useful "
                "discriminator; dynamic_range_db and energy_arc are "
                "unaffected by gain normalisation and remain valid. Silence "
                "trimming could have removed a quiet intro, but not an "
                "internal build.",
                "low_end_fraction_below_200hz is a POWER fraction. Low "
                "frequencies carry most of the power in almost all music, so "
                "values near 0.7 are normal, not 'bass-heavy'. Use the "
                "catalogue ranking, not the absolute number.",
                "spectral_centroid_hz_mean is magnitude-weighted (the "
                "conventional definition). A power-weighted variant is also "
                "reported and is much lower.",
                "bpm was folded down into the 70-140 tapping range only when "
                "the half-tempo lag was itself a strong autocorrelation peak. "
                "Where bpm_octave_ambiguous is true the true tempo may be "
                "half or double the reported value, so no tempo term was "
                "added to not_for for those tracks.",
                "vocal presence is 'unknown' for all 24 tracks -- see "
                "vocal_probe.method. No track is claimed to be instrumental.",
            ],
            "trailer_criteria": TRAILER_CRITERIA,
            "trailer_verdict": (
                f"{sum(1 for v in out.values() if v['trailer_suitability']['qualifies'])}"
                f" of {len(out)} tracks meet the measured bar; best score was "
                f"{max(v['trailer_suitability']['n_passed'] for v in out.values())}"
                f"/{len(TRAILER_CRITERIA)} criteria. See trailer_suitability "
                f"on each track."),
        }
    }
    header.update(out)
    json.dump(header, open(OUT, "w"), indent=2)
    print(f"wrote {OUT} ({len(out)} tracks)")


if __name__ == "__main__":
    main()
