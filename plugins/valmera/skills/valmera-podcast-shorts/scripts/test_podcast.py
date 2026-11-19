"""Tests for the podcast run (podcast.py) and the film's gates (wf1_film.py). No network, no model, no render.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_podcast.py
"""
import json
import os
import sys

import pytest
from types import SimpleNamespace

os.environ["PODCAST_NO_REEXEC"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import podcast  # noqa: E402
import wf1_film  # noqa: E402


def test_incomplete_test_set_is_not_marked_delivered(tmp_path):
    from types import SimpleNamespace
    work=tmp_path/'run';work.mkdir()
    state=dict(video_id='anAmTGcewX8',test=True,pid=None,done=['fetch','hear','moments','shorts'])
    (work/'run.json').write_text(json.dumps(state))
    with pytest.raises(RuntimeError,match='Incomplete test set remains resumable.*m02'):
        podcast.deliver(SimpleNamespace(),{}, {},dict(made=[dict(id='m01')],failed=[dict(id='m02',error='coverage short')]),str(tmp_path/'out'),test=True)
    assert not (tmp_path/'out').exists()
    assert json.loads((work/'run.json').read_text())==state


def test_video_id_reads_every_youtube_shape_and_hashes_the_rest():
    for link in ("https://youtu.be/0cn3VBjfN8g?si=abc", "https://www.youtube.com/watch?v=0cn3VBjfN8g&t=10s",
                 "https://youtube.com/live/0cn3VBjfN8g", "https://www.youtube.com/shorts/0cn3VBjfN8g"):
        assert podcast.video_id(link) == "0cn3VBjfN8g"
    a, b = podcast.video_id("https://example.com/ep1.mp3"), podcast.video_id("https://example.com/ep2.mp3")
    assert a != b and a.startswith("x") and len(a) == 11


def test_captions_words_times_every_word(tmp_path):
    p = tmp_path / "c.json3"
    p.write_text(json.dumps({"events": [
        {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "hello"}, {"utf8": " world", "tOffsetMs": 500}]},
        {"tStartMs": 4000, "dDurationMs": 3000, "segs": [{"utf8": "a written line"}]},
        {"tStartMs": 8000, "segs": [{"utf8": "\n"}]}]}))
    w = podcast.captions_words(str(p))
    assert w[:2] == [(1.0, "hello"), (1.5, "world")]
    assert [x[1] for x in w[2:]] == ["a", "written", "line"] and w[2][0] == 4.0 and w[4][0] == 6.0


def _m(t0, t1, strength, styles=(), shows=("Uber", "Travis Kalanick"), **kw):
    return dict(title=f"m{t0}", t0=t0, t1=t1, strength=strength, shows=list(shows), styles=[dict(id=s) for s in styles], **kw)


def test_rank_has_no_count_only_a_bar():
    many = [_m(100 * i, 100 * i + 30, 8) for i in range(37)]
    assert len(podcast.rank(many)) == 37                      # the count follows the podcast
    few = [_m(0, 30, 9), _m(100, 130, 6.5), _m(200, 230, 7)]
    kept = podcast.rank(few)
    assert [k["strength"] for k in kept] == [9, 7] and [k["rank"] for k in kept] == [1, 2] and kept[0]["id"] == "m01"
    assert podcast.rank([_m(0, 30, 9, shows=())]) == []       # nothing a camera filmed: no real footage, no short


def test_rank_drops_the_weaker_of_two_overlapping_moments():
    kept = podcast.rank([_m(0, 40, 8), _m(30, 60, 9), _m(100, 130, 8)])
    assert [(k["t0"], k["t1"]) for k in kept] == [(30.0, 60.0), (100.0, 130.0)]


def test_styles_differ_from_the_two_shorts_before_and_spread_evenly():
    order = list(wf1_film.ORDER)
    ms = podcast.assign_styles([_m(100 * i, 100 * i + 30, 8, styles=("footage-cut", "window")) for i in range(12)], order)
    got = [m["style"] for m in ms]
    assert all(got[i] not in got[max(i - 2, 0):i] for i in range(len(got)))
    counts = {s: got.count(s) for s in order}
    assert max(counts.values()) - min(counts.values()) <= 1 and set(got) == set(order)
    one = podcast.assign_styles([_m(0, 30, 9, styles=("prints",))], order)
    assert one[0]["style"] == "prints"                          # the moment's own best fit when nothing stands in its way


def test_every_style_is_in_the_registry_as_an_engine_style():
    reg = json.load(open(os.path.join(os.path.dirname(os.path.dirname(HERE)), "_shell", "styles.json")))["wf1"]["styles"]
    ids = {s["id"] for s in reg if s.get("engine") == "cut"}
    assert ids == set(wf1_film.STYLES) == set(wf1_film.ORDER)


def test_alien_gate_never_asks_for_the_gimmicks_and_arc_needs_two_years():
    frame_of = lambda seg, word: 30 * (seg + 1)  # noqa: E731
    one = dict(arc=[dict(seg=0, word="1999", year=1999, stakes=-0.5)])
    assert wf1_film.alien_asks(one, "footage-cut", frame_of)[0] == []
    two = dict(arc=[dict(seg=0, word="1999", year=1999, stakes=-0.5), dict(seg=3, word="2010", year=2010, stakes=0.8)])
    asks, why = wf1_film.alien_asks(two, "footage-cut", frame_of)
    assert [a["dev"] for a in asks] == ["arc"] and [m["year"] for m in asks[0]["marks"]] == [1999, 2010] and "passed" in why
    for style in ("archive-reel", "window", "prints"):         # a style with its own finish keeps it
        assert wf1_film.alien_asks(two, style, frame_of)[0] == []
    assert wf1_film.alien_asks(dict(arc=[dict(seg=0, year="soon"), dict(seg=1, year=99999)]), "footage-cut", frame_of)[0] == []
    mixed = [dict(dev="diecut"), dict(dev="answer"), dict(dev="lamp"), dict(dev="arc", marks=[]), dict(dev="rhyme"), "x", dict(dev="new")]
    assert [a["dev"] for a in wf1_film.clean_asks(mixed)] == ["arc", "rhyme"]
    assert set(wf1_film.ALIEN_NEVER) == {"diecut", "answer", "lamp"}


