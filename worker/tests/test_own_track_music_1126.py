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
    assert bed.source_volume(str(tmp_path), 44.86) == [{"start": 0.0, "end": 44.86, "gain_db": g, "music_id": "bed"}]
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
    assert edl["volume"] == [{"start": 0.0, "end": 20.0, "gain_db": got["source_under_db"], "music_id": "bed"}] and rep["bed"]["source_under_db"] == got["source_under_db"]
    schemas.validate_edl(edl, 20.0)


def test_the_duck_leaves_the_spoken_words_at_their_recorded_level(tmp_path):
    """A few lines over a skate, a game or a machine: the whole source went 10-24 dB down under the leading bed, the
    words with it (the dialogue leveller measures with the automation left out, so it never brought them back). Now
    only the stretches where nobody speaks are ducked, easing in and out beside the words."""
    idx = {"words": [{"w": "look", "t0": 5.0, "t1": 5.4}, {"w": "at", "t0": 5.5, "t1": 5.7}, {"w": "this", "t0": 5.8, "t1": 6.2},
                     {"w": "go", "t0": 14.0, "t1": 14.3}]}
    spans = bed.speech_spans(idx)
    assert spans == [[4.8, 6.4], [13.8, 14.5]]
    bed._write(str(tmp_path), {"on": True, "cid": "mk-1", "settled": True, "source_under_db": -20.0, "source_speech": spans})
    vol = bed.source_volume(str(tmp_path), 20.0)
    for a, b in spans:                                                   # no item touches a spoken stretch
        assert not any(v["start"] < b and a < v["end"] for v in vol), (a, b, vol)
    assert min(v["gain_db"] for v in vol) == -20.0 and any(v["gain_db"] == -10.0 for v in vol)    # full depth, and shoulders
    assert vol[0]["start"] == 0.0 and vol[-1]["end"] == 20.0
    bed._write(str(tmp_path), {"on": True, "cid": "mk-1", "settled": True, "source_under_db": -20.0, "source_speech": [[0.0, 20.0]]})
    assert bed.source_volume(str(tmp_path), 20.0) == []                  # talk throughout: the source is never ducked


def test_music_that_starts_late_and_a_lo_fi_song_still_read_as_music():
    """Only the first 180 s were read, and a song had to clear one bar alone: a long upload whose song starts late,
    or a phone-recorded song part-way to both bars, read as ambience and was ducked under a chosen bed."""
    late = np.concatenate([_machinery(70.0), _song(40.0), _machinery(70.0)])
    assert perceive.is_music(perceive.music_measure(_machinery(180.0))) is False
    assert perceive.is_music(perceive.music_measure(late)) is True
    assert perceive.is_music({"beat": 0.16, "tonal": 0.14}) is True              # part-way to both
    assert perceive.is_music({"beat": 0.14, "tonal": 0.09}) is False             # p6275's game sound, measured
    assert perceive.is_music({"beat": 0.07, "tonal": 0.0}) is False              # p6309


def test_a_cached_preparation_from_before_the_music_measure_is_rebuilt(monkeypatch, tmp_path):
    """Every upload indexed before this carries a perception with no `music`. The cache's identity hashes perceive's
    own source, so a cache made by older measurement code is never restored: the film measures again, with music."""
    from score import preparation
    preparation._code_key.cache_clear()
    now = preparation._code_key()
    older = tmp_path / "perceive_old.py"
    older.write_text(open(perceive.__file__, encoding="utf-8").read().replace("music=music_measure(x), ", ""))
    monkeypatch.setattr(perceive, "__file__", str(older))
    preparation._code_key.cache_clear()
    try:
        assert preparation._code_key() != now
    finally:
        monkeypatch.undo()
        preparation._code_key.cache_clear()


def test_removing_the_bed_brings_the_uploads_own_sound_back(tmp_path):
    """The ambience duck was plain source-time volume with no link to the bed: removing the chosen music left the
    upload's own sound 10-24 dB down wherever nobody spoke. The duck now belongs to the bed (music_id): remove_music
    takes it with the track, and the renderer never plays a level whose music is gone or muted."""
    import json

    import schemas
    from agent_tools import remove_music
    from renderer import build_filtergraph
    from timeline import Timeline
    bed._write(str(tmp_path), {"on": True, "cid": "mk-1", "settled": True, "source_under_db": -16.0,
                               "source_speech": [[4.8, 6.4]]})
    duck = bed.source_volume(str(tmp_path), 20.0)
    assert duck and all(v["music_id"] == "bed" for v in duck)
    hand = {"start": 10.0, "end": 12.0, "gain_db": -6.0}                 # the customer's own level stays
    edl = schemas.validate_edl({"keep": [[0.0, 20.0]],
                                "music": [{"id": "bed", "storage_key": "music/1/a.mp3", "start": 0.0, "end": 20.0,
                                           "gain_db": -18.0, "duck": False}],
                                "volume": duck + [hand]}, 20.0).model_dump()
    assert [v["music_id"] for v in edl["volume"]].count("bed") == len(duck)

    class Ctx:
        written = None

        def latest_edl(self):
            return {"version": 1, "json": json.loads(json.dumps(edl))}

        def write_edl(self, e, desc):
            self.written = e
            return f"EDL v1 -> v2: {desc}"

        def run(self, fn, *a, **k):
            return None
    ctx = Ctx()
    ctx.db = ctx
    ctx.project_id = 1
    out = remove_music(ctx, "bed")
    assert out.startswith("EDL v") and "own sound" in out
    assert ctx.written["volume"] == [dict(hand, music_id=None)]

    # an EDL that lost its bed some other way (or muted it) never plays the duck
    index = {"video": {"duration": 20}, "words": [], "sentences": []}
    tl = Timeline(edl["keep"])
    deep = f"volume={-16.0}dB:enable="
    assert deep in build_filtergraph(edl, 20.0, True, tl, None, [], index, preview=True)
    orphan = dict(edl, music=[])
    g = build_filtergraph(orphan, 20.0, True, tl, None, [], index, preview=True)
    assert deep not in g and "volume=-6.0dB:enable='between(t,10.00,12.00)'" in g
    muted = dict(edl, music=[dict(edl["music"][0], mute=True)])
    assert deep not in build_filtergraph(muted, 20.0, True, tl, None, [], index, preview=True)
    assert schemas.sounding_volume(orphan) == [dict(hand, music_id=None)]


def test_the_music_measure_reads_ten_minutes_in_bounded_memory(monkeypatch):
    """The peak finder's sliding median copied every frame x bin x 25 neighbours of a 60 s window at once: 221 MB
    measured on 10 minutes of audio, on top of the decoded track (a Linux worker is OOM-killed, not given a
    MemoryError). Row by row it reads the same values in a few MB."""
    import tracemalloc
    rng = np.random.default_rng(1)
    t = np.arange(SR * 600) / SR
    x = (0.3 * np.sin(2 * np.pi * 220 * t) * (1 + np.sin(2 * np.pi * 2 * t)) + 0.05 * rng.standard_normal(len(t))).astype(np.float32)
    tracemalloc.start()
    try:
        got = perceive.music_measure(x)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert got is not None and peak < 80e6, peak
    song = np.clip(_song(20.0), -1, 1)
    rows = perceive.music_measure(song)
    monkeypatch.setattr(perceive, "MUSIC_MED_ROWS", 10 ** 9)          # the whole window at once: the old reading
    assert perceive.music_measure(x) == got and perceive.music_measure(song) == rows
