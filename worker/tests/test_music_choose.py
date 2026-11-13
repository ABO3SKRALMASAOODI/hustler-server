"""Valmera chooses the music (owner, 13 Nov 2026): the licensed library, the signal shortlist, the model's pick,
and the gate that lets the chosen track in and keeps it removable."""

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import music_choose as MC  # noqa: E402
import music_gate  # noqa: E402


def _z(seed, base=None):
    r = np.random.default_rng(seed).normal(size=32)
    if base is not None:
        r = base + 0.15 * r
    return [round(float(x), 4) for x in r / np.linalg.norm(r)]


STORY = np.random.default_rng(1).normal(size=32)
STORY /= np.linalg.norm(STORY)


def _track(cid, z, bpm=96.0, mode="minor", dur=180.0, rise_at=None, vocals=0.0, lic="CC-BY-4.0"):
    curve = [-24] * int(dur)
    secs = [{"t0": 0.0, "t1": float(dur), "label": "body", "db": -24.0}]
    if rise_at is not None:
        for i in range(int(rise_at), int(dur)):
            curve[i] = -14
        secs = [{"t0": 0.0, "t1": float(rise_at), "label": "verse", "db": -24.0},
                {"t0": float(rise_at), "t1": float(dur), "label": "chorus", "db": -14.0}]
    return {"cid": cid, "title": cid.title(), "author": "A", "source": "test", "license": lic,
            "license_url": "https://creativecommons.org/licenses/by/4.0/", "credit": f"\"{cid}\" by A, CC-BY 4.0",
            "storage_key": f"music-library/{cid}.mp3", "source_url": f"https://incompetech.com/x/{cid}.mp3",
            "source_sha256": "a" * 64, "lufs": -14.0, "duration_s": dur, "bpm": bpm, "mode": mode, "tags": ["Orchestra"],
            "vocals": vocals, "style": 0, "z": z, "curve": curve, "sections": secs,
            "downbeats": [round(i * 2.5, 2) for i in range(int(dur / 2.5))], "phrase_ends": [s["t1"] for s in secs[:-1]],
            "drops": []}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    tracks = [_track("near-story", _z(2, STORY), rise_at=40.0), _track("far-away", _z(9)),
              _track("sung-song", _z(3, STORY), vocals=0.6), _track("near-story-two", _z(4, STORY), bpm=150.0),
              {"cid": "Bad Key", "storage_key": "elsewhere/x.mp3", "license": "CC-BY-4.0", "credit": "x", "z": _z(5)},
              dict(_track("no-credit", _z(6, STORY)), credit=""),
              dict(_track("nc-licence", _z(7, STORY)), license="CC-BY-NC-4.0"),
              dict(_track("other-host", _z(8, STORY)), source_url="https://example.com/a.mp3")]
    profiles = {"kinds": {"story": {"n": 3, "bpm": {"p25": 85, "p50": 95, "p75": 110}, "minor_share": 0.8,
                                    "shape10": [-2, -1, -1, 0, 0, 1, 2, 2, 3, 1],
                                    "voice_over_music_db": {"p25": 6, "p50": 9, "p75": 12}, "bed_lift_db": {"p50": 3},
                                    "turns": {"climax": {"median_db": 4.0}}, "endings": {"fade": 3, "hit_and_tail": 5},
                                    "top_tags": {"Orchestra": 0.2},
                                    "refs": [{"id": f"r{i}", "z": _z(10 + i, STORY)} for i in range(3)]}}}
    m, p = tmp_path / "manifest.json", tmp_path / "profiles.json"
    m.write_text(json.dumps({"tracks": tracks}))
    p.write_text(json.dumps(profiles))
    monkeypatch.setattr(MC, "MANIFEST_PATH", str(m))
    monkeypatch.setattr(MC, "PROFILES_PATH", str(p))
    MC._CACHE.clear()
    yield
    MC._CACHE.clear()


FILM = {"kind": "story", "duration_s": 60.0, "turns": [{"t": 6.0, "role": "hook_end"}, {"t": 40.0, "role": "climax"}],
        "pace": {"cuts10": 5.0}, "speech_share": 0.85, "brief": "a founder bets the company"}


