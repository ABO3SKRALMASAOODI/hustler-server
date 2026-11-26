"""An upload's steady sound is its film's music only when it measures as music (26 Nov: #901, #1158, #1826).

Loudness alone read shooter gameplay, cane-harvest machinery and wind as 'the upload carries its own track: it is
the film's music', and those first films went out with no chosen music (p6275, p6304: bed null). Synthetic signals
only; the thresholds were set on 64 tracks of the licensed library (63 read as music) and two speechless uploads."""
import numpy as np

from score import bed, perceive
from tests.test_score_round_five import library  # noqa: F401 - the fixture

SR = 16000


def _song(seconds=20.0, bpm=120):
    t = np.arange(int(seconds * SR)) / SR
    x = np.zeros_like(t)
    chords = [(220.0, 277.18, 329.63), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63), (196.0, 246.94, 329.63)]
    for k in range(int(seconds / 2)):
        a, z = int(k * 2 * SR), int((k + 1) * 2 * SR)
        for fq in chords[k % 4]:
            x[a:z] += 0.12 * np.sin(2 * np.pi * fq * t[a:z])
    period = 60.0 / bpm
    for b in np.arange(0, seconds, period):
        i = int(b * SR)
        n = min(int(0.12 * SR), len(x) - i)
        x[i:i + n] += 0.6 * np.exp(-np.arange(n) / (0.02 * SR)) * np.sin(2 * np.pi * 60 * np.arange(n) / SR)
    return x.astype(np.float32)


def _machinery(seconds=20.0, seed=3):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 0.2, int(seconds * SR))
    x = np.convolve(x, np.ones(8) / 8, mode="same")              # a dull roar
    clanks = rng.uniform(0, seconds, 25)                          # irregular knocks, no pulse
    for c in clanks:
        i = int(c * SR)
        n = min(int(0.05 * SR), len(x) - i)
        x[i:i + n] += rng.normal(0, 0.8, n) * np.exp(-np.arange(n) / (0.01 * SR))
    return x.astype(np.float32)


def test_a_song_measures_as_music_and_machinery_does_not():
    song, noise = perceive.music_measure(_song()), perceive.music_measure(_machinery())
    assert perceive.is_music(song) is True, song
    assert perceive.is_music(noise) is False, noise
    assert perceive.is_music(None) is None and perceive.music_measure(np.zeros(100, np.float32)) is None


def test_a_steady_sound_that_is_not_music_leaves_room_for_a_chosen_bed():
    """Before: own_track([-14] * 40, 0, 20) was True for any steady loud sound, and the film went out dry."""
    loud = [-14.0] * 40
    assert bed.own_track(loud, 0, 20.0) is True                                    # no measure (an older index): as before
    assert bed.own_track(loud, 0, 20.0, perceive.music_measure(_song())) is True   # a clip posted over a song keeps it
    assert bed.own_track(loud, 0, 20.0, perceive.music_measure(_machinery())) is False
    assert bed.own_track(loud, 0, 20.0, {"beat": 0.14, "tonal": 0.09}) is False    # p6275, measured


def test_perception_carries_the_music_measure(tmp_path):
    import wave
    p = tmp_path / "m.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(_machinery(8.0), -1, 1) * 30000).astype("<i2").tobytes())
    a = perceive.audio_facts(str(p), True)
    assert set(a["music"]) == {"beat", "tonal"} and perceive.is_music(a["music"]) is False


def test_ambience_plays_under_the_bed_that_leads_a_wordless_film(tmp_path):
    """A -13 dB harvester under a -19 LUFS song buried it: the renderer leaves a room's own sound where it was
    recorded. The source now sits AMBIENCE_UNDER_DB under the bed, never below the floor; a song stays untouched."""
    import json
    per = {"audio": {"rms_db": [-13.0] * 60, "music": {"beat": 0.14, "tonal": 0.09}}}
    (tmp_path / "perception.json").write_text(json.dumps(per))
    g = bed.ambience_under(str(tmp_path))
    assert g == max(bed.AMBIENCE_FLOOR_DB, bed.LEAD_LUFS - bed.AMBIENCE_UNDER_DB + 13.0) and g < 0
    bed._write(str(tmp_path), {"on": True, "cid": "mk-1", "settled": True, "source_under_db": g})
    assert bed.source_volume(str(tmp_path), 44.86) == [{"start": 0.0, "end": 44.86, "gain_db": g}]
    per["audio"]["music"] = {"beat": 0.5, "tonal": 0.3}
    (tmp_path / "perception.json").write_text(json.dumps(per))
    assert bed.ambience_under(str(tmp_path)) is None                    # the upload's song is never pushed down
    per["audio"] = {"rms_db": [-40.0] * 60, "music": {"beat": 0.05, "tonal": 0.0}}
    (tmp_path / "perception.json").write_text(json.dumps(per))
    assert bed.ambience_under(str(tmp_path)) is None                    # a quiet room already sits under the bed


def test_a_wordless_upload_of_machinery_gets_a_chosen_bed_with_its_sound_under_it(tmp_path, library):
    """#901 / p6275 / p6309: steady game or machine sound was read as the film's own music; the first film went out
    with no chosen music. Before this fix prechoose returned None ('the upload carries its own track')."""
    import json

    import schemas
    import score.compiler as S
    from score import lower
    from tests.test_score_compiler import BIN, wordless_index
    from tests.test_score_taste import film
    idx = wordless_index(20.0)
    perc = {"audio": {"step": 0.5, "rms_db": [-13.0] * 42, "mean_db": -13.0, "onsets": [], "music": {"beat": 0.14, "tonal": 0.09}}}
    json.dump(idx, open(tmp_path / "index.json", "w"))
    json.dump(perc, open(tmp_path / "perception.json", "w"))
    work = str(tmp_path)
    per = {"dur": 20.0, "audio": perc["audio"], "shots": [{"t": [0, 20.0]}]}
    assert bed.own_track(perc["audio"]["rms_db"], 0, 20.0) is True              # by loudness alone: the old reading
    doc = bed.prechoose(work, {"about": "cane harvest", "wishes": [], "asks": []}, per, idx)
    assert doc and doc["on"] and doc["kind"] == "marketing"
    text = film([{"k": "b", "id": "b01", "at": "@0..@20", "job": "x", "pic": "main"}])
    c = S.compile_score(text, index=idx, work_dir=work, sid="amb", perception=perc, bin_items=BIN)
    assert c.meter["taste"]["supply"]["own_bed"] is False and c.meter["taste"]["supply"]["bed"] is True
    got = bed.settle(work, c, idx, S.parse_score(text)[0], "cane harvest")
    assert got and got["lead"] and got["source_under_db"] < 0
    edl, _loc, rep = lower.lower(c.l1, work)
    assert edl["music"] and edl["music"][0]["storage_key"] == "bed:t-one"
    assert edl["volume"] == [{"start": 0.0, "end": 20.0, "gain_db": got["source_under_db"]}] and rep["bed"]["source_under_db"] == got["source_under_db"]
    schemas.validate_edl(edl, 20.0)
