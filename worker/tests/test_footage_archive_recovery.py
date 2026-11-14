from footage import run


def test_archive_recovers_by_subject_when_detailed_queries_miss(tmp_path, monkeypatch):
    subject = dict(name="AlexNet", kind="thing", must_show="the actual network",
                   searches=[dict(where="youtube", q="AlexNet 2012 original research presentation")])
    line = dict(i=0, say="AlexNet changed the result.", pictures=["AlexNet"], dur=3)
    monkeypatch.setattr(run.plan, "plan", lambda *a: (dict(subjects=[subject], lines=[dict(line, subjects=["AlexNet"])]), 0))
    candidate = dict(id="ia:network", url="https://archive.org/details/network", title="AlexNet", channel="lab", where="archive")
    queries = []
    def search(rows):
        queries.extend(rows)
        return {i: [candidate] if q["where"] == "archive" and q["q"] == "AlexNet" else [] for i, q in enumerate(rows)}
    monkeypatch.setattr(run.search, "run", search)
    monkeypatch.setattr(run.plan, "rank", lambda subjects, hits, per=None: ([(i, h, "exact subject") for i, hs in hits.items() for h in hs], 0))
    opened = []
    def open_source(s, *args):
        opened.append(s["url"])
        return dict(s, proxy="checked-source.mp4", shots=[(0, 10)], transcript=[], hear_usd=0)
    monkeypatch.setattr(run, "_open", open_source)
    monkeypatch.setattr(run.look, "moments", lambda *a: ([], 0))
    monkeypatch.setattr(run.shots, "frames", lambda *a: {})
    checked = []
    monkeypatch.setattr(run.look, "check", lambda *a: (checked.append(True) or [], 0))
    result = run.find([line], [], "network", str(tmp_path), want_voices=False)
    assert opened == [candidate["url"]]
    assert checked and result["counts"]["clips"] == 0  # retrieval never bypasses the visual check
    assert len([q for q in queries if q["where"] == "archive"]) <= 5