def _film(folder, title, style, music=True):
    os.makedirs(folder)
    for n in ["film.mp4"] + (["film_clean.mp4"] if music else []):
        open(os.path.join(folder, n), "wb").write(b"mp4:" + n.encode())
    rec = dict(run_id="r1", workflow="wf1", style=dict(id=style), window=[10.0, 40.0], script=dict(title=title, hook_line="h", close_line="c"),
               spend_usd=0.5, checks=dict(ok=True, endcard_ok=True, mark_ok=True, final_s=35.0, lufs=-14.1, music=dict(sounds=6),
                                          podcast=dict(pictures=12, clips=9, photographs=3, new_pictures_10s=4.0, display_words=["UBER"],
                                                       sources=[dict(f=[60, 96], kind="clip", name="Uber", source="https://youtu.be/abc", time_range=[3.0, 4.5]),
                                                                dict(f=[96, 130], kind="photo", name="Travis Kalanick", source="https://commons/x", time_range=None)]),
                                          stories=dict(credits=[dict(kind="footage", title="UberCab 2010", credit="TechCrunch", page="https://youtu.be/abc", time_range=[3.0, 4.5]),
                                                                dict(kind="footage", title="UberCab 2010", credit="TechCrunch", page="https://youtu.be/abc", time_range=[9.0, 10.5]),
                                                                dict(kind="photograph", title="Kalanick.jpg", credit="J. Doe", licence="CC BY 2.0", page="https://commons/x")])))
    if music:
        rec["music"] = dict(title="Track", author="A", license="CC-BY 4.0", credit="Track by A (CC-BY 4.0)", page_url="https://m/x")
    json.dump(rec, open(os.path.join(folder, "film.json"), "w"))


def _fake_run(tmp_path, monkeypatch, fail=()):
    work_root, out = str(tmp_path / "work"), str(tmp_path / "out")
    monkeypatch.setattr(podcast, "WORK_ROOT", work_root)
    monkeypatch.setattr(podcast, "ENV_FILE", str(tmp_path / "no.env"))             # never the real backend .env
    monkeypatch.setattr(podcast, "WALL_WAITS", ())
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", out)          # `out` is this test's Podcast Shorts: a delivery folder
    info = dict(id="abcdefghijk", title="The Episode", channel="Show", upload_date="20240329", duration=3600, webpage_url="https://youtu.be/abcdefghijk")

    def fetch(run):
        os.makedirs(run.p("source"), exist_ok=True)
        open(run.p("source", "podcast.mp4"), "wb").write(b"x" * 1000)
        return dict(video=run.p("source", "podcast.mp4"), captions=None, how="downloaded for this run", bytes=1000, duration=3600.0, info=info)

    def hear(run, fetched):
        json.dump([[0.0, "hello"]], open(run.p("words.json"), "w"))
        return dict(words=1, transcript="test", path=run.p("words.json"))

    def moments(run, fetched):
        ms = podcast.assign_styles(podcast.rank([_m(100 * i, 100 * i + 30, 9 - i, styles=("footage-cut",)) for i in range(3)]), list(wf1_film.ORDER))
        return dict(podcast=dict(speaker="Mark Cuban", short_title="On Uber", year=2024), found=5, moments=ms, left_out=[], bar=podcast.BAR, spend=[dict(usd=0.2)])

    def build_short(run, m, fetched, pod, tries=3):
        if m["id"] in fail:
            return dict(id=m["id"], ok=False, error=fail[m["id"]] if isinstance(fail, dict) else "supply: no footage")
        folder = run.p("films", m["id"])
        _film(folder, f"Title {m['id']}", m["style"])
        return dict(id=m["id"], ok=True, folder=folder)
    for name, fn in (("fetch", fetch), ("hear", hear), ("moments", moments), ("build_short", build_short)):
        monkeypatch.setattr(podcast, name, fn)
    return work_root, out


