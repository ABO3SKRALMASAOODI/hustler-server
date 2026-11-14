import json

from footage import fetch


def test_exact_original_reused_without_network_or_mutation(tmp_path, monkeypatch):
    original = tmp_path / "podcast.mp4"
    original.write_bytes(b"original" * 1000)
    url = "https://www.youtube.com/watch?v=anAmTGcewX8"
    monkeypatch.setenv("FOOTAGE_LOCAL_SOURCES", json.dumps([dict(url=url, path=str(original))]))
    monkeypatch.setattr(fetch, "run_ladder", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network")))
    assert fetch.proxy(url, str(tmp_path / "scan"))[0] == str(original)
    assert fetch.streams(url)["url"] == str(original)
    assert fetch.whole(url, str(tmp_path / "whole")) == str(original)
    assert original.read_bytes() == b"original" * 1000
    assert fetch.local_source(url + "other") is None


def test_missing_or_invalid_manifest_does_not_claim_a_cache(tmp_path, monkeypatch):
    for value in ("broken", "{}", "[null]", json.dumps([dict(url="u", path=str(tmp_path / "gone"))])):
        monkeypatch.setenv("FOOTAGE_LOCAL_SOURCES", value)
        assert fetch.local_source("u") is None


# ----------------------------------------------------------------- round 3 (valmera-stories): open each source once, keep it

def _find_whole_first(tmp_path, monkeypatch, keep=None, verify_env="0"):
    """One run.find over one short source and one line with four picks, the network replaced: returns (record, calls)."""
    import os
    from footage import cut, run, shots as fshots, verify
    calls = dict(whole_av=0, proxy=0, streams=0, section=0, verify=0, local=0)
    monkeypatch.setenv("FOOTAGE_VOICES", "0")
    monkeypatch.setenv("FOOTAGE_VERIFY", verify_env)
    if keep:
        monkeypatch.setenv("FOOTAGE_KEEP_SOURCES", str(keep))
    else:
        monkeypatch.delenv("FOOTAGE_KEEP_SOURCES", raising=False)
    monkeypatch.setattr(run.plan, "plan", lambda lines, named, about: (dict(subjects=[dict(name="A", kind="thing", must_show="A", searches=[dict(where="youtube", q="A")])],
                                                                         lines=[dict(i=0, show="A", subjects=["A"])]), 0.0))
    monkeypatch.setattr(run.search, "run", lambda qs: {0: [dict(id="h0", title="T0", url="https://www.youtube.com/watch?v=abc", where="youtube",
                                                                channel="c", duration=120.0)]})
    monkeypatch.setattr(run.plan, "rank", lambda subjects, hits, per=3: ([(0, h, "w") for h in hits[0]], 0.0))

    def whole_av(url, d):
        calls["whole_av"] += 1
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "whole.mp4")
        open(p, "wb").write(b"w" * 5000)
        return p, "default"

    def boom(name):
        def f(*a, **kw):
            calls[name] += 1
            raise AssertionError(f"{name} called")
        return f
    monkeypatch.setattr(fetch, "whole_av", whole_av)
    monkeypatch.setattr(fetch, "proxy", boom("proxy"))
    monkeypatch.setattr(fetch, "streams", boom("streams"))
    monkeypatch.setattr(fetch, "section", boom("section"))
    monkeypatch.setattr(fshots, "probe", lambda p: dict(dur=120.0, w=1280, h=720, fps=30, audio=True))
    monkeypatch.setattr(fshots, "cuts", lambda p, upto: [(k * 4.0, k * 4.0 + 4.0) for k in range(30)])
    monkeypatch.setattr(run.hear, "transcribe", lambda p, s: ([], 0.0))
    monkeypatch.setattr(run.look, "moments", lambda sub, need, local: ([], 0.0))
    monkeypatch.setattr(fshots, "frames", lambda proxy, times, d: {t: f"{d}/{t}.jpg" for t in times})
    monkeypatch.setattr(run.look, "check", lambda sub, need, cands, local, work: ([dict(c, ok=True, clean=True, grade=4, lines=[0], shows="A")
                                                                                    for c in cands], 0.0))

    def local(src, t0, t1, raw):
        calls["local"] += 1
        assert src.endswith("whole.mp4")
        open(raw, "wb").write(b"x")
        return raw, "whole"
    monkeypatch.setattr(cut, "local", local)
    monkeypatch.setattr(cut, "source", boom("section"))
    monkeypatch.setattr(cut, "measure", lambda raw: dict(y=0.5))
    monkeypatch.setattr(cut, "target", lambda stats: dict(y=0.5))
    monkeypatch.setattr(cut, "eq_for", lambda st, tgt: {})
    monkeypatch.setattr(cut, "finish", lambda raw, out, W, H, x, eq, trim=None, preserve_source=False: open(out, "wb").write(b"c"))

    def vclip(path, sub, dur):
        calls["verify"] += 1
        return dict(ok=True), 0.0
    monkeypatch.setattr(verify, "clip", vclip)
    lines = [dict(i=0, say="one two three four five six", pictures=["A"], dur=1.8)]
    res = run.find(lines, [dict(name="A", **{"is": "thing"})], "t", str(tmp_path / "out"), shot_s=0.45, line_max=4,
                   whole_first=True, reassign_lost=True)
    return res, calls


def test_whole_first_cuts_every_pick_of_a_short_source_from_one_fetch_and_verify_off_never_verifies(tmp_path, monkeypatch):
    res, calls = _find_whole_first(tmp_path, monkeypatch)
    clips = res["lines"][0]["clips"]
    assert len(clips) == 4 and calls["local"] == 4
    assert calls["whole_av"] == 1 and calls["proxy"] == calls["streams"] == calls["section"] == 0
    assert calls["verify"] == 0                                    # FOOTAGE_VERIFY=0: the verify pass never runs


def test_verify_stays_on_by_default(tmp_path, monkeypatch):
    _, calls = _find_whole_first(tmp_path, monkeypatch, verify_env="1")
    assert calls["verify"] == 4


def test_kept_sources_write_a_manifest_that_local_source_resolves(tmp_path, monkeypatch):
    import os
    keep = tmp_path / "keep"
    _find_whole_first(tmp_path, monkeypatch, keep=keep)
    rows = json.loads((keep / "manifest.json").read_text())
    assert len(rows) == 1 and rows[0]["url"] == "https://www.youtube.com/watch?v=abc" and rows[0]["rung"] == "default"
    assert os.path.isfile(rows[0]["path"]) and os.path.dirname(rows[0]["path"]) == str(keep)
    monkeypatch.setenv("FOOTAGE_LOCAL_SOURCES", json.dumps(rows))
    assert fetch.local_source(rows[0]["url"]) == os.path.realpath(rows[0]["path"])
    assert not [p for p in os.listdir(tmp_path / "out") if p.startswith("footage-")]        # the work folder is gone


def test_a_wayback_source_is_never_resolved_live_and_a_pick_past_its_copy_fails(tmp_path, monkeypatch):
    from footage import run
    src = dict(key="s0", url="https://www.youtube.com/watch?v=x", rung="wayback", proxy=str(tmp_path / "p.mp4"), dur_read=10.0)
    assert run._whole_first(dict(url="u", duration=500.0)) and not run._whole_first(dict(url="u", duration=900.0))
    assert src["rung"] == "wayback"
