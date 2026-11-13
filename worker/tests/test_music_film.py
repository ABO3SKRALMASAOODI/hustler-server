"""Every film gets its music (MUSIC2, 14 Nov 2026): music_film's story marks, the audition, the track fetch, the
Director's arranged bed and its seam in score.bed.

Signal only: a synthetic song, a synthetic library, a fake model, no network, no storage."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import types

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import music_film as MF  # noqa: E402
from score import bed  # noqa: E402
from score import music as MU  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which(FF) is None, reason="needs ffmpeg")


def _wav(tmp, name, bars=28, intro_bars=8):
    from rhythm import dsp
    from test_w2c_rhythm import synth
    y, _truth = synth(bpm=120.0, bars=bars, intro_bars=intro_bars)
    raw = tmp / f"{name}.f32"
    np.asarray(y, np.float32).tofile(raw)
    out = str(tmp / f"{name}.wav")
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(dsp.SR), "-ac", "1", "-i", str(raw), "-ac", "2", "-ar", "48000", out], check=True)
    return out


@pytest.fixture(scope="module")
def songs(tmp_path_factory):
    if shutil.which(FF) is None:
        pytest.skip("needs ffmpeg")
    d = tmp_path_factory.mktemp("songs")
    # one song that never comes in (all intro: hats and a quiet pad), one with an intro and a drop
    return {"flat": _wav(d, "flat", bars=28, intro_bars=28), "full": _wav(d, "full", bars=28, intro_bars=8)}


def _marks(dur=32.0, turn=None, payoff=14.1):
    words = [(0.4 + k * 0.5, 0.8 + k * 0.5) for k in range(int((dur - 2) / 0.5))]
    return {"dur_s": dur, "words": words, "cuts": [], "turn_s": turn, "payoff_s": payoff, "end_hit_s": None, "quiet": []}


# ----------------------------------------------------------------- the story's marks

def test_a_model_reads_the_turn_and_the_payoff_from_the_films_lines_and_fails_closed():
    lines = [(0.0, "FedEx had five thousand dollars left."), (21.2, "A lender said no."), (32.1, "And he won twenty-seven thousand.")]
    got = MF.story_marks(lines, 58.0, lambda system, user, schema: {"turn_s": 21.2, "payoff_s": 32.1, "mood": "tense, daring"})
    assert got == {"turn_s": 21.2, "payoff_s": 32.1, "mood": "tense, daring"}
    back = MF.story_marks(lines, 58.0, lambda *a: {"turn_s": 40.0, "payoff_s": 32.1, "mood": "x"})
    assert back["turn_s"] is None and back["payoff_s"] == 32.1          # a turn after the payoff is no turn
    out = MF.story_marks(lines, 58.0, lambda *a: {"turn_s": 99.0, "payoff_s": None, "mood": ""})
    assert out["turn_s"] is None and out["payoff_s"] is None             # a moment outside the film is not one

    def broken(*_a):
        raise RuntimeError("no model")
    assert MF.story_marks(lines, 58.0, broken) is None and MF.story_marks([], 58.0, lambda *a: {}) is None and MF.story_marks(lines, 58.0, None) is None


def test_the_films_lines_come_from_its_own_captions():
    l1 = {"fps": 30, "items": [{"k": "word", "v": "cap", "t": "FedEx", "f": [0, 12]}, {"k": "word", "v": "cap", "t": "had", "f": [12, 20]},
                               {"k": "word", "v": "capd", "t": "Then", "f": [90, 100]}, {"k": "word", "v": "big", "t": "$27,000", "f": [95, 120]},
                               {"k": "shot", "f": [0, 90]}]}
    assert MF.lines_of(l1) == [(0.0, "FedEx had"), (3.0, "Then")]
    # a film without caption items: its index's words through the spine, a line a sentence
    take = {"fps": 30, "items": [], "spine": [{"f": [0, 60], "src": [10.0, 12.0]}, {"f": [60, 150], "src": [20.0, 23.0]}]}
    words = [{"w": "skip", "t0": 1.0, "t1": 1.2}, {"w": "He", "t0": 10.2, "t1": 10.4}, {"w": "won.", "t0": 10.5, "t1": 10.9},
             {"w": "Then", "t0": 20.5, "t1": 20.8}, {"w": "more", "t0": 21.0, "t1": 21.3}]
    assert MF.lines_of(take, {"words": words}) == [(0.2, "He won."), (2.5, "Then more")]


def test_the_chooser_reads_the_film_from_the_engines_marks():
    m = dict(_marks(30.0, turn=9.0, payoff=14.0), cuts=[1.0 * k for k in range(1, 28)])
    f = MF.facts("wf3", m, brief="A founder's gamble.", mood="tense")
    assert f["kind"] == "story" and f["duration_s"] == 30.0 and f["pace"]["cuts10"] == 9.0 and 0.7 < f["speech_share"] < 0.8
    assert f["turns"] == [{"t": 9.0, "role": "turn"}, {"t": 14.0, "role": "climax"}] and f["brief"].endswith("Mood: tense")


# ----------------------------------------------------------------- the audition

def _opt(cid):
    t = {"cid": cid, "title": cid.title(), "author": "someone", "license": "CC-BY-4.0", "credit": f"\"{cid}\" by someone (CC BY 4.0)", "storage_key": f"music-library/{cid}.mp3",
         "source_url": "https://incompetech.com/x.mp3", "source_sha256": "0" * 64, "duration_s": 57.0, "bpm": 120.0, "mode": "minor", "tags": ["Electronic music"], "vocals": 0.0}
    return {"cid": cid, "score": 0.8, "track_score": 0.8, "facts": {"ref_similarity": 0.8, "tempo_fit": 0.9}, "track": t,
            "section": {"start_s": 0.0, "end_s": 30.0, "why": [], "align": [], "loop": False}}


@needs_ffmpeg
def test_the_engine_tries_the_models_picks_and_keeps_the_first_that_carries_the_film(songs, monkeypatch):
    import music_choose
    monkeypatch.setattr(music_choose, "shortlist", lambda film, used=(), n=8: ([_opt("flat"), _opt("full")], {"n": 2, "bpm": {"p25": 100, "p75": 130}, "minor_share": 0.7, "top_tags": {}}))
    film = {"kind": "story", "duration_s": 32.0, "turns": [{"t": 14.1, "role": "climax"}], "pace": {"cuts10": 8.0}, "speech_share": 0.8, "brief": "a gamble"}
    asked = []

    def model(system, user, schema):
        asked.append(user)
        return {"order": [1, 2], "why": "the first has the mood"}
    ch, path, tried = MF.choose(film, _marks(), "/nonexistent", call=model, fetch=lambda t: songs[t["cid"]])
    assert asked and "Options:" in asked[0]
    assert [r["cid"] for r in tried] == ["flat", "full"] and tried[0]["carries"] > tried[1]["carries"] + 1.0     # no entrance to land the payoff on
    assert tried[1]["drop_is"] == "entrance" and tried[1]["intro_bars"] == 0
    assert ch["cid"] == "full" and path == songs["full"] and ch["chosen_by"] == "model" and "kept pick 2" in ch["why"] and ch["credit"] and ch["sung"] is False
    # no model, or a model that fails: the signal's order is tried, the same door
    ch2, _p, tried2 = MF.choose(film, _marks(), "/nonexistent", call=None, fetch=lambda t: songs[t["cid"]])
    assert ch2["cid"] == "full" and ch2["chosen_by"] == "signal" and len(tried2) == 2

    def down(*_a):
        raise RuntimeError("down")
    assert MF.choose(film, _marks(), "/nonexistent", call=down, fetch=lambda t: songs[t["cid"]])[0]["cid"] == "full"
    # nothing fetchable: no choice, and the film goes out without music
    def nofetch(_t):
        raise OSError("offline")
    ch3, p3, tried3 = MF.choose(film, _marks(), "/nonexistent", call=None, fetch=nofetch)
    assert ch3 is None and p3 is None and all("error" in r for r in tried3)


@needs_ffmpeg
def test_a_long_film_is_scored_chapter_by_chapter_a_track_each(songs, tmp_path, monkeypatch):
    import music_choose
    # 300 s of narration with a long pause every 70 s or so: the chapters turn there
    words, t = [], 0.4
    while t < 298.0:
        words.append((t, t + 0.4))
        t += 0.5 if int(t) not in (69, 141, 215) else 2.5
    marks = {"dur_s": 300.0, "words": words, "cuts": [], "turn_s": None, "payoff_s": None, "end_hit_s": None, "quiet": []}
    chs = MF.chapters(marks)
    assert chs[0][0] == 0.0 and chs[-1][1] == 300.0 and len(chs) == 4 and all(b - a >= MF.CHAPTER_S[0] for a, b in chs)
    assert all(any(abs(b - x) < 2.0 for x in (70.5, 142.5, 216.5)) for _a, b in chs[:-1])       # in the narrator's pauses
    lib = {"one": songs["full"], "two": _wav(tmp_path, "two", bars=40, intro_bars=4), "three": _wav(tmp_path, "three", bars=44, intro_bars=6),
           "four": _wav(tmp_path, "four", bars=48, intro_bars=2)}
    monkeypatch.setattr(music_choose, "shortlist", lambda film, used=(), n=8: ([_opt(c) for c in lib], {"n": 4, "bpm": {"p25": 100, "p75": 130}, "minor_share": 0.7, "top_tags": {}}))
    film = {"kind": "longform", "duration_s": 300.0, "turns": [], "pace": {"cuts10": 2.0}, "speech_share": 0.8, "brief": "a long story"}
    music, rep = MF.score_chapters(film, marks, "longform", "/nonexistent", fetch=lambda t: lib[t["cid"]])
    rows = rep["chapters"]
    picked = [r["choice"]["cid"] for r in rows]
    assert len(picked) == 4 and len(set(picked)) == 4                    # no track twice in one film
    assert music.shape == (int(300.0 * MU.SR), 2) and all(r["intro_bars"] == 0 for r in rows)
    for (a, b), r in zip(chs, rows):                                      # each chapter closes on its own hit, silent before the next opens
        assert a < r["end_hit_s"] < b
        gap = music[int((b - 0.05) * MU.SR):int(b * MU.SR)]
        assert float(np.abs(gap).max()) == 0.0
    assert abs(rep["under_voice_db"] - MU.UNDER_VOICE["longform"]) <= 0.7


def test_a_library_file_is_kept_only_when_it_is_the_one_that_was_measured(tmp_path, monkeypatch):
    body = b"an mp3, as far as this test goes"
    fake = types.SimpleNamespace(download=lambda url, out, allowed_hosts=None, max_bytes=0, timeout_s=0: open(out, "wb").write(body))
    monkeypatch.setitem(sys.modules, "net_fetch", fake)
    good = {"cid": "t-good", "source_url": "https://incompetech.com/a.mp3", "source_sha256": hashlib.sha256(body).hexdigest()}
    p = MF.local_track(good, str(tmp_path))
    assert open(p, "rb").read() == body and MF.local_track(good, str(tmp_path)) == p
    with pytest.raises(ValueError):
        MF.local_track({"cid": "t-bad", "source_url": "https://incompetech.com/b.mp3", "source_sha256": "f" * 64}, str(tmp_path))
    assert sorted(os.listdir(tmp_path)) == ["t-good.mp3"]                # the changed file is not kept, nor its part


# ----------------------------------------------------------------- the Director's bed, arranged

def _film(dur=32.0):
    fps = 30
    words = [{"t0": 0.4 + k * 0.5, "t1": 0.8 + k * 0.5} for k in range(int((dur - 2) / 0.5))]
    l1 = {"fps": fps, "dur_f": int(dur * fps), "spine": [{"f": [0, int(dur * fps)], "src": [0.0, dur]}], "items": [], "sound": []}
    return l1, {"words": words}


@needs_ffmpeg
def test_the_directors_bed_is_cut_to_the_film_and_plays_as_written(songs, tmp_path, monkeypatch):
    work = str(tmp_path)
    l1, index = _film(32.0)
    doc = {"on": True, "cid": "t-one", "settled": True, "title": "One", "author": "someone", "license": "CC-BY-4.0", "gain_db": -14.0, "offset_s": 8.0,
           "turn_s": 14.1, "kind": "podcast"}
    shutil.copyfile(songs["full"], os.path.join(work, bed.TRACK_FILE))
    f = {"kind": "podcast", "speech_share": 0.8, "turns": [{"t": 14.1, "role": "turn"}]}
    bed._write(work, doc)
    arr = bed.arranged(work, doc, l1, index, f, 14.1)
    assert arr and os.path.exists(arr["path"]) and arr["file"] == MF.BED_FILE
    assert abs(arr["drop_at"] - 14.1) < 0.02 and arr["build_at"] < arr["drop_at"] and arr["intro_bars"] == 0
    assert abs(arr["under_voice_db"] - bed.UNDER_VOICE["podcast"]) <= 0.6 and arr["end_s"] <= 32.0 - MU.PROFILE["end_gap_s"] + 1e-6
    music, fx = bed.items(work, 32.0)
    m = music[0]
    assert fx == [] and m["gain_db"] == 0.0 and m["duck"] is False and m["start"] == 0.0 and m["provenance"] == "valmera_chosen" and m["storage_key"] == "bed:t-one"
    assert m["end"] <= 32.0 - 1.0 / 30 and "offset_s" not in m and "loop" not in m     # nothing of it rides into the end card
    assert bed.chosen(work)["placed"] == "arranged"
    again, fx2 = bed.items(work, 25.0)                                   # the same film lowered again, shorter: not the film this was cut for
    assert again[0]["gain_db"] == -14.0 and again[0]["offset_s"] == 8.0 and len(fx2) == 1 and bed.chosen(work)["placed"] == "plain"
    # the asset is the audio the item plays: the cut for the arranged item, the library's own copy for the plain one
    asked = []
    monkeypatch.setattr(MF, "ensure_arranged_asset", lambda c, d, p: (asked.append("cut") or "music/7/cut.m4a", None))
    monkeypatch.setitem(sys.modules, "music_gate", types.SimpleNamespace(grant=lambda *a: None))
    real = sys.modules.get("music_choose")
    monkeypatch.setitem(sys.modules, "music_choose", types.SimpleNamespace(ensure_project_asset=lambda c, ch: (asked.append("library") or "music/7/library.mp3", None)))
    ctx = types.SimpleNamespace(project_id=7)
    assert bed.project_asset(ctx, None, bed.chosen(work))[0] == "music/7/library.mp3"
    bed.items(work, 32.0)
    assert bed.project_asset(ctx, None, bed.chosen(work))[0] == "music/7/cut.m4a" and asked == ["library", "cut"]
    if real is not None:
        monkeypatch.setitem(sys.modules, "music_choose", real)
    # the track that cannot be had, or a song that cannot carry the film: the plain bed plays, with its drop, and the reason is on file
    os.remove(os.path.join(work, bed.TRACK_FILE))
    monkeypatch.setitem(sys.modules, "storage", types.SimpleNamespace(download_to=lambda k, p: (_ for _ in ()).throw(OSError("not in storage"))))
    monkeypatch.setitem(sys.modules, "music_choose", types.SimpleNamespace(find=lambda cid: {"storage_key": "music-library/t-one.mp3"}))
    plain = dict(doc)
    bed._write(work, plain)
    assert bed.arranged(work, plain, l1, index, f, 14.1) is None
    assert "not in storage" in json.load(open(os.path.join(work, bed.FILE)))["arranged_error"]
    music, fx = bed.items(work, 32.0)
    assert music[0]["gain_db"] == -14.0 and music[0]["offset_s"] == 8.0 and len(fx) == 1 and fx[0]["kind"] == "hole"


@needs_ffmpeg
def test_a_film_nobody_speaks_in_gets_the_music_as_its_lead(songs, tmp_path):
    l1, _index = _film(20.0)
    arr = MF.bake_bed(str(tmp_path), {"cid": "t-one"}, l1, {"words": []}, songs["full"], turn_s=None, lead=True)
    assert arr["under_voice_db"] is None and arr["drop_at"] is None and arr["intro_bars"] == 0 and arr["end_s"] <= 20.0 - MU.PROFILE["end_gap_s"] + 1e-6
    x = MU._decode(arr["path"], MU.SR, 2)
    k = MU._k_frames(x[:int(18 * MU.SR)], 480)
    assert np.percentile(k, 90) > MU.VOICE_LUFS - 3.0                    # at the level a voice would have had


def test_the_arranged_bed_is_the_projects_asset_with_its_licence(tmp_path, monkeypatch):
    path = tmp_path / "bed_arranged.m4a"
    path.write_bytes(b"aac")
    up, rows = [], []
    monkeypatch.setitem(sys.modules, "storage", types.SimpleNamespace(upload_file=lambda p, k, ct=None: up.append((k, ct))))
    monkeypatch.setitem(sys.modules, "db", types.SimpleNamespace(asset_by_key="asset_by_key", insert_asset="insert_asset"))
    track = {"cid": "t-one", "title": "One", "author": "someone", "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
             "credit": "\"One\" by someone (CC BY 4.0)", "storage_key": "music-library/t-one.mp3", "source_url": "https://incompetech.com/one.mp3", "source_sha256": "0" * 64}
    monkeypatch.setitem(sys.modules, "music_choose", types.SimpleNamespace(find=lambda cid: track if cid == "t-one" else None))

    class Db:
        def run(self, fn, *a, **k):
            if fn == "insert_asset":
                rows.append((a, k))
            return None
    ctx = types.SimpleNamespace(project_id=7, db=Db())
    key, err = MF.ensure_arranged_asset(ctx, {"cid": "t-one", "arranged": {"sha": "abc123", "end_s": 31.9, "drop_at": 14.1}}, str(path))
    assert err is None and key == "music/7/library-t-one-cut-abc123.m4a" and up == [(key, "audio/mp4")]
    meta = rows[0][1]["meta"]
    assert meta["chosen_by"] == "valmera" and meta["library_slug"] == "t-one" and meta["credit"] == track["credit"] and meta["license"] == "CC-BY-4.0" and meta["removable"]
    assert MF.ensure_arranged_asset(ctx, {"cid": "gone"}, str(path))[0] is None
