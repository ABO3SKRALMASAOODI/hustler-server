"""search_sfx / fetch_sfx — real recorded sounds, found on demand.

The bundled pack and AI sound generation are both deleted; what an edit
needs now is FETCHED: Openverse (fronting Freesound's half-million
recordings) searched by the sound's physical name, license terms labeled
per hit, downloads through net_fetch into ordinary project assets. These
tests pin the duration cap that keeps field recordings out of a one-shot
picker, the license reuse from music_search, and the tool-level contract
(same-turn ids, honest failures, add_sfx handoff).
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                             # noqa: E402
import config                                                  # noqa: E402
import sfx_search                                              # noqa: E402


def _hit(title="Camera Shutter", dur=0.3, lic="cc0"):
    return {"provider": "openverse", "id": "openverse:x", "title": title,
            "author": "A", "duration_s": dur, "license": lic,
            "page_url": "https://freesound.org/x",
            "_url": "https://cdn.freesound.org/previews/x-hq.mp3"}


def test_duration_cap_keeps_field_recordings_out(monkeypatch):
    rows = [{"id": "a", "title": "Click", "duration": 300, "license": "cc0",
             "url": "https://cdn/x.mp3", "creator": "A"},
            {"id": "b", "title": "One hour of rain", "duration": 3.6e6,
             "license": "cc0", "url": "https://cdn/y.mp3", "creator": "B"}]
    monkeypatch.setattr(sfx_search.net_fetch, "get_json",
                        lambda *a, **k: {"results": rows})
    hits = sfx_search.search("click")
    assert [h["title"] for h in hits] == ["Click"]      # 1hr filtered
    # ND is refused outright; NC passes and is labeled by the shared
    # music_search note (its own tests own the wording).
    rows[0]["license"] = "by-nc-nd"
    assert sfx_search.search("click") == []


def test_openverse_query_is_modification_only(monkeypatch):
    seen = {}

    def fake(url, params=None, **kw):
        seen.update(params or {})
        return {"results": []}
    monkeypatch.setattr(sfx_search.net_fetch, "get_json", fake)
    sfx_search.search("whoosh")
    assert seen.get("license_type") == "modification"


def test_verified_cc0_outage_catalog_survives_openverse_401(monkeypatch):
    monkeypatch.setattr(
        sfx_search.music_search, "_openverse_get_json",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 401")))
    hits = sfx_search.search("soft playful transition whoosh", max_s=2)
    assert hits and hits[0]["provider"] == "openverse"
    assert hits[0]["license"].startswith("cc0")
    assert hits[0]["page_url"].startswith("https://freesound.org/")


def test_outage_catalog_respects_duration_cap_and_physical_query(monkeypatch):
    monkeypatch.setattr(
        sfx_search.music_search, "_openverse_get_json",
        lambda *a, **k: {"results": []})
    assert sfx_search.search("camera shutter reveal", max_s=.5)
    assert not sfx_search.search("camera shutter reveal", max_s=.1)
    assert not sfx_search.search("unrelated abstract mood", max_s=3)


class _Db:
    def run(self, *a, **k):
        return None


class _Ctx:
    db = _Db()
    project_id = 3
    workdir = "/tmp"


def test_fetch_rejects_an_unrecoverable_result_id():
    ctx = _Ctx()
    out = agent_tools.fetch_sfx(ctx, "openverse:never-searched")
    assert out.startswith("REJECTED") and "Call search_sfx" in out


def test_search_results_hand_off_to_fetch_and_add(monkeypatch):
    monkeypatch.setattr(sfx_search, "search", lambda q, max_s=None: [_hit()])
    ctx = _Ctx()
    out = agent_tools.search_sfx(ctx, "camera shutter")
    assert "fetch_sfx(id)" in out and "add_sfx" in out
    assert "license terms" in out
    assert ctx._sfx_hits["openverse:x"]["title"] == "Camera Shutter"


def test_disabled_deployment_rejects_honestly(monkeypatch):
    monkeypatch.setattr(config, "SFX_SEARCH_ENABLED", False)
    out = agent_tools.search_sfx(_Ctx(), "whoosh")
    assert out.startswith("REJECTED") and "upload" in out


def test_failed_download_never_claims_success(monkeypatch):
    ctx = _Ctx()
    ctx._sfx_hits = {"openverse:x": _hit()}

    def boom(item, path):
        raise sfx_search.SfxSearchError("cdn said no")
    monkeypatch.setattr(sfx_search, "download", boom)
    out = agent_tools.fetch_sfx(ctx, "openverse:x")
    assert "Could not download" in out and "Do NOT claim" in out


def test_search_hit_cache_survives_the_agent_turn():
    hit = _hit()

    class CacheDb:
        def run(self, fn, *args):
            if fn.__name__ == "kv_get":
                return json.dumps({hit["id"]: hit})
            return None

    class CacheCtx:
        db = CacheDb()
        project_id = 44
        _sfx_hits = {}

    got, err = agent_tools._recover_search_hit(
        CacheCtx(), "sfx", hit["id"],
        lambda _rid: (_ for _ in ()).throw(AssertionError("no API call")))
    assert err is None and got["title"] == "Camera Shutter"


def test_editorial_ranking_prefers_clean_one_shot_over_music_and_loops(
        monkeypatch):
    rows = [
        {"id": "noisy", "title": "Cinematic Whoosh Music Loop Pack",
         "duration": 1400, "license": "cc0", "url": "https://cdn/noisy.mp3",
         "creator": "A"},
        {"id": "clean", "title": "Clean Cinematic Whoosh One Shot",
         "duration": 1200, "license": "cc0", "url": "https://cdn/clean.mp3",
         "creator": "B", "description": "dry transition foley"},
        {"id": "long", "title": "Whoosh ambience background",
         "duration": 12000, "license": "cc0", "url": "https://cdn/long.mp3",
         "creator": "C"},
    ]
    monkeypatch.setattr(sfx_search.net_fetch, "get_json",
                        lambda *a, **k: {"results": rows})
    hits = sfx_search.search("cinematic whoosh", count=3)
    assert hits[0]["id"] == "openverse:clean"
    assert hits[-1]["id"] != "openverse:clean"


def test_one_call_web_sfx_searches_fetches_and_places(monkeypatch, tmp_path):
    hit = _hit("Clean Cinematic Whoosh", 1.2)
    monkeypatch.setattr(sfx_search, "search",
                        lambda query, max_s=None, count=6: [hit])
    fetched = {"title": hit["title"], "duration_s": 1.2,
               "storage_key": "sfx/3/real.mp3", "license_note": "CC0",
               "hit": hit}
    monkeypatch.setattr(agent_tools, "_download_sfx_hit",
                        lambda ctx, selected: (fetched, None))
    audition = tmp_path / "candidate.mp3"
    audition.write_bytes(b"measured audio")
    monkeypatch.setattr(
        agent_tools, "_measure_sfx_candidate",
        lambda ctx, selected, need_file=False: (
            selected,
            {"active_duration_s": 1.1, "duration_s": 1.2,
             "leading_silence_s": 0, "attack_s": .25,
             "peak_position": .5, "tail_s": .5, "crest_db": 7,
             "spectral_centroid_hz": 2200, "bass_ratio": .08,
             "midband_ratio": .4, "strong_event_count": 1},
            str(audition), None))
    placed = {}

    def fake_add(_ctx, storage_key, at, gain_db, purpose=None):
        placed.update(storage_key=storage_key, at=at, gain_db=gain_db,
                      purpose=purpose)
        return "EDL v1 -> v2: sfx placed"

    monkeypatch.setattr(agent_tools, "add_sfx", fake_add)

    class Ctx(_Ctx):
        workdir = str(tmp_path)

        @staticmethod
        def latest_edl():
            return {"json": {"keep": [[0, 10]]}}

    out = agent_tools.add_web_sfx(Ctx(), "cinematic whoosh", 3.2, -7)
    assert out.startswith("EDL v1 -> v2")
    assert placed == {"storage_key": "sfx/3/real.mp3", "at": 3.2,
                      "gain_db": -7.0, "purpose": "cinematic whoosh"}
    assert "Clean Cinematic Whoosh" in out and "license: CC0" in out


def test_one_call_web_sfx_uses_actual_listener_when_choice_is_unambiguous(
        monkeypatch, tmp_path):
    first = _hit("Generic Whoosh", 1.2)
    first["id"] = "openverse:generic"
    second = _hit("Tailored Product Sweep", 1.1)
    second["id"] = "openverse:tailored"
    monkeypatch.setattr(
        sfx_search, "search",
        lambda query, max_s=None, count=6: [first, second])
    local_by_id = {}

    def measure(_ctx, selected, need_file=False):
        local = tmp_path / (selected["id"].split(":")[1] + ".mp3")
        local.write_bytes(b"actual sound")
        local_by_id[selected["id"]] = str(local)
        return (selected,
                {"active_duration_s": 1.0, "duration_s": 1.2,
                 "leading_silence_s": 0, "attack_s": .15,
                 "peak_position": .4, "tail_s": .5, "crest_db": 7,
                 "spectral_centroid_hz": 2200, "bass_ratio": .08,
                 "midband_ratio": .4, "strong_event_count": 1},
                str(local), None)

    chosen = {}

    def download(_ctx, selected):
        chosen["id"] = selected["id"]
        return ({"title": selected["title"], "duration_s": 1.1,
                 "storage_key": "sfx/3/chosen.mp3", "license_note": "CC0",
                 "hit": selected}, None)

    monkeypatch.setattr(agent_tools, "_measure_sfx_candidate", measure)
    monkeypatch.setattr(agent_tools, "_download_sfx_hit", download)
    monkeypatch.setattr(agent_tools.llm, "audio_review_available", lambda: True)
    heard_prompt = {}

    def listen(prompt, *args, **kwargs):
        heard_prompt["text"] = prompt
        return ("Choose openverse:tailored — its restrained sweep lands with "
                "the product motion and leaves room for speech.")

    monkeypatch.setattr(agent_tools.llm, "ask_audio", listen)
    monkeypatch.setattr(agent_tools, "add_sfx",
                        lambda *_args, **_kwargs: "EDL v1 -> v2: sfx placed")

    class Ctx(_Ctx):
        workdir = str(tmp_path)
        editing_metrics = {}
        edit_plan = {
            "steps": ["Build a coherent product reveal"],
            "objective": "make the result feel precise, not bombastic",
            "sfx_direction": "one restrained tactile digital family",
            "sequence_map": [{
                "role": "proof", "anchor": "interface resolves",
                "purpose": "make the interaction feel effortless",
                "sound": "a light glassy sweep; preserve the voice",
                "source_start_s": 2.5, "source_end_s": 4.0,
                "energy": .55,
            }, {
                "role": "ending", "anchor": "logo lockup",
                "purpose": "close with confidence",
                "sound": "near-silence, no impact",
                "source_start_s": 8.0, "source_end_s": 9.5,
                "energy": .2,
            }],
        }

        @staticmethod
        def latest_edl():
            return {"json": {"keep": [[0, 10]]}}

    ctx = Ctx()
    out = agent_tools.add_web_sfx(ctx, "restrained product UI sweep", 3.2)

    assert chosen["id"] == "openverse:tailored"
    assert "Actual listening selection" in out
    assert ctx.editing_metrics["sfx_candidates_heard"] == 2
    assert "one restrained tactile digital family" in heard_prompt["text"]
    assert "interface resolves" in heard_prompt["text"]
    assert "light glassy sweep" in heard_prompt["text"]
    assert "logo lockup" not in heard_prompt["text"]
    trace = ctx.editing_metrics["editorial_decisions"][0]
    assert trace["kind"] == "sfx_cast"
    assert trace["decision"] == "use"
    assert trace["candidate_id"] == "openverse:tailored"
    assert trace["asset_key"] == "sfx/3/chosen.mp3"
    assert trace["purpose"] == "restrained product UI sweep"
    assert trace["review_stage"] == "actual_audio"
    assert trace["source"] == "independent_listener"


def test_one_call_web_sfx_can_choose_professional_silence(
        monkeypatch, tmp_path):
    first = _hit("Heavy Trailer Slam", 1.2)
    first["id"] = "openverse:slam"
    second = _hit("Cartoon Pop", .7)
    second["id"] = "openverse:pop"
    monkeypatch.setattr(
        sfx_search, "search",
        lambda query, max_s=None, count=6: [first, second])

    def measure(_ctx, selected, need_file=False):
        local = tmp_path / (selected["id"].split(":")[1] + ".mp3")
        local.write_bytes(b"actual sound")
        return (selected,
                {"active_duration_s": 1.0, "duration_s": 1.2,
                 "leading_silence_s": 0, "attack_s": .08,
                 "peak_position": .2, "tail_s": .5, "crest_db": 8,
                 "spectral_centroid_hz": 2200, "bass_ratio": .2,
                 "midband_ratio": .4, "strong_event_count": 1},
                str(local), None)

    monkeypatch.setattr(agent_tools, "_measure_sfx_candidate", measure)
    monkeypatch.setattr(agent_tools.llm, "audio_review_available", lambda: True)
    monkeypatch.setattr(
        agent_tools.llm, "ask_audio",
        lambda *_args, **_kwargs: (
            '{"choice":"none","reason":"the unforced logo settle feels '
            'more premium without either exaggerated sound"}'))
    monkeypatch.setattr(
        agent_tools, "_download_sfx_hit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an abstention must not fetch an asset")))
    monkeypatch.setattr(
        agent_tools, "add_sfx",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an abstention must not write the EDL")))

    class Ctx(_Ctx):
        workdir = str(tmp_path)
        editing_metrics = {}
        edit_plan = {"sfx_direction": "quiet premium restraint"}

        @staticmethod
        def latest_edl():
            return {"json": {"keep": [[0, 10]]}}

    ctx = Ctx()
    out = agent_tools.add_web_sfx(ctx, "subtle logo settle", 6.0)

    assert out.startswith("SOUND DESIGN DECISION: KEEP THIS MOMENT DRY")
    assert "no SFX was fetched or placed" in out
    assert ctx.editing_metrics["sfx_candidates_heard"] == 2
    assert ctx.editing_metrics["sfx_abstentions"] == 1
    trace = ctx.editing_metrics["editorial_decisions"][0]
    assert trace["kind"] == "sfx_cast"
    assert trace["decision"] == "none"
    assert set(trace["candidate_ids"]) == {"openverse:slam", "openverse:pop"}
    assert trace["purpose"] == "subtle logo settle"
    assert trace["at"] == 6.0
    assert trace["review_stage"] == "actual_audio"
    assert not list(tmp_path.glob("*.mp3"))