def test_a_run_delivers_one_complete_folder_once_and_deletes_its_work(tmp_path, monkeypatch, capsys):
    work_root, out = _fake_run(tmp_path, monkeypatch, fail=("m02",))
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out])
    assert podcast.main() == 0
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["work_empty"] and os.listdir(work_root) == [] and rep["shorts"] == 2 and rep["not_made"][0]["id"] == "m02"
    folder = rep["delivered"]
    assert os.path.basename(folder) == "Mark Cuban - On Uber (2024) - 2 Shorts" and os.listdir(out) == [os.path.basename(folder)]
    names = sorted(os.listdir(folder))
    assert names == ["01 - Title m01.mp4", "02 - Title m03.mp4", "README.md", "SOURCES.md", "clean", "records", "shorts.json"]
    assert sorted(os.listdir(os.path.join(folder, "clean"))) == ["01 - Title m01.mp4", "02 - Title m03.mp4"]
    assert open(os.path.join(folder, "01 - Title m01.mp4"), "rb").read() == b"mp4:film.mp4"                  # the short with its music
    assert open(os.path.join(folder, "clean", "01 - Title m01.mp4"), "rb").read() == b"mp4:film_clean.mp4"   # the same short without
    man = json.load(open(os.path.join(folder, "shorts.json")))
    assert man["published"] is False and [s["rank"] for s in man["shorts"]] == [1, 3] and man["not_made"][0]["error"] == "supply: no footage"
    assert all(s["end_card"] and s["watermark"] and s["music"]["credit"] for s in man["shorts"])
    src = open(os.path.join(folder, "SOURCES.md")).read()
    for needle in ("https://youtu.be/abcdefghijk", "Footage (Uber): UberCab 2010 | TechCrunch | https://youtu.be/abc | 3.0-4.5 s of the source | shown at 2.0-3.2 s",
                   "Photograph (Travis Kalanick): Kalanick.jpg | J. Doe | CC BY 2.0 | https://commons/x", "Found and not used: 1 more clips",
                   "Track by A (CC-BY 4.0)", "not licensed"):
        assert needle in src
    # the same podcast again is refused before anything is fetched (owner, 14 Nov: never the same podcast twice), with
    # one JSON line naming the delivered folder; the delivered folder is never added to
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://www.youtube.com/watch?v=abcdefghijk&t=5", "--out", out])
    assert podcast.main() == 4
    ref = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert ref["source_id"] == "yt:abcdefghijk" and ref["delivered"] == folder and ref["refused"]
    assert sorted(os.listdir(folder)) == names and len(os.listdir(out)) == 1 and os.listdir(work_root) == []


def test_a_run_that_makes_nothing_delivers_nothing_and_keeps_its_work_to_continue(tmp_path, monkeypatch, capsys):
    work_root, out = _fake_run(tmp_path, monkeypatch, fail=("m01", "m02", "m03"))
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out])
    assert podcast.main() == 2
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["stopped"]["stage"] == "deliver" and not os.path.exists(out) and len(os.listdir(work_root)) == 1
    work = os.path.join(work_root, os.listdir(work_root)[0])
    assert podcast.find_run("https://youtu.be/abcdefghijk") == (work, True)      # the same link continues this run


def test_type_on_a_bright_picture_takes_dark_ink_for_as_long_as_it_is_up():
    voices = {"cap": {"font": "InterDisplay-Bold.ttf", "px": 58, "shadow": {"a": 0.85}}, "dsp": {"font": "InterDisplay-Black.ttf", "px": 128}}
    items = [dict(id="w1", k="word", role="cap", v="cap", f=[10, 40], t="hello", x=0.4, y=0.6, ink="#FFFFFF", op=1.0, stroke=dict(w=1.6, c="#0B0A09")),
             dict(id="w2", k="word", role="big", v="dsp", f=[25, 45], t="BIG", x=0.3, y=[0.6] * 20, ink="#F2C14E", op=[1.0] * 20, stroke=dict(w=3.0, c="#0B0A09")),
             dict(id="w3", k="word", role="stat", v="cap", f=[20, 30], t="LABEL", x=0.4, y=0.75, ink="#FFFFFF", op=1.0),
             dict(id="s1", k="shot", f=[0, 60], pic="main")]
    assert wf1_film.ink_by_ground([dict(i) for i in items], dict(voices), []) == items
    out = wf1_film.ink_by_ground([dict(i) for i in items], voices, [(20, 30)])
    cap = [(i["f"], i["v"], i["ink"]) for i in out if i.get("t") == "hello"]
    assert cap == [([10, 20], "cap", "#FFFFFF"), ([20, 30], "capd", "#0B0A09"), ([30, 40], "cap", "#FFFFFF")]
    big = [i for i in out if i.get("t") == "BIG"]
    assert [(i["f"], i["ink"], len(i["y"])) for i in big] == [([25, 30], "#0B0A09", 5), ([30, 45], "#F2C14E", 15)]
    assert big[0]["stroke"]["c"] == "#F6F1E7" and len({i["id"] for i in out}) == len(out)
    assert [i for i in out if i["id"] in ("w3", "s1")] == items[2:]         # labels and pictures are left alone


