"""Floor packages F3, F4, U2 and U3 (6 Nov).

F3: analysis times its own stages into the job result. F4: a proxy carries a
keyframe every 30 frames. U2: the legal lines of the libraries. U3: a parallax
crop keeps its subject.
"""

import json
import shutil
import subprocess
import threading

import numpy as np
import pytest

import broll_source
import config
import depth_fx
import indexer
import media
import music_rights
import music_search
import net_fetch
import sfx_search
import stock


# ── F3: stage timers ─────────────────────────────────────────────────────

def test_clock_keeps_a_stage_per_name_across_threads_and_retries():
    clock = indexer._StageClock()
    done = threading.Event()

    def stage():
        with clock.stage("snd_asr_s"):
            done.wait(0.02)

    threads = [threading.Thread(target=stage) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert clock.timed("pic_shots_s", lambda a, b=0: a + b, 2, b=3) == 5
    assert set(clock.seconds) == {"snd_asr_s", "pic_shots_s"}
    assert clock.seconds["snd_asr_s"] >= 0.05


def test_a_failing_stage_still_reports_its_time():
    clock = indexer._StageClock()
    with pytest.raises(ValueError):
        clock.timed("pic_motion_s", lambda: (_ for _ in ()).throw(ValueError()))
    assert "pic_motion_s" in clock.seconds


def test_stages_are_in_the_index_job_result(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=30:duration=2", "-f", "lavfi",
                    "-i", "sine=frequency=300:duration=2", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", str(clip)], check=True)
    import perception
    import sidecars
    import transcript_verify
    tile = tmp_path / "tile.jpg"
    tile.write_bytes(b"x")
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "scratch"))
    monkeypatch.setattr(indexer.storage, "download_to",
                        lambda key, path: shutil.copy(clip, path))
    monkeypatch.setattr(indexer.storage, "upload_file", lambda *a, **k: None)
    monkeypatch.setattr(indexer, "_finish_setup", lambda *a, **k: None)
    monkeypatch.setattr(indexer.transcribe, "transcribe",
                        lambda *a, **k: ([], "en"))
    monkeypatch.setattr(transcript_verify, "verify", lambda w, *a: w)
    monkeypatch.setattr(indexer.tilestrip, "build_for_video_with_frames",
                        lambda *a, **k: ([(str(tile), 0.0, 2.0)], 2, [], []))
    monkeypatch.setattr(indexer.spatial, "analyze_frames",
                        lambda *a, **k: None)
    monkeypatch.setattr(indexer.spatial, "augment_with_shot_frames",
                        lambda *a, **k: None)
    monkeypatch.setattr(indexer.motion_judge, "analyze_video",
                        lambda *a: None)
    monkeypatch.setattr(indexer.source_layout, "analyze",
                        lambda *a, **k: None)
    monkeypatch.setattr(indexer.visual_index, "build", lambda *a, **k: None)
    monkeypatch.setattr(perception, "analyze_audio", lambda *a: None)
    monkeypatch.setattr(sidecars, "prefetch_after_index",
                        lambda *a, **k: None)

    class Db:
        def run(self, fn, *args, **kw):
            if fn is indexer.dbx.get_asset:
                return {"id": 3, "kind": "original", "bytes": 10,
                        "storage_key": "original/key.mp4"}
            if fn is indexer.dbx.get_project:
                return {"chat_session_id": 5}
            if fn is indexer.dbx.set_progress:
                return True
            if fn is indexer.dbx.insert_asset:
                return 10
            return None

    result = indexer.run_index_job(Db(), {
        "id": 7, "project_id": 2, "user_id": 1, "payload": {"asset_id": 3}})
    timings = result["timings"]
    for stage in ("pic_lane_s", "snd_lane_s", "pic_shots_s", "pic_motion_s",
                  "pic_tiles_s", "pic_strip_s", "pic_spatial_s",
                  "pic_tile_upload_s", "pic_proxy_upload_s",
                  "pic_shot_spatial_s", "pic_layout_s", "snd_wav_s",
                  "snd_upload_s", "snd_asr_s", "snd_verify_s",
                  "snd_silences_s", "snd_perception_s", "storyboard_s",
                  "finish_setup_s", "probe_s", "cache_lookup_s"):
        assert stage in timings, stage
    # what the admin views already read stays
    for old in ("proxy_s", "wav_s", "lanes_s", "upload_persist_s"):
        assert old in timings
    json.dumps(timings)                        # it lands in a JSON column