def test_only_licensed_credited_rows_from_the_library_prefix(lib):
    ids = {t["cid"] for t in MC.tracks()}
    assert ids == {"near-story", "far-away", "sung-song", "near-story-two"}


def test_shortlist_prefers_the_references_and_aligns_the_lift_with_the_turn(lib):
    opts, _prof = MC.shortlist(FILM)
    assert opts[0]["cid"] == "near-story"
    sec = opts[0]["section"]
    assert sec["start_s"] == 0.0 and sec["end_s"] == 60.0
    assert any(a["what"].startswith("lift on the climax") for a in sec["align"])
    # sung words under a talking voice rank below the instrumental tracks near the references
    prof = MC.profiles()["story"]
    fit = {t["cid"]: MC.track_fit(t, prof, FILM)[0] for t in MC.tracks()}
    assert fit["sung-song"] < fit["near-story"] and fit["far-away"] < fit["near-story"]
    # with no voice the song is no longer pushed back
    quiet = dict(FILM, speech_share=0.0)
    assert MC.track_fit(MC.find("sung-song"), prof, quiet)[1]["vocal_penalty"] == 0.0


def test_the_model_picks_among_the_shortlist_and_its_reason_is_kept(lib):
    seen = {}

    def call(system, user, schema):
        seen["user"] = user
        return {"option": 2, "why": "brighter pulse for the cutting"}
    ch = MC.choose(FILM, call=call)
    opts, _ = MC.shortlist(FILM)
    assert ch["cid"] == opts[1]["cid"] and ch["chosen_by"] == "model"
    assert ch["why"] == "brighter pulse for the cutting"
    assert "a founder bets the company" in seen["user"] and "climax at 40.0 s" in seen["user"]
    assert ch["storage_key"].startswith("music-library/") and ch["credit"] and ch["removable"] is True
    assert ch["levels"]["voice_over_music_db"] == 9 and ch["ending"] == "hit_and_tail"


def test_a_failed_or_out_of_range_model_answer_keeps_the_signal_choice(lib):
    def boom(*_a):
        raise RuntimeError("no model")
    assert MC.choose(FILM, call=boom)["chosen_by"] == "signal"
    assert MC.choose(FILM, call=lambda *a: {"option": 99, "why": "x"})["chosen_by"] == "signal"


def test_tracks_the_customer_already_has_are_avoided(lib):
    assert MC.choose(FILM, used=["near-story"], call=lambda *a: {"option": 1, "why": ""})["cid"] != "near-story"


def test_no_voice_makes_the_music_the_lead(lib):
    film = dict(FILM, speech_share=0.05)
    assert MC.levels(MC.profiles()["story"], film)["lead"] is True