def test_fetch_walks_the_rungs_when_youtube_walls_a_player_and_remembers_the_one_that_worked(tmp_path, monkeypatch):
    class R:
        def __init__(self, code, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err
    calls = []

    def fake(args, timeout=3600):
        calls.append(args)
        if "youtube:player_client=web_embedded" in args:
            return R(0, '{"id": "abcdefghijk"}')
        return R(1, "", "ERROR: [youtube] abcdefghijk: Sign in to confirm you are not a bot.")
    monkeypatch.setattr(podcast, "_ytdlp", fake)
    run = podcast.Run("https://youtu.be/abcdefghijk", str(tmp_path))
    ok = lambda r: r.returncode == 0  # noqa: E731
    assert podcast.ladder(run, ["-J", "x"], ok).stdout.startswith("{") and len(calls) == 2 and run.state["rung"] == "embedded"
    calls.clear()
    podcast.ladder(run, ["-J", "x"], ok)
    assert len(calls) == 1                                    # the rung that worked goes first from then on
    monkeypatch.setattr(podcast, "_ytdlp", lambda args, timeout=3600: R(1, "", "ERROR: Sign in to confirm"))
    try:
        podcast.ladder(run, ["-J", "x"], ok)
        raise AssertionError("no rung delivered, so the ladder must say so")
    except RuntimeError as e:
        assert "Sign in to confirm" in str(e)


def test_nothing_outlives_the_film():
    items = [dict(id="a", k="word", f=[90, 110], y=[0.6] * 20, op=1.0, crop=[0.0, 0.0, 1.0, 1.0]), dict(id="b", k="word", f=[100, 104]),
             dict(id="e", k="card", f=[98, 102], crop=[0.1, 0.0, 0.9, 1.0], s=[1.0, 1.01, 1.02, 1.03]),
             dict(id="c", k="shot", f=[0, 100]), dict(id="d", k="word", f=[20, 30], y=[0.5] * 10)]
    out = wf1_film.inside(items, 100)
    assert [(i["id"], i["f"]) for i in out] == [("a", [90, 100]), ("e", [98, 100]), ("c", [0, 100]), ("d", [20, 30])]
    assert len(out[0]["y"]) == 10 and out[0]["crop"] == [0.0, 0.0, 1.0, 1.0] and len(out[3]["y"]) == 10
    assert out[1]["crop"] == [0.1, 0.0, 0.9, 1.0] and out[1]["s"] == [1.0, 1.01]          # a rect of four numbers is not four frames


def test_a_long_stretch_of_the_take_takes_the_pictures_the_film_has_to_spare():
    FPS = wf1_film.FPS
    clip = lambda n, s="Uber": dict(path=f"/c/{n}.mp4", seconds=2.0, subject=s)  # noqa: E731
    script = dict(segments=[dict(say="a", pictures=["Uber"]), dict(say="b", pictures=[]), dict(say="c", pictures=[])])
    seg_f = [0, 10 * FPS, 20 * FPS]
    ins = [[2 * FPS, 3 * FPS, "clip", "Uber", clip("used")]]
    footage = {0: [clip("used"), clip("spare0")], 2: [clip("spare2")]}
    pics = {"Uber": [dict(path="/p/1.jpg", w=1000, h=1000)]}
    out = wf1_film.fill_gaps([list(x) for x in ins], footage, pics, script, seg_f, FPS, 28 * FPS, stress=[])
    added = [x for x in out if x[4]["path"] != "/c/used.mp4"]
    assert [x[4]["path"] for x in out].count("/c/used.mp4") == 1 and len({x[4]["path"] for x in out}) == len(out)       # nothing is shown twice
    assert {x[4]["path"] for x in added} == {"/c/spare0.mp4", "/c/spare2.mp4", "/p/1.jpg"}
    assert all(b[0] - a[1] >= 2 * FPS for a, b in zip(out, out[1:]))             # the take stands between them
    assert all(x[0] >= 3 * FPS + 2 * FPS and x[1] <= 28 * FPS - 2 * FPS for x in added)
    assert all(abs((x[4]["src"][1] - x[4]["src"][0]) - (x[1] - x[0]) / FPS) < 0.01 for x in added if x[2] == "clip")
    assert wf1_film.fill_gaps([list(x) for x in ins], {0: [clip("used")]}, {}, script, seg_f, FPS, 28 * FPS, stress=[]) == ins      # nothing to spare: the take stays


def test_names_keep_their_apostrophes():
    assert podcast.clean_name("Humanity\u2019s Main Character Energy") == "Humanity's Main Character Energy"
    assert podcast.clean_name("a/b: c?") == "a b c" and podcast.clean_name("") == "untitled"


def test_a_short_whose_footage_would_not_open_is_not_cut_from_photographs():
    src = open(os.path.join(HERE, "wf1_short.py")).read()
    ns = {}
    exec(src[src.index("def refused(foot):"):src.index("def dull(c):")], ns)          # the rule alone: the module itself loads the engines
    walled = ns["walled"]
    ok, bad, way = dict(where="youtube"), dict(where="youtube", error="default: Sign in to confirm", error_stage="fetch"), dict(where="youtube", rung="wayback")
    arch = dict(where="archive")

    def foot(clips, with_footage, lines, sources):
        return dict(counts=dict(clips=clips, lines_with_footage=with_footage, lines=lines), sources=sources)
    assert walled(foot(0, 0, 13, [bad, bad, bad]))
    assert walled(foot(3, 3, 13, [way, way, bad, ok, arch, arch]))                    # a clip or three through the archive: still walled
    assert not walled(foot(24, 13, 13, [ok, ok, bad]))                                # footage came: a film is made
    # 19 Nov m07: 14 of 16 refused while 3 of 5 sentences were covered. The refused share alone decides: the refused
    # sources are fetched again rather than a full new search run under the wall.
    assert walled(foot(9, 5, 13, [way, bad, ok]))
    assert walled(foot(7, 3, 5, [bad] * 14 + [ok] * 2))
    assert not walled(foot(0, 0, 13, [ok, ok, ok, bad]))                              # the sources opened and showed nothing: photographs carry it
    assert not walled(None) and not walled(foot(0, 0, 13, [])) and not walled(foot(0, 0, 13, [arch, arch]))
    assert ns["refused"](foot(0, 0, 1, [ok, bad, way, arch])) == (3, 2)
    internal=dict(where="youtube",error="open() got an unexpected keyword argument",error_stage="hear")
    assert not walled(foot(0,0,5,[internal,internal]))
    assert not walled(foot(0,0,5,[dict(where="youtube",error="unclassified old failure")]*2))
    gone = dict(where="youtube", error="default: ERROR: [youtube] x: Video unavailable. This video has been removed", error_stage="fetch")
    aged = dict(where="youtube", error="default: ERROR: Sign in to confirm your age", error_stage="fetch")
    assert not walled(foot(0, 0, 5, [gone, gone, aged]))                              # a video that is gone is not the wall: nothing waits for it
    assert ns["wall_error"]("anonymous-web_safari: ERROR: [youtube] o6f0B9oo87w: Sign in to confirm you\u2019re not a bot")


def test_shorts_waiting_for_footage_hold_the_delivery_and_the_run_continues_later(tmp_path, monkeypatch, capsys):
    work_root, out = _fake_run(tmp_path, monkeypatch, fail={"m03": wf1_film.WALL + ": 9 of 10 footage sources would not open"})
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out])
    assert podcast.main() == 2
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["stopped"]["stage"] == "deliver" and "waiting for footage" in rep["stopped"]["error"] and not os.path.exists(out)
    work = os.path.join(work_root, os.listdir(work_root)[0])
    assert sorted(os.listdir(os.path.join(work, "films"))) == ["m01", "m02"]           # what was made is kept for the run that completes the set


