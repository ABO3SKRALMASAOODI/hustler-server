import broll_source
import llm
import mcp_film
import stock


def test_chapter_timing_covers_leading_pause_sentences_and_tail():
    idx = {"video": {"duration": 180}, "sentences": [
        {"t0": n + 1, "t1": n + 8, "text": f"Passage {n}"} for n in range(0, 175, 10)]}
    rows = [r for interval in mcp_film.chapters(idx) for r in mcp_film.beats(idx, *interval)]
    assert rows[0]["start"] == 0 and rows[-1]["end"] == 180
    assert all(a["end"] == b["start"] for a, b in zip(rows, rows[1:]))
    assert " ".join(r["say"] for r in rows) == " ".join(s["text"] for s in idx["sentences"])


def test_archive_failure_repairs_picture_from_exact_words(tmp_path, monkeypatch):
    monkeypatch.setattr(stock, "PEXELS_KEY", "")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    monkeypatch.setattr(broll_source, "find_named", lambda *a, **kw: ([], {}))
    item, got = mcp_film.picture(None, {"say": "The heart pumps blood.", "subject": {"name": "heart"},
                                         "quote": "pumps blood"}, str(tmp_path))
    assert item["design"]["parts"][0]["label"] == "pumps blood"
    assert got["width"] == 1920 and got["height"] == 1080 and got["bytes"] > 1000


def test_stock_relevance_precedes_resolution_and_provider_limit(monkeypatch):
    hits = [[{"id": "head", "title": "head scan", "width": 3840},
             {"id": "heart", "title": "heart anatomy", "width": 1280}],
            [{"id": "cookie", "title": "heart-shaped cookie cutter", "width": 1920}]]
    def rank(system, evidence, **kw):
        import json
        candidates = json.loads(evidence)["candidates"]
        assert [c["title"] for c in candidates] == ["head scan", "heart-shaped cookie cutter", "heart anatomy"]
        return {"text": "[2,0,1]"}
    monkeypatch.setattr(llm, "ask_text", rank)
    assert stock._rank_matches(list(enumerate(hits)), "human heart anatomy", 1) == [hits[0][1]]


def test_an_invented_card_is_repaired_at_planning(monkeypatch):
    monkeypatch.setattr(llm, "ask_text", lambda *a, **kw: {"text":
        '[{"i":0,"quote":"six months","design":{"kind":"number","parts":[{"value":"6"}]}}]'})
    rows = [{"say": "it was about a year"}]
    mcp_film.picture_plan(rows, "documentary")
    assert not rows[0]["quote"] and not rows[0]["design"]


def test_observed_frame_can_override_a_matching_library_title(monkeypatch):
    rows = [{"id": "cutter", "title": "Human heart", "_thumb": "one"},
            {"id": "organ", "title": "Study", "_thumb": "two"}]
    monkeypatch.setattr(llm, "ask_text", lambda *a, **kw: {"text": "[0,1]"})
    monkeypatch.setattr(broll_source, "check_named", lambda *a: ([], [
        {"id": "cutter", "ok": False, "grade": 1, "shows": "Metal cookie cutter"},
        {"id": "organ", "ok": True, "grade": 9, "shows": "Anatomical heart model"}], 0))
    result = stock._rank_matches([("library", rows)], "human heart anatomy", 1)
    assert result[0]["id"] == "organ"
    assert result[0]["description"] == "Anatomical heart model"


def test_director_selects_only_discovered_library_ids(monkeypatch):
    import json
    picture = {"id": "archive:1", "asset_key": "films/3/photo.jpg", "shows": "The heart ventricles"}
    monkeypatch.setattr(llm, "ask_text", lambda *a, **kw: {"text": json.dumps([
        {"i": 0, "ids": ["invented", "archive:1"], "searches": [], "quote": ""}])})
    rows = [{"say": "These are the chambers."}]
    mcp_film.picture_plan(rows, "documentary", [picture])
    assert rows[0]["leads"] == [picture]