# ── F4: proxy keyframes ──────────────────────────────────────────────────

def _clip(path, seconds=3, fps=30):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x240:rate={fps}:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-x264-params", "keyint=300", "-pix_fmt", "yuv420p",
                    str(path)], check=True)


def _key_frames(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "frame=key_frame", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.split()
    return [i for i, k in enumerate(out) if k.strip(",") == "1"]


def test_a_proxy_has_a_keyframe_every_30_frames(tmp_path):
    src, dst = tmp_path / "s.mp4", tmp_path / "p.mp4"
    _clip(src)
    media.make_proxy(str(src), str(dst), 30.0, False, False, duration=3.0)
    assert _key_frames(dst) == [0, 30, 60]
    assert config.PROXY_KEYINT == 30


def test_an_old_proxy_with_a_long_gop_still_seeks(tmp_path, monkeypatch):
    src, old = tmp_path / "s.mp4", tmp_path / "old.mp4"
    _clip(src)
    monkeypatch.setattr(config, "PROXY_KEYINT", 0)
    media.make_proxy(str(src), str(old), 30.0, False, False, duration=3.0)
    assert _key_frames(old) == [0]
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "2.0", "-i", str(old), "-frames:v",
         "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True).stdout
    assert len(frame) >= 320 * 180


# ── U2.1: the Internet Archive lane ──────────────────────────────────────

# A recorded answer (archive.org advancedsearch, 6 Nov): Digimon as "public
# domain" in the open upload collection, and what the allow-listed ones return.
IA_RECORDED = {"response": {"docs": [
    {"identifier": "digimon-savers-sigla-ita",
     "title": "Digimon Savers - SIgla Ita",
     "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
     "collection": ["opensource_movies", "community"]},
    {"identifier": "opening-to-toy-story-2-2000-vhs",
     "title": "Opening to Toy Story 2 2000 VHS",
     "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
     "collection": ["vhsopenings", "vhsvault", "fav-someone"]},
    {"identifier": "no-collection-at-all", "title": "Nothing says where",
     "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/"},
    {"identifier": "sylacauga_saga",
     "title": "Industry on Parade: Sylacauga Saga",
     "licenseurl": "http://creativecommons.org/licenses/publicdomain/",
     "collection": ["prelinger", "fav-chadmw"]},
    {"identifier": "universal-newsreel-1", "title": "Universal Newsreel",
     "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
     "collection": "universal_newsreels"},
]}}


def test_an_uploader_licence_outside_the_collections_is_refused(monkeypatch):
    asked = {}

    def fake(url, params=None, **kw):
        asked.update(params)
        return IA_RECORDED
    monkeypatch.setattr(net_fetch, "get_json", fake)
    hits = broll_source._archive("factory", "video", "portrait", 4)
    assert [h["_ident"] for h in hits] == ["sylacauga_saga",
                                           "universal-newsreel-1"]
    # asked of the Archive as well, so the page is not spent on refused items
    assert "collection:(prelinger OR FedFlix OR nasa OR universal_newsreels)" \
        in asked["q"]
    assert "collection" in asked["fl[]"]


# ── U2.2: one licence rule ───────────────────────────────────────────────

@pytest.mark.parametrize("lic,ok", [
    ("cc0", True), ("pdm", True), ("by", True), ("by-sa", True),
    ("by-nc", False), ("by-nc-sa", False), ("by-nc-nd", False),
    ("by-nd", False), ("sampling+", False), ("", False), (None, False),
    ("https://creativecommons.org/licenses/by-nc/4.0/", False),
    ("https://creativecommons.org/licenses/by/4.0/", True)])
def test_one_licence_rule(lic, ok):
    assert music_rights.commercial_ok(lic) is ok


def _openverse_page(*licences):
    return {"results": [
        {"id": f"p{i}", "title": f"Photo {i}", "creator": "C", "license": lic,
         "url": f"https://live.staticflickr.com/{i}.jpg", "width": 2000,
         "height": 3000, "source": "flickr"}
        for i, lic in enumerate(licences)]}


def test_an_nc_openverse_photo_never_reaches_a_customer(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    seen = {}

    def fake(url, params=None, **kw):
        seen.update(params or {})
        return _openverse_page("by-nc", "by-nc-sa", "by", "by-nc-nd", "cc0")
    monkeypatch.setattr(music_search, "_openverse_get_json", fake)
    hits = stock.search("harbour", kind=stock.KIND_PHOTO,
                        orientation="portrait")
    assert sorted(h["license"] for h in hits) == ["by", "cc0"]
    assert seen["license_type"] == "commercial,modification"
    # the B-roll lane reads the same lane function
    lane = broll_source.LANES["openverse"]("harbour", "photo", "portrait", 4)
    assert sorted(c["license"] for c in lane) == ["CC BY", "CC0 1.0"]


def test_an_nc_sound_is_not_offered(monkeypatch):
    rows = [{"id": "nc", "title": "Whoosh NC", "duration": 900,
             "license": "by-nc", "url": "https://cdn/nc.mp3", "creator": "A"},
            {"id": "by", "title": "Whoosh BY", "duration": 900,
             "license": "by", "url": "https://cdn/by.mp3", "creator": "B"}]
    monkeypatch.setattr(music_search, "_openverse_get_json",
                        lambda *a, **k: {"results": rows})
    assert [h["title"] for h in sfx_search.search("whoosh")] == ["Whoosh BY"]


def test_search_music_never_opens_the_nc_catalog(monkeypatch):
    import agent_tools
    seen = {}

    def fake(query, **kw):
        seen.update(kw)
        return []
    monkeypatch.setattr(music_search, "available", lambda: True)
    monkeypatch.setattr(music_search, "search", fake)

    class Ctx:
        pass
    agent_tools.search_music(Ctx(), "lofi", commercial_use=False)
    assert seen["commercial_only"] is True


# ── U2.3: Commons' AI items ──────────────────────────────────────────────

def _commons(n, **more):
    return dict({"id": f"wikimedia:photo:{n}", "provider": "wikimedia",
                 "kind": "photo", "title": f"File {n}"}, **more)


@pytest.fixture(autouse=True)
def _no_memory():
    broll_source._AI_MADE.clear()
    broll_source._WIKIDATA_IDS.clear()
    yield
    broll_source._AI_MADE.clear()
    broll_source._WIKIDATA_IDS.clear()


def _model_says(monkeypatch, *replies):
    import llm
    asked = []

    def fake(system, user, **kw):
        asked.append((system, json.loads(user), kw))
        reply = replies[min(len(asked), len(replies)) - 1]
        return None if reply is None else {"text": reply}
    monkeypatch.setattr(llm, "ask_text", fake)
    return asked


def test_a_model_reads_commons_metadata_and_its_ai_items_go(monkeypatch):
    asked = _model_says(monkeypatch, "[2]")
    real = _commons(1, title="Rocket on its pad")
    fake = _commons(2, title="A rocket", _categories="Some category",
                    _source_note="Own work")
    pexels = {"id": "pexels:photo:1", "provider": "pexels", "kind": "photo"}
    kept, note = broll_source.drop_ai_made([real, fake, pexels])
    assert [c["id"] for c in kept] == [real["id"], pexels["id"]]
    assert note is None
    rows = asked[0][1]
    assert len(rows) == 2                      # only Commons is read
    assert rows[1]["categories"] == "Some category"
    assert rows[1]["source"] == "Own work"
    assert asked[0][2]["purpose"] == "commons_ai_items"


def test_the_same_file_through_openverse_is_read_too(monkeypatch):
    _model_says(monkeypatch, "[1]")
    via_openverse = {"id": "openverse:photo:1", "provider": "openverse",
                     "_origin": "wikimedia", "title": "A portrait"}
    flickr = {"id": "openverse:photo:2", "provider": "openverse",
              "_origin": "flickr", "title": "A portrait"}
    kept, _note = broll_source.drop_ai_made([via_openverse, flickr])
    assert [c["id"] for c in kept] == ["openverse:photo:2"]


def test_a_verdict_is_not_paid_for_twice(monkeypatch):
    asked = _model_says(monkeypatch, "[]")
    item = _commons(1)
    broll_source.drop_ai_made([item])
    kept, _note = broll_source.drop_ai_made([item])
    assert kept == [item] and len(asked) == 1


@pytest.mark.parametrize("reply", [None, "I cannot tell.", "[99]", '["x"]'])
def test_with_no_reading_commons_stays_out_and_says_so(monkeypatch, reply):
    _model_says(monkeypatch, reply)
    pexels = {"id": "pexels:photo:1", "provider": "pexels"}
    kept, note = broll_source.drop_ai_made([_commons(1), pexels])
    assert kept == [pexels]
    assert "left out" in note
    assert not broll_source._AI_MADE           # the next search asks again


def test_a_thread_with_no_turn_reads_on_the_claude_lane(monkeypatch):
    import claude_lane
    import llm
    monkeypatch.setattr(llm, "claude_trial_lane", lambda: None)
    monkeypatch.setattr(llm, "turn_plan", lambda: "")
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    asked = _model_says(monkeypatch, "[]")
    broll_source.drop_ai_made([_commons(1)])
    client, model = asked[0][2]["lane"]
    assert model == config.CLAUDE_TRIAL_MODEL and client.is_claude_lane


def test_a_turn_reads_on_its_own_lane(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "claude_trial_lane", lambda: ("client", "m"))
    asked = _model_says(monkeypatch, "[]")
    broll_source.drop_ai_made([_commons(1)])
    assert asked[0][2]["lane"] is None


def test_ask_text_takes_the_callers_lane(monkeypatch):
    import llm
    from types import SimpleNamespace as NS
    monkeypatch.setattr(llm, "claude_trial_lane", lambda: None)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    used = {}

    def fake(client, model, messages, **kw):
        used.update(client=client, model=model)
        return NS(choices=[NS(message=NS(content="[]"))],
                  usage=NS(prompt_tokens=1, completion_tokens=1))
    monkeypatch.setattr(llm, "create_with_dialect", fake)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    got = llm.ask_text("s", "u", lane=("the client", "claude-x"))
    assert got["text"] == "[]" and used == {"client": "the client",
                                            "model": "claude-x"}


def test_stock_search_reads_openverse_commons_files(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    page = _openverse_page("by", "cc0")
    page["results"][0]["source"] = "wikimedia"
    page["results"][0]["tags"] = [{"name": "ai generated"}]
    monkeypatch.setattr(music_search, "_openverse_get_json",
                        lambda *a, **k: page)
    asked = _model_says(monkeypatch, "[1]")
    hits = stock.search("harbour", kind=stock.KIND_PHOTO)
    assert [h["id"] for h in hits] == ["openverse:photo:p1"]
    assert asked[0][1][0]["categories"] == "ai generated"


def test_stock_search_says_why_when_only_commons_had_an_answer(monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    page = _openverse_page("by")
    page["results"][0]["source"] = "wikimedia"
    monkeypatch.setattr(music_search, "_openverse_get_json",
                        lambda *a, **k: page)
    _model_says(monkeypatch, None)
    with pytest.raises(stock.StockError, match="AI check"):
        stock.search("harbour", kind=stock.KIND_PHOTO)


def test_source_broll_reads_one_board_in_one_call(monkeypatch):
    from tools_ext import broll_tools as bt
    asked = _model_says(monkeypatch, "[1, 3]")
    a = [_commons(i) for i in range(1, 7)]
    pex = {"id": "pexels:video:1", "provider": "pexels"}
    found = bt._without_ai_made([({"id": "m1"}, a + [pex], []),
                                 ({"id": "m2"}, [a[0], _commons(9), pex], [])])
    assert len(asked) == 1
    # the head of each slate was read (four, then two): 1 and 3 are AI, and
    # the unread tail of a slate goes with them
    assert [c["id"] for c in found[0][1]] == [
        "wikimedia:photo:2", "wikimedia:photo:4", "pexels:video:1"]
    assert [c["id"] for c in found[1][1]] == ["wikimedia:photo:9",
                                              "pexels:video:1"]
    assert asked[0][1][4]["title"] == "File 9"


def test_source_broll_tells_the_agent_why_commons_is_missing(monkeypatch):
    from tools_ext import broll_tools as bt
    _model_says(monkeypatch, None)
    found = bt._without_ai_made([({"id": "m1"}, [_commons(1)], [])])
    assert found[0][1] == [] and "AI check" in found[0][2][0]


# ── U2.4: Commons by what a file depicts ─────────────────────────────────

def _page(pid, title):
    return {"pageid": pid, "title": f"File:{title}.jpg",
            "index": pid, "imageinfo": [{
                "url": f"https://upload.wikimedia.org/{pid}.jpg",
                "mime": "image/jpeg", "width": 3000, "height": 4000,
                "descriptionurl": f"https://commons.wikimedia.org/{pid}",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/"
                                            "licenses/by-sa/4.0/"},
                    "ObjectName": {"value": title},
                    "Categories": {"value": "A|B"},
                    "Credit": {"value": "Own work"}}}]}


def _wikis(monkeypatch, entity):
    searched = []

    def fake(url, params=None, **kw):
        if "wikidata" in url:
            return {"search": [entity] if entity else []}
        text = params["gsrsearch"]
        searched.append(text)
        if "haswbstatement" in text:
            return {"query": {"pages": {"7": _page(7, "DSC_0042"),
                                        "8": _page(8, "Both lists")}}}
        return {"query": {"pages": {"8": _page(8, "Both lists"),
                                    "9": _page(9, "Text hit")}}}
    monkeypatch.setattr(net_fetch, "get_json", fake)
    return searched


MUSK = {"id": "Q317521", "label": "Elon Musk",
        "match": {"type": "label", "language": "en", "text": "Elon Musk"}}


def test_a_named_query_searches_the_depicts_statement_first(monkeypatch):
    searched = _wikis(monkeypatch, MUSK)
    hits = broll_source._wikimedia("Elon Musk", "photo", "portrait", 4)
    assert "haswbstatement:P180=Q317521 filetype:bitmap" in searched
    assert "Elon Musk filetype:bitmap" in searched
    assert [c["id"].rsplit(":", 1)[1] for c in hits] == ["7", "8", "9"]
    # a file named DSC_0042 is still known to show him, and ranks so
    assert hits[0]["keywords"] == ["Elon Musk"]
    assert broll_source.relevance(hits[0], ["Elon Musk"]) == 1.0
    assert "keywords" not in hits[2]
    assert hits[0]["_categories"] == "A|B" and \
        hits[0]["_source_note"] == "Own work"


def test_a_described_shot_stays_a_text_search(monkeypatch):
    searched = _wikis(monkeypatch, None)
    hits = broll_source._wikimedia("rocket at dusk on the pad", "photo",
                                   "portrait", 4)
    assert searched == ["rocket at dusk on the pad filetype:bitmap"]
    assert [c["id"].rsplit(":", 1)[1] for c in hits] == ["8", "9"]


def test_a_prefix_of_a_name_is_not_that_name(monkeypatch):
    searched = _wikis(monkeypatch, MUSK)
    broll_source._wikimedia("Elon", "photo", "portrait", 4)
    assert searched == ["Elon filetype:bitmap"]


def test_wikidata_down_costs_nothing_but_the_first_tier(monkeypatch):
    searched = []

    def fake(url, params=None, **kw):
        if "wikidata" in url:
            raise net_fetch.FetchError("HTTP 503")
        searched.append(params["gsrsearch"])
        return {"query": {"pages": {"9": _page(9, "Text hit")}}}
    monkeypatch.setattr(net_fetch, "get_json", fake)
    hits = broll_source._wikimedia("Elon Musk", "photo", "portrait", 4)
    assert len(hits) == 1 and len(searched) == 1
    assert "elon musk" not in broll_source._WIKIDATA_IDS  # asked again later


def test_a_moment_may_name_what_it_depicts(monkeypatch):
    from tools_ext import broll_tools as bt
    assert "depicts" in bt._MOMENT["properties"]


# ── U3: the parallax crop keeps its subject ──────────────────────────────

def _still(w=1600, h=1200):
    return np.full((h, w, 3), 90, np.uint8)


def test_a_wide_still_crops_to_its_face(monkeypatch):
    import broll_score
    monkeypatch.setattr(broll_score, "faces",
                        lambda rgb: [(800.0, 40.0, 40.0, 40.0, 0.8)])
    fx, fy = depth_fx.still_focus(_still(), 1080, 1920)
    assert fy == 0.5
    # the 9:16 window is 0.42 of the width: the face at 0.85 sits in its
    # middle band, not on its edge
    x0 = fx - 0.21
    assert x0 + 0.3 * 0.42 <= 0.854 <= x0 + 0.7 * 0.42


def test_a_tall_still_keeps_a_face_high_in_a_landscape_crop(monkeypatch):
    import broll_score
    monkeypatch.setattr(broll_score, "faces",
                        lambda rgb: [(300.0, 1000.0, 60.0, 60.0, 0.8)])
    fx, fy = depth_fx.still_focus(_still(900, 1600), 1920, 1080)
    assert fx == 0.5
    win = (900 / (1920 / 1080)) / 1600.0
    y0 = fy - win / 2
    assert y0 + 0.2 * win <= 1030 / 1600.0 <= y0 + 0.6 * win


def test_the_larger_face_wins_when_both_cannot_fit(monkeypatch):
    import broll_score
    monkeypatch.setattr(broll_score, "faces", lambda rgb: [
        (40.0, 60.0, 20.0, 20.0, 0.8), (820.0, 60.0, 80.0, 80.0, 0.8)])
    assert depth_fx.still_focus(_still(), 1080, 1920)[0] > 0.6


def test_with_no_face_the_salient_part_is_kept(monkeypatch):
    import broll_score
    monkeypatch.setattr(broll_score, "faces", lambda rgb: [])
    img = _still()
    img[300:900, 60:320] = (240, 240, 240)
    img[300:900, 60:320, 0] = np.random.RandomState(1).randint(
        0, 255, (600, 260), np.uint8)
    assert depth_fx.still_focus(img, 1080, 1920)[0] < 0.5


def test_a_still_already_in_the_frames_shape_is_not_moved():
    assert depth_fx.still_focus(_still(900, 1600), 1080, 1920) == (0.5, 0.5)


def test_an_unread_focus_never_fails_the_crop(monkeypatch):
    import broll_score

    def boom(rgb):
        raise RuntimeError("no detector")
    monkeypatch.setattr(broll_score, "faces", boom)
    assert depth_fx._still_centre(_still(), 1080, 1920) == (0.5, 0.5)


class _Plan:
    W, H, fps, fps3, preview, source = 540, 960, 30.0, 30.0, True, None


class _Writer:
    def __init__(self, *a, **k):
        self.frames = []

    def write(self, f):
        self.frames.append(f)

    def close(self):
        pass


@pytest.mark.parametrize("kind", ["parallax", "ken_burns"])
def test_prep_still_crops_picture_and_depth_at_the_same_place(
        monkeypatch, tmp_path, kind):
    seen = {}
    img = _still()
    img[:, 1200:] = 200
    depth = np.tile(np.linspace(0, 1, 1600, dtype=np.float32), (1200, 1))
    monkeypatch.setattr(depth_fx, "still_for",
                        lambda plan, item: (img, ("asset", "p"), None))
    monkeypatch.setattr(depth_fx, "still_depth",
                        lambda *a: (depth, "full", "depth"))
    monkeypatch.setattr(depth_fx, "still_focus", lambda *a: (0.85, 0.5))
    monkeypatch.setattr(depth_fx, "ClipWriter", _Writer)
    real = depth_fx.cover_crop

    def spy(im, W, H, centre=(0.5, 0.5), margin=1.0):
        seen.setdefault("centres", []).append(centre)
        return real(im, W, H, centre=centre, margin=margin)
    monkeypatch.setattr(depth_fx, "cover_crop", spy)
    monkeypatch.setattr(depth_fx, "render_parallax_planes",
                        lambda *a, **k: None)
    monkeypatch.setattr(depth_fx, "visible_inpaint_planes", lambda *a: 0.0)
    plan = _Plan()
    plan.workdir = str(tmp_path)
    item = {"id": "d1", "kind": kind, "start": 0.0, "end": 1.0,
            "source": {"kind": "asset", "asset_key": "k"},
            "move": {"path": "push_in", "amount": 0.35, "ease": "in_out"}}
    depth_fx.DEPTH._prep_still(plan, item, 6, 0.0)
    assert seen["centres"] and all(c == (0.85, 0.5) for c in seen["centres"])