def test_a_long_stretch_of_bare_take_in_the_final_build_takes_checked_spare_pictures():
    """19 Nov m03/m10: 34 s and 22 s on the speaker once windows were turned down; fill_gaps had no caller. The final
    build fills every stretch over about 3 s with approved pictures not yet shown, each checked on its own words; one
    turned down gives its place to the next spare."""
    FPS = wf1_film.FPS
    clip = lambda n, url=None, tr=None: dict(path=f"/c/{n}.mp4", seconds=2.0, subject="Servers", **({"source": dict(url=url), "time_range": tr} if url else {}))  # noqa: E731
    coverage = dict(windows=[dict(name="Servers", must_show="Meta's data-centre servers", t0=1.0, t1=3.0),
                             dict(name="Servers", must_show="Meta's data-centre servers", t0=30.0, t1=32.0)])
    shown = clip("shown", "https://y/v", [139.82, 141.42])
    ins = [[wf1_film.LEAD_F + FPS, wf1_film.LEAD_F + 3 * FPS, "clip", "Servers", dict(shown, window=0, src=[0.3, 1.7])]]
    footage = {0: [shown, clip("twin", "https://y/v", [139.9, 141.5]), clip("bad"), clip("good")], 1: [clip("good"), clip("later"), clip("extra")]}
    script = dict(segments=[dict(say="a", pictures=["Servers"])])
    checked = []

    def checker(rows):
        checked.extend(r[4]["path"] for r in rows)
        ok = [r for r in rows if r[4]["path"] != "/c/bad.mp4"]
        return ok, dict(placements=[dict(ok=r in ok) for r in rows], spend=[dict(usd=0.01)])
    first, last = wf1_film.LEAD_F + FPS, ins[0][1] + 8 * FPS                        # 8 s of bare take after the one picture
    out, rep = wf1_film.fill_holes(ins, coverage, footage, {}, script, [0], first, last, [], checker, rejected=["/c/rejected.mp4"])
    paths = [x[4]["path"] for x in out]
    assert "/c/bad.mp4" in checked and "/c/bad.mp4" not in paths and "/c/twin.mp4" not in checked     # the same interval is never a filler
    assert len(paths) == len(set(paths)) and {"/c/good.mp4", "/c/later.mp4", "/c/extra.mp4"} <= set(paths)
    edges = [first] + [f for x in out for f in (x[0], x[1])] + [last]
    assert max(b - a for a, b in zip(edges[0::2], edges[1::2])) <= wf1_film.FILL_GAP_S * FPS          # no bare stretch over 3 s
    added = [x for x in out if x[4].get("gap_fill")]
    assert all(x[4]["illustration"] == "Meta's data-centre servers" and x[4]["window"] is None for x in added)
    assert rep["rejected"] == 1 and rep["accepted"] == len(added) == 3 and len(rep["spend"]) == 2


def test_footage_routes_reach_the_footage_engine_from_the_operator_config(tmp_path, monkeypatch):
    env = tmp_path / "backend.env"
    env.write_text("OPENAI_API_KEY=sk-never-read-here\nYTDLP_PROXY=http://user:pass@proxy.example:12323\n"
                   "export FOOTAGE_FETCH_CMD='python fetch.py'\nYTDLP_PROXY_FALLBACK=\n")
    for k in podcast.ROUTE_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert podcast.footage_routes(str(env)) == ["YTDLP_PROXY", "FOOTAGE_FETCH_CMD"]
    assert os.environ["YTDLP_PROXY"] == "http://user:pass@proxy.example:12323" and os.environ["FOOTAGE_FETCH_CMD"] == "python fetch.py"
    assert "OPENAI_API_KEY" not in os.environ                                        # only the route keys are read
    monkeypatch.setenv("YTDLP_PROXY", "")                                            # a key the operator set, even empty, stands
    monkeypatch.delenv("FOOTAGE_FETCH_CMD")
    assert podcast.footage_routes(str(env)) == ["FOOTAGE_FETCH_CMD"] and os.environ["YTDLP_PROXY"] == ""


def test_the_run_paces_its_youtube_fetches_through_one_bucket_its_processes_share(tmp_path, monkeypatch):
    for k in ("FOOTAGE_PACE_DIR", "FOOTAGE_PACE_S", "FOOTAGE_PACE_BURST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(podcast, "WORK_ROOT", str(tmp_path))
    run = podcast.Run("https://youtu.be/abcdefghijk", str(tmp_path))
    assert podcast.footage_pace(run) == str(tmp_path / "pace") == os.environ["FOOTAGE_PACE_DIR"]
    assert float(os.environ["FOOTAGE_PACE_S"]) > 0 and int(os.environ["FOOTAGE_PACE_BURST"]) >= 1
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "..", "worker")))
    try:
        from footage import fetch
    finally:
        sys.path.pop(0)
    waits = []
    monkeypatch.setattr(fetch.time, "sleep", waits.append)
    n = int(os.environ["FOOTAGE_PACE_BURST"]) + 2
    for _ in range(n):
        fetch.pace()                                                                  # what every ladder rung calls
    assert len(waits) == 2 and min(waits) > 0                                        # past the burst, each waits its turn
    monkeypatch.setenv("FOOTAGE_PACE_S", "0.5")                                      # a key already set stands
    assert podcast.footage_pace(run) and os.environ["FOOTAGE_PACE_S"] == "0.5"


