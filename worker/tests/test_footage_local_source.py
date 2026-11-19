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

def _find_whole_first(tmp_path, monkeypatch, keep=None, verify_env="0", size=(1280, 720), live=None, meta=None):
    """One run.find over one short source and one line with four picks, the network replaced: returns (record, calls).
    size: the whole file's picture size; live: what fetch.streams resolves (None: it must not be called; False: it
    fails); meta: the source's own info json written beside the whole file."""
    import os
    from footage import cut, run, shots as fshots, verify
    calls = dict(whole_av=0, proxy=0, streams=0, section=0, verify=0, local=0, seek=0)
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
        if meta:
            open(os.path.join(d, fetch.META + ".info.json"), "w").write(json.dumps(meta))
        return p, "default"

    def boom(name):
        def f(*a, **kw):
            calls[name] += 1
            raise AssertionError(f"{name} called")
        return f
    monkeypatch.setattr(fetch, "whole_av", whole_av)
    monkeypatch.setattr(fetch, "proxy", boom("proxy"))
    if live is None:
        monkeypatch.setattr(fetch, "streams", boom("streams"))
    else:
        def streams(url, local=True):
            calls["streams"] += 1
            assert local is False                                   # the live stream, never the small kept copy
            if live is False:
                raise fetch.FetchError("walled")
            return live
        monkeypatch.setattr(fetch, "streams", streams)

    def seek(stream, t0, t1, raw):
        calls["seek"] += 1
        open(raw, "wb").write(b"sharp")
        return raw
    monkeypatch.setattr(cut, "seek", seek)
    monkeypatch.setattr(fetch, "section", boom("section"))
    monkeypatch.setattr(fshots, "probe", lambda p: dict(dur=120.0, w=size[0], h=size[1], fps=30, audio=True))
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



# ----------------------------------------------------------------- round 1 of 19 Nov: clips at the size that fills the phone

def test_a_whole_source_that_came_small_is_cut_from_its_sharper_live_stream(tmp_path, monkeypatch):
    """A 360p whole file (YouTube's only combined mp4 under 720p) needs 5.3x to fill the phone: its picks are cut from
    the live stream at up to 1080p instead, resolved once; the record says how and from what size."""
    res, calls = _find_whole_first(tmp_path, monkeypatch, size=(640, 360), live=dict(url="https://cdn/v", h=1080, w=1920),
                                   meta=dict(title="T0", uploader="Up", description="Susan Li on Meta's spending"))
    clips = res["lines"][0]["clips"]
    assert len(clips) == 4 and calls["streams"] == 1 and calls["seek"] == 4 and calls["local"] == 0
    assert all(c["how"] == "stream_sharper" and c["source_size"] == [640, 360] and c["rung"] == "default" for c in clips)
    assert clips[0]["source"]["description"] == "Susan Li on Meta's spending"


def test_a_small_source_with_no_sharper_stream_still_cuts_from_its_file(tmp_path, monkeypatch):
    res, calls = _find_whole_first(tmp_path, monkeypatch, size=(640, 360), live=False)
    assert len(res["lines"][0]["clips"]) == 4 and calls["streams"] == 1 and calls["local"] == 4 and calls["seek"] == 0


def test_the_whole_first_ladder_asks_for_1080p_streams_first_and_writes_the_sources_own_metadata(tmp_path, monkeypatch):
    import os
    fmt = fetch.whole_av_format()
    rungs = fmt.split("/")
    assert rungs[0].startswith("bv*[height<=1080]") and "+ba" in rungs[0] and "[filesize<?" in rungs[0]
    assert rungs.index("b[height<=720][ext=mp4]") > rungs.index("bv*[height<=720]+ba")       # the 360p combined file last
    seen = {}
    monkeypatch.delenv("FOOTAGE_FETCH_CMD", raising=False)
    monkeypatch.setattr(fetch, "local_source", lambda url: None)

    def ladder(url, args, timeout_s=None, done=None):
        seen["args"] = args
        open(os.path.join(tmp_path, "whole.mp4"), "wb").write(b"v" * 3000)
        open(os.path.join(tmp_path, "meta.info.json"), "w").write(json.dumps(dict(title="t", channel="ch", description=" d ")))
        assert done("")
        return "", "default"
    monkeypatch.setattr(fetch, "run_ladder", ladder)
    path, rung = fetch.whole_av("https://www.youtube.com/watch?v=abc", str(tmp_path))
    assert path.endswith("whole.mp4") and rung == "default"            # the info json is never taken for the video
    assert seen["args"][seen["args"].index("-f") + 1] == fmt and "--write-info-json" in seen["args"]
    assert fetch.info_of(str(tmp_path)) == dict(title="t", channel="ch", description="d")


def test_kept_sources_keep_their_own_description(tmp_path, monkeypatch):
    import os
    from footage import run
    keep = tmp_path / "keep"
    monkeypatch.setenv("FOOTAGE_KEEP_SOURCES", str(keep))
    whole = tmp_path / "whole.mp4"
    whole.write_bytes(b"w" * 3000)
    run.keep_sources([dict(url="https://www.youtube.com/watch?v=k", whole=str(whole), rung="default", title="T",
                           channel="C", description="Steve Jobs talks to Apple staff")], log=lambda _: None)
    rows = json.loads((keep / "manifest.json").read_text())
    assert rows[0]["description"] == "Steve Jobs talks to Apple staff" and os.path.isfile(rows[0]["path"])
