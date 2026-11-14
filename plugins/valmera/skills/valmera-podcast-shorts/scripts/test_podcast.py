"""Tests for the podcast run (podcast.py) and the film's gates (wf1_film.py). No network, no model, no render.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_podcast.py
"""
import json
import os
import sys

os.environ["PODCAST_NO_REEXEC"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import podcast  # noqa: E402
import wf1_film  # noqa: E402


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
    # the same link again is a new run and a new folder: a delivered folder is never added to
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out])
    assert podcast.main() == 0
    again = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["delivered"]
    assert again != folder and sorted(os.listdir(folder)) == names and len(os.listdir(out)) == 2


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
    ok, bad, way = dict(where="youtube"), dict(where="youtube", error="default: Sign in to confirm"), dict(where="youtube", rung="wayback")
    arch = dict(where="archive")

    def foot(clips, with_footage, lines, sources):
        return dict(counts=dict(clips=clips, lines_with_footage=with_footage, lines=lines), sources=sources)
    assert walled(foot(0, 0, 13, [bad, bad, bad]))
    assert walled(foot(3, 3, 13, [way, way, bad, ok, arch, arch]))                    # a clip or three through the archive: still walled
    assert not walled(foot(24, 13, 13, [ok, ok, bad]))                                # footage came: a film is made
    assert not walled(foot(9, 5, 13, [way, bad, ok]))                                 # refused, but enough was found to cut on
    assert not walled(foot(0, 0, 13, [ok, ok, ok, bad]))                              # the sources opened and showed nothing: photographs carry it
    assert not walled(None) and not walled(foot(0, 0, 13, [])) and not walled(foot(0, 0, 13, [arch, arch]))
    assert ns["refused"](foot(0, 0, 1, [ok, bad, way, arch])) == (3, 2)


def test_shorts_waiting_for_footage_hold_the_delivery_and_the_run_continues_later(tmp_path, monkeypatch, capsys):
    work_root, out = _fake_run(tmp_path, monkeypatch, fail={"m03": wf1_film.WALL + ": 9 of 10 footage sources would not open"})
    monkeypatch.setattr(sys, "argv", ["podcast.py", "https://youtu.be/abcdefghijk", "--out", out])
    assert podcast.main() == 2
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["stopped"]["stage"] == "deliver" and "waiting for footage" in rep["stopped"]["error"] and not os.path.exists(out)
    work = os.path.join(work_root, os.listdir(work_root)[0])
    assert sorted(os.listdir(os.path.join(work, "films"))) == ["m01", "m02"]           # what was made is kept for the run that completes the set


def test_once_the_wall_is_seen_the_shorts_not_started_wait_without_spending(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, "WORK_ROOT", str(tmp_path))
    run = podcast.Run("https://youtu.be/abcdefghijk", str(tmp_path))
    run.walled.set()
    monkeypatch.setattr(podcast.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a short was started behind the wall")))
    rec = podcast.build_short(run, dict(id="m05", style="window", title="t"), dict(info={}, video="x"), {})
    assert rec["ok"] is False and wf1_film.WALL in rec["error"] and not os.path.exists(os.path.join(str(tmp_path), "shorts", "m05", "done.json"))