def test_shorts_past_supply_continue_behind_the_wall_and_the_run_waits_for_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, "WORK_ROOT", str(tmp_path))
    run = podcast.Run("https://youtu.be/abcdefghijk", str(tmp_path))
    run.walled.set()
    st = os.path.join(str(tmp_path), "shorts", "m02", "work", "r1")
    os.makedirs(st)
    json.dump(dict(done=["ideas", "script", "supply"], pid=None), open(os.path.join(st, "run.json"), "w"))
    assert podcast.past_supply(os.path.join(str(tmp_path), "shorts", "m02"))
    assert not podcast.past_supply(os.path.join(str(tmp_path), "shorts", "m05"))
    started = []

    def proc(cmd, **kw):
        started.append(cmd)
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(delivered=str(tmp_path / "films" / "m02"))))
    monkeypatch.setattr(podcast.subprocess, "run", proc)
    rec = podcast.build_short(run, dict(id="m02", style="window", title="t"), dict(info={}, video="x"), {})
    assert started and rec["ok"]                                                     # its footage is in hand: it goes on
    # the run itself waits and continues the walled shorts until the wall lifts
    monkeypatch.setattr(podcast, "ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setattr(podcast, "WALL_WAITS", (600, 1200, 2400))
    slept, tries = [], {}

    def build_short(run, m, fetched, pod, tries_=3):
        tries[m["id"]] = tries.get(m["id"], 0) + 1
        if m["id"] == "m03" and tries["m03"] < 3:
            run.walled.set()
            return dict(id="m03", ok=False, error=f"supply: {wf1_film.WALL}: 9 of 10 footage sources still refused")
        return dict(id=m["id"], ok=True, folder="f")
    monkeypatch.setattr(podcast, "build_short", build_short)
    monkeypatch.setattr(podcast.time, "sleep", slept.append)
    run.walled.clear()
    got = podcast.shorts(run, dict(info={}, video=str(tmp_path / "v.mp4")), dict(moments=[dict(id=f"m0{i}") for i in (1, 2, 3)]), 1)
    assert slept == [600, 1200] and tries == {"m01": 1, "m02": 1, "m03": 3}
    assert len(got["made"]) == 3 and not got["failed"]


def test_once_the_wall_is_seen_the_shorts_not_started_wait_without_spending(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, "WORK_ROOT", str(tmp_path))
    run = podcast.Run("https://youtu.be/abcdefghijk", str(tmp_path))
    run.walled.set()
    monkeypatch.setattr(podcast.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a short was started behind the wall")))
    rec = podcast.build_short(run, dict(id="m05", style="window", title="t"), dict(info={}, video="x"), {})
    assert rec["ok"] is False and wf1_film.WALL in rec["error"] and not os.path.exists(os.path.join(str(tmp_path), "shorts", "m05", "done.json"))


def test_verified_source_cache_works_without_contacting_walled_metadata(tmp_path, monkeypatch):
    cache=tmp_path/'cache'; cache.mkdir()
    (cache/'podcast.mp4').write_bytes(b'cached-source')
    (cache/'info.json').write_text(json.dumps(dict(id='abcdefghijk',duration=120)))
    (cache/'captions.en.json3').write_text('{}')
    work=tmp_path/'work';work.mkdir()
    run=podcast.Run('https://youtu.be/abcdefghijk',str(work))
    monkeypatch.setenv('PODCAST_SOURCE_CACHE',str(cache))
    monkeypatch.setattr(podcast,'probe_s',lambda p:120)
    monkeypatch.setattr(podcast,'ladder',lambda *a,**k: (_ for _ in ()).throw(AssertionError('metadata contacted')))
    got=podcast.fetch(run)
    assert got['video']==str(cache/'podcast.mp4') and got['captions'].endswith('.json3')


# ----------------------------------------------------------------- never the same podcast twice (owner, 14 Nov 2026)

def _ledger():
    import delivered as DL
    return DL, DL.rows()


def test_a_delivery_is_recorded_with_its_exact_source_ids(tmp_path, monkeypatch, capsys):
    work_root, out = _fake_run(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk?si=x", "--out", out])
    assert podcast.main() == 0
    folder = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["delivered"]
    DL, rows = _ledger()
    assert len(rows) == 1 and rows[0]["wf"] == "wf1" and rows[0]["folder"] == folder and rows[0]["seeded"] is False
    import hashlib
    assert set(rows[0]["source_ids"]) == {"yt:abcdefghijk", "sha256:" + hashlib.sha256(b"x" * 1000).hexdigest()}
    assert rows[0]["idea"]["title"] == "The Episode" and rows[0]["idea"]["beats"] == ["Title m01", "Title m02", "Title m03"]


def test_the_same_bytes_under_another_link_are_refused_after_the_fetch_before_any_model(tmp_path, monkeypatch, capsys):
    work_root, out = _fake_run(tmp_path, monkeypatch)
    import hashlib
    import delivered as DL
    DL.append(DL.make_row("wf1", "/delivered/Earlier Folder", ["yt:zzzzzzzzzzz", "sha256:" + hashlib.sha256(b"x" * 1000).hexdigest()], dict(title="Earlier")))
    monkeypatch.setattr(podcast, "moments", lambda run, fetched: (_ for _ in ()).throw(AssertionError("a model was asked")))
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://example.com/the-same-episode.mp4", "--out", out])
    assert podcast.main() == 4
    lines = capsys.readouterr().out.strip().splitlines()
    ref = json.loads(lines[-1])
    assert ref["delivered"] == "/delivered/Earlier Folder" and ref["source_id"].startswith("sha256:")
    assert not os.path.exists(out) and os.listdir(work_root) == [] and len(DL.rows()) == 1


def test_a_test_run_may_reuse_a_delivered_source_and_never_delivers(tmp_path, monkeypatch, capsys):
    work_root, out = _fake_run(tmp_path, monkeypatch)
    import delivered as DL
    DL.append(DL.make_row("wf1", "/delivered/The Episode", ["yt:abcdefghijk"], dict(title="The Episode")))
    test_out = str(tmp_path / "test-out")
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--test-out", test_out])
    assert podcast.main() == 0
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["delivered"].startswith(test_out + os.sep) and not os.path.exists(out)
    assert len(DL.rows()) == 1                                    # a test is never recorded as a delivery
    # never combined with a delivery, never inside a delivery folder
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--test-out", test_out, "--out", out])
    with pytest.raises(SystemExit):
        podcast.main()
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(tmp_path / "Podcast Shorts"))
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--test-out", str(tmp_path / "Podcast Shorts" / "x")])
    with pytest.raises(SystemExit):
        podcast.main()
    assert len(DL.rows()) == 1 and not os.path.exists(tmp_path / "Podcast Shorts")


def test_a_test_run_and_a_delivery_never_continue_each_other(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, "WORK_ROOT", str(tmp_path / "work"))
    t, _ = podcast.find_run("https://youtu.be/abcdefghijk", test=True)
    json.dump(dict(video_id="abcdefghijk", test=True, done=[]), open(os.path.join(t, "run.json"), "w"))
    d, resumed = podcast.find_run("https://youtu.be/abcdefghijk")
    assert d != t and not resumed
    assert podcast.find_run("https://youtu.be/abcdefghijk", test=True) == (t, True)


def test_an_out_outside_the_delivery_folders_is_a_test_never_recorded(tmp_path, monkeypatch, capsys):
    """Only Podcast Shorts (or another delivery folder, however its path is spelled) is a delivery: any other --out is a
    test, which may reuse a delivered source and is never recorded."""
    work_root, out = _fake_run(tmp_path, monkeypatch)
    import delivered as DL
    DL.append(DL.make_row("wf1", "/delivered/The Episode", ["yt:abcdefghijk"], dict(title="The Episode")))
    lane = str(tmp_path / "lanes" / "wf1")
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", lane])
    assert podcast.main() == 0
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["delivered"].startswith(lane + os.sep) and len(DL.rows()) == 1
    # another spelling of the delivery folder is the delivery folder: refused there, and a test never writes into it
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out.upper()])
    assert podcast.main() == 4
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["refused"]
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--test-out", os.path.join(out.upper(), "t")])
    with pytest.raises(SystemExit):
        podcast.main()
    assert not os.path.exists(out) and len(DL.rows()) == 1