def test_empty_library_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(MC, "MANIFEST_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(MC, "PROFILES_PATH", str(tmp_path / "missing2.json"))
    MC._CACHE.clear()
    assert MC.choose(FILM) is None
    MC._CACHE.clear()


def test_gate_admits_a_chosen_library_copy_and_keeps_unpicked_library_refused():
    key = "music/12/library-near-story.mp3"
    chosen = {"kind": "music", "storage_key": key, "meta": {"library_slug": "near-story", "chosen_by": "valmera"}}
    prov, err = music_gate.decide(key, chosen, is_picked=lambda s: False, seen_before=lambda k: False)
    assert (prov, err) == ("valmera_chosen", None)
    plain = {"kind": "music", "storage_key": key, "meta": {"library_slug": "near-story"}}
    prov, err = music_gate.decide(key, plain, is_picked=lambda s: False, seen_before=lambda k: False)
    assert prov is None and err
    assert "valmera_chosen" in music_gate.PROVENANCES


def test_real_library_files_validate_when_present():
    if not os.path.exists(os.path.join(MC.LIB_DIR, "manifest.json")):
        pytest.skip("no library in this checkout")
    MC._CACHE.clear()
    rows = MC.tracks()
    assert len(rows) >= 100
    assert all(r["storage_key"] == f"music-library/{r['cid']}.mp3" for r in rows)
    assert set(MC.profiles()) >= {"story", "longform"}
    MC._CACHE.clear()


def test_library_audio_is_fetched_from_its_publisher_and_kept_only_when_its_hash_matches(monkeypatch):
    import hashlib

    import net_fetch
    import storage
    body = b"ID3 fake mp3 bytes"
    uploads, calls = [], []
    monkeypatch.setattr(storage, "exists", lambda key: False)
    monkeypatch.setattr(storage, "upload_file", lambda path, key, ct: uploads.append((key, ct, open(path, "rb").read())))

    def fake_download(url, out, allowed_hosts=None, max_bytes=None, timeout_s=None):
        calls.append((url, tuple(allowed_hosts or ())))
        open(out, "wb").write(body)
    monkeypatch.setattr(net_fetch, "download", fake_download)
    t = _track("near-story", _z(2, STORY))
    t["source_sha256"] = hashlib.sha256(body).hexdigest()
    assert MC.ensure_library_object(t) == (True, None)
    assert uploads == [("music-library/near-story.mp3", "audio/mpeg", body)]
    assert calls[0][1] == MC.SOURCE_HOSTS
    t["source_sha256"] = "b" * 64
    ok, err = MC.ensure_library_object(t)
    assert not ok and "changed" in err and len(uploads) == 1


def test_a_song_is_fetched_on_demand_only_where_the_references_carry_songs_and_the_voice_rests(lib, monkeypatch):
    import music_search
    prof = MC.profiles()["story"]
    film = dict(FILM, speech_share=0.1)
    assert MC.songs_on_demand(film, call=lambda *a: {"query": "x"}) == []          # this kind's references carry no song
    prof["sung_share"] = 0.4
    assert MC.songs_on_demand(FILM, call=lambda *a: {"query": "x"}) == []          # the voice talks over most of it
    hits = [{"id": "a", "ok": True}, {"id": "b", "ok": False}]
    seen = {}

    def fake_search(q, min_s=None, commercial_only=False, count=6):
        seen.update(q=q, min_s=min_s, commercial_only=commercial_only)
        return hits
    monkeypatch.setattr(music_search, "search", fake_search)
    monkeypatch.setattr(music_search, "usable", lambda h, commercial_only=False: h["ok"])
    got = MC.songs_on_demand(film, call=lambda *a: {"query": "dark   trap anthem"})
    assert [h["id"] for h in got] == ["a"]
    assert seen == {"q": "dark trap anthem", "min_s": 60.0, "commercial_only": True}


def test_long_form_gets_one_track_per_chapter_without_repeats(lib):
    film = dict(FILM, kind="story", duration_s=120.0, turns=[{"t": 40.0, "role": "climax"}, {"t": 100.0, "role": "climax"}])
    seq = MC.choose_sequence(film, [(0, 60), (60, 120)], call=lambda *a: {"option": 1, "why": ""})
    assert len(seq) == 2 and seq[0]["cid"] != seq[1]["cid"]
    assert (seq[1]["film_in_s"], seq[1]["film_out_s"]) == (60.0, 120.0)


def test_the_engine_gets_a_local_file_and_its_own_word_for_the_kind(lib, monkeypatch):
    import storage
    got = {}
    monkeypatch.setattr(MC, "ensure_library_object", lambda t: (True, None))
    monkeypatch.setattr(storage, "download_to", lambda key, path, **k: got.update(key=key, path=path))
    ch = MC.choose(FILM, call=lambda *a: {"option": 1, "why": ""})
    assert MC.song_file(ch, "/tmp/x.mp3") == "/tmp/x.mp3" and got["key"] == ch["storage_key"]
    assert MC.ENGINE_KIND[ch["kind"]] == "stories"
    monkeypatch.setattr(MC, "ensure_library_object", lambda t: (False, "gone"))
    with pytest.raises(RuntimeError):
        MC.song_file(ch, "/tmp/x.mp3")