def test_each_short_carries_its_platform_song_to_the_podcast_folder(tmp_path, monkeypatch, capsys):
    """Owner, 15 Nov 2026: the publisher adds the song Valmera chose from the platform's own catalog. Each short's choice
    (made at its delivery, _shell/platform_song.py) reaches shorts.json and one platform_music.json naming the clean/ file."""
    work_root, out = _fake_run(tmp_path, monkeypatch)
    made = podcast.build_short

    def build_short(run, m, fetched, pod, tries=3):
        r = made(run, m, fetched, pod, tries)
        p = os.path.join(r["folder"], "film.json")
        rec = json.load(open(p))
        rec["platform_music"] = dict(upload="film_clean.mp4", song=f"Song {m['id']}", artist="A", start="0:00", original_sound_pct=100,
                                     added_sound_pct=25, alternates=[], why="fits", how="post it")
        json.dump(rec, open(p, "w"))
        return r
    monkeypatch.setattr(podcast, "build_short", build_short)
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out])
    assert podcast.main() == 0
    folder = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["delivered"]
    pm = json.load(open(os.path.join(folder, "platform_music.json")))
    assert [s["upload"] for s in pm["shorts"]] == ["clean/01 - Title m01.mp4", "clean/02 - Title m02.mp4", "clean/03 - Title m03.mp4"]
    assert pm["shorts"][0]["song"] == "Song m01" and pm["shorts"][0]["added_sound_pct"] == 25 and pm["how"] == "post it"
    man = json.load(open(os.path.join(folder, "shorts.json")))
    assert man["shorts"][1]["platform_music"]["upload"] == "clean/02 - Title m02.mp4"
    assert "platform_music.json" in open(os.path.join(folder, "README.md")).read()


@pytest.mark.parametrize("real,moving,documents_first", [(0, 0, False), (2, 2, False), (10, 4, False), (10, 7, True)])
def test_recovery_spends_on_motion_before_more_stills(tmp_path, monkeypatch, real, moving, documents_first):
    from types import SimpleNamespace
    import wf1_short, wf1_documents
    calls=[]
    old=dict(path="existing-original.jpg")
    pack=dict(coverage=dict(contract=3,windows=[dict(name="Google",must_show="Its actual premises",say="Google",t0=0,t1=5)]),
              referent_research={"references":[]},research_revision=7,
              footage=[],pictures={"Google":[old]},credits=[])
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=pack)))
    def documents(*args, **kwargs):
        calls.append("documents")
        return dict(pictures={"Google":[dict(path="new-original.jpg")]},credits=[],errors=[],spend=[])
    def footage(*args, **kwargs):
        calls.append("footage")
        assert kwargs["hear_s"]==0 and kwargs["motion_first"] and kwargs["scout_motion"]
        return dict(lines=[],credits=[])
    monkeypatch.setattr(wf1_documents,"acquire",documents)
    monkeypatch.setattr(wf1_short.chain,"footage",footage)
    monkeypatch.setattr(wf1_short,"_llm",lambda:object())
    ctx=SimpleNamespace(supply=str(tmp_path),log=lambda text:None)
    wf1_short.refill(ctx,dict(title="Company scale",style=dict(id="prints")),pack,
                     dict(weak_windows=[0],real_seconds=real,moving_seconds=moving,required_moving_seconds=6))
    assert calls==(["documents"] if documents_first else ["footage"])
    assert old in pack["pictures"]["Google"]


def test_the_selection_reads_the_conversation_in_ten_minute_parts(tmp_path, monkeypatch):
    """15 Nov: one reading of a 46-minute interview kept 10 moments. Each part is read on its own (with a minute past
    its end for a moment across the seam); the moments are pooled, overlaps keep the stronger, and there is no count."""
    import llm
    words = [(float(t), f"w{t}") for t in range(0, 2780, 2)]                     # 46 minutes
    parts = podcast.parts_of(words)
    assert [round(p[0]) for p in parts] == [0, 600, 1200, 1800, 2400] and parts[-1][1] > 2778
    assert all(w[0] < p[1] + podcast.PART_OVERLAP_S for p in parts for w in p[2]) and parts[0][2][-1][0] >= 650
    tail = podcast.parts_of([(float(t), "w") for t in range(0, 1300, 2)])
    assert [round(p[0]) for p in tail] == [0, 600] and tail[-1][1] > 1298                  # a 100 s tail joins the part before it
    (tmp_path / "words.json").write_text(json.dumps(words))
    asked = []

    def ask(system, content, **kw):
        k = len(asked)
        asked.append(content)
        assert "STARTS in this part" in content and f"part {k + 1} of 5" in content
        base = 600 * k
        ms = [dict(title=f"p{k}m{i}", t0=base + 120 * i, t1=base + 120 * i + 40, strength=8, shows=["Nvidia"],
                   styles=[dict(id="window")]) for i in range(5)]
        if k:
            ms.append(dict(title="seam twin", t0=base - 10, t1=base + 25, strength=7, shows=["TSMC"]))   # overlaps the part before's last
        return json.dumps(dict(podcast=dict(speaker="Jensen Huang"), moments=ms, left_out=[dict(t0=base + 5, title="x", why="y")])), {}
    monkeypatch.setattr(llm, "ask", ask)
    run = SimpleNamespace(p=lambda *a: str(tmp_path.joinpath(*a)))
    sel = podcast.moments(run, dict(info=dict(id="xCUala5j7aQ", title="CBS", duration=2778)))
    assert len(asked) == 5 and sel["parts"] == 5 and sel["found"] == 29
    assert len(sel["moments"]) == 25 and len(sel["left_out"]) == 5      # every part's moments, none capped; the weaker seam twin dropped
    assert "Only talk with nothing real in it at all is left out" in podcast.SELECT_CONTRACT and "two real things" not in podcast.SELECT_CONTRACT


def test_a_model_route_that_stops_answering_holds_the_delivery_and_the_shorts_not_started(tmp_path, monkeypatch, capsys):
    """16 Nov: the Codex limit hit at m05; m06-m10 were each marked failed in four seconds, and a real run would then
    have delivered the podcast short of them for ever. Now the run waits and continues on the same link."""
    limit = 'check: openai 502: {"error": {"message": "Local model failed: usage limit", "code": "codex_proxy_error"}}'
    assert podcast.route_out(limit) and podcast.route_out("script: openai 429: slow down") and not podcast.route_out("render: ineligible")
    assert podcast.route_out("script: openai 503: Claude backend daily call cap reached (1500/1500)")
    assert not podcast.route_out('check: openai 502: {"error": {"message": "Local model failed: Reading prompt from stdin"}}')
    monkeypatch.setattr(podcast, "WORK_ROOT", str(tmp_path))
    run = podcast.Run("https://youtu.be/abcdefghijk", str(tmp_path))
    started = []

    def proc(cmd, **kw):
        started.append(cmd)
        return SimpleNamespace(returncode=2, stdout=json.dumps(dict(stopped=dict(stage="check", error=limit[7:]))))
    monkeypatch.setattr(podcast.subprocess, "run", proc)
    monkeypatch.setenv("CODEX_PROXY_LOCAL", "1")
    m = lambda i: dict(id=f"m0{i}", style="window", title="t")                # noqa: E731
    first = podcast.build_short(run, m(5), dict(info={}, video="x"), {})
    later = podcast.build_short(run, m(6), dict(info={}, video="x"), {})
    assert len(started) == 1 and run.route_out.is_set()
    assert podcast.ROUTE_OUT in first["error"] and podcast.ROUTE_OUT in later["error"]
    assert not os.path.exists(os.path.join(str(tmp_path), "shorts", "m06", "done.json"))
    work_root, out = _fake_run(tmp_path / "r", monkeypatch, fail={"m03": f"{podcast.ROUTE_OUT}: {limit}"})
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out])
    assert podcast.main() == 2
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["stopped"]["stage"] == "deliver" and "waiting for the model route" in rep["stopped"]["error"] and not os.path.exists(out)
