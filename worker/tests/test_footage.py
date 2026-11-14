"""Lane footage (13 Nov 2026): real video of the actual people, places and events, per script line.

What these hold without a network: the switch keeps customers off (rights are the owner's decision) and
costs a customer's supply nothing while off; a clip is 1-3 s inside one shot; the grade moves clips
toward one measured look and never wrecks one; the model's answers are read for what they say, a broken
answer costs that row only; the Bin and stock.download route a footage candidate through the one seam;
broll_source's picture check asks gpt-6-sol and falls back to the Claude model when OpenAI cannot answer.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import broll_source  # noqa: E402
import stock  # noqa: E402
from footage import cut, look, plan, seam, switch  # noqa: E402
from score import supply  # noqa: E402


def test_customers_are_off_by_default_and_any_bad_row_is_off():
    assert switch.allowed("customer", 7, switch.parse(None)) is False
    assert switch.allowed("customer", 7, switch.parse("not json")) is False
    assert switch.allowed("customer", 7, switch.parse({"mode": "maybe"})) is False
    assert switch.allowed("customer", 7, switch.parse({"mode": "off", "test_user_ids": [7]})) is True
    assert switch.allowed("customer", 8, switch.parse({"mode": "off", "test_user_ids": [7]})) is False
    assert switch.allowed("customer", None, switch.parse({"mode": "on"})) is True
    assert switch.allowed("customer", 7, switch.parse({"mode": "on", "test_user_ids": ["x"]})) is False


def test_the_owners_workflows_are_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("VALMERA_FOOTAGE", raising=False)
    assert switch.allowed("workflow")
    monkeypatch.setenv("VALMERA_FOOTAGE", "0")
    assert not switch.allowed("workflow")
    assert seam.for_lines([], [], "", "/nonexistent") is None


def test_while_off_a_customers_supply_gets_nothing_and_waits_for_nothing(monkeypatch):
    monkeypatch.setattr(seam, "customer_cfg", lambda: dict(switch.DEFAULTS))
    called = []
    import footage.run as frun
    monkeypatch.setattr(frun, "find", lambda *a, **k: called.append(1))
    t0 = time.monotonic()
    out = seam.bin_offers("/tmp", [{"want": "Oscar Piastri at McLaren"}], [[{"name": "Oscar Piastri"}]], time.monotonic() + 600)
    assert out == [] and not called and time.monotonic() - t0 < 0.5


def test_when_on_a_short_supply_budget_is_said_not_spent(monkeypatch):
    monkeypatch.setattr(seam, "customer_cfg", lambda: {"mode": "on", "test_user_ids": []})
    import footage.run as frun
    monkeypatch.setattr(frun, "find", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    report = []
    assert seam.bin_offers("/tmp", [{"want": "x"}], [[{"name": "X"}]], time.monotonic() + 30, report) == []
    assert "skipped" in report[0]["footage"]


def test_a_slow_first_switch_read_is_bounded_and_stays_off(monkeypatch):
    import threading
    seam._CFG.update(cfg=None, at=0.0, busy=False)
    release, finished = threading.Event(), threading.Event()

    def read():
        release.wait(2)
        seam._CFG.update(cfg={"mode": "on", "test_user_ids": []}, at=time.monotonic(), busy=False)
        seam._READY.set()
        finished.set()
    monkeypatch.setattr(seam, "_read_cfg", read)
    t0 = time.monotonic()
    assert seam.customer_cfg()["mode"] == "off"
    assert time.monotonic() - t0 < seam.CFG_FIRST_READ_S + 0.15
    release.set()
    assert finished.wait(1) and seam.customer_cfg()["mode"] == "on"
    seam._CFG.update(cfg=None, at=0.0, busy=False)


def test_a_clip_is_one_to_three_seconds_inside_one_shot():
    assert cut.window((10.0, 20.0), 15.0, 2.0) == (14.0, 16.0)
    a, b = cut.window((10.0, 20.0), 10.1, 2.0)
    assert a >= 10.12 and abs(b - a - 2.0) < 1e-6
    a, b = cut.window((10.0, 20.0), 19.9, 9.0)
    assert b <= 19.88 and abs(b - a - 3.0) < 1e-6
    a, b = cut.window((10.0, 20.0), 15.0, 0.2)
    assert abs(b - a - 1.0) < 1e-6
    a, b = cut.window((3.0, 3.9), 3.5, 2.0)              # a short shot gives what it has, never a cut across it
    assert a >= 3.12 and b <= 3.78 + 1e-9


def test_archived_checked_source_is_cut_without_reopening_youtube(tmp_path, monkeypatch):
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"checked archive source")
    monkeypatch.setattr(cut, "grab", lambda *args: (_ for _ in ()).throw(AssertionError("reopened blocked source")))
    calls = []
    monkeypatch.setattr(cut, "local", lambda *args: (calls.append(args) or (args[-1], "whole")))
    src = dict(url="https://www.youtube.com/watch?v=x", proxy=str(proxy), rung="wayback", dur_read=120)
    assert cut.source(src, None, 10, 12, "out.mp4") == ("out.mp4", "archive_proxy")
    assert calls[0][:3] == (str(proxy), 10, 12)


def test_remote_cut_failure_uses_whole_then_only_a_checked_proxy_range(tmp_path, monkeypatch):
    import pytest
    from footage import fetch
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"checked source")
    def blocked(*args):
        raise fetch.FetchError("remote seek blocked")
    monkeypatch.setattr(cut, "grab", blocked)
    calls = []
    monkeypatch.setattr(cut, "local", lambda *args: (calls.append(args) or (args[-1], "whole")))
    src = dict(url="https://www.youtube.com/watch?v=x", proxy=str(proxy), dur_read=20)
    assert cut.source(src, None, 10, 12, "out", whole=lambda: "whole.mp4")[1] == "whole"
    assert calls[-1][0] == "whole.mp4"
    assert cut.source(src, None, 10, 12, "out", whole=blocked)[1] == "verified_proxy"
    assert calls[-1][0] == str(proxy)
    with pytest.raises(fetch.FetchError):
        cut.source(src, None, 19, 22, "out", whole=blocked)


def test_the_grade_moves_each_clip_toward_one_measured_look_and_never_wrecks_one():
    clips = [dict(yavg=60, ylow=10, yhigh=120, sat=30), dict(yavg=140, ylow=40, yhigh=230, sat=70), dict(yavg=110, ylow=20, yhigh=200, sat=50)]
    tgt = cut.target(clips)
    assert not tgt["mono"] and tgt["yavg"] == 110
    dark = cut.eq_for(clips[0], tgt)
    assert dark["brightness"] > 0 and dark["contrast"] > 1 and dark["saturation"] > 1
    for c in clips:
        e = cut.eq_for(c, tgt)
        assert 0.85 <= e["contrast"] <= 1.25 and -0.1 <= e["brightness"] <= 0.1 and 0.75 <= e["saturation"] <= 1.3
    bw = [dict(yavg=100, ylow=20, yhigh=200, sat=2), dict(yavg=90, ylow=20, yhigh=190, sat=1), dict(yavg=120, ylow=30, yhigh=210, sat=40)]
    tb = cut.target(bw)
    assert tb["mono"] and cut.eq_for(bw[2], tb)["saturation"] == 0.0     # a mostly archive film is graded black and white
    assert cut.eq_for(dict(yavg=None), tgt) == dict(contrast=1.0, brightness=0.0, saturation=1.0)


def test_frames_come_from_the_moments_first_then_the_rest_of_the_source():
    shots = [(i * 2.0, i * 2.0 + 2.0) for i in range(100)]
    times = look.pick_times(shots, [101.0], cap=12)
    assert len(times) == 12
    assert {95.0, 97.0, 99.0, 101.0, 103.0, 105.0, 107.0} <= set(times)
    assert min(times) < 50 and max(times) > 150
    long_take = look.pick_times([(0.0, 40.0)], [], cap=36)      # a 40 s drive is looked at ten times, not once
    assert len(long_take) == 10 and long_take[0] == 2.0 and long_take[-1] == 38.0


def test_a_long_take_carries_several_lines_but_never_the_same_seconds_twice():
    from footage import run
    assert run._same("1970s FEDERAL EXPRESS PROMO FILM   FEDEX  DASSAULT", "1970s Federal Express promo film FedEx Dassault")
    assert not run._same("Fred Smith interview", "Fred Smith interview 2")


def test_the_sources_kept_are_the_models_choice_and_a_broken_row_costs_that_row(monkeypatch):
    from footage import ask
    subjects = [dict(name="Fred Smith", must_show="him"), dict(name="Memphis", must_show="the airport")]
    hits = {0: [dict(id="a", title="t", where="youtube", duration=100), dict(id="b", title="t", where="youtube", duration=100)],
            1: [dict(id="c", title="t", where="archive", duration=None)]}
    answer = {"keep": [{"s": 0, "h": 1, "why": "the interview"}, {"s": 0, "h": 9}, {"s": "x"}, {"s": 1, "h": 0}, {"s": 0, "h": 1}]}
    monkeypatch.setattr(ask, "ask", lambda *a, **k: (json.dumps(answer), 0.002))
    kept, usd = plan.rank(subjects, hits, per=3)
    assert [(s, h["id"]) for s, h, _ in kept] == [(0, "b"), (1, "c")] and usd == 0.002


def test_a_footage_candidate_has_its_own_licence_class_and_is_credited():
    c = dict(provider="footage", license=seam.LICENCE, page_url="https://www.youtube.com/watch?v=x")
    assert supply.licence_class(c) == ("F", seam.LICENCE, "https://www.youtube.com/watch?v=x")


def test_stock_download_routes_a_footage_candidate_through_the_seam(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 4096)
    out = tmp_path / "got.mp4"
    from footage import verify
    proof = dict(version=verify.VERSION, model=verify.MODEL, subject="test thing", kind="thing", ok=True, frames_match=[True]*3, sha256=verify.digest(str(clip)))
    item = dict(verification=proof, named="test thing", provider="footage", kind="video", local_file=str(clip), footage=dict(url="https://www.youtube.com/watch?v=x", time_range=[1, 2]))
    got = stock.download(item, str(out), 1080, 1920)
    assert out.read_bytes() == clip.read_bytes() and got["source_url"] == "https://www.youtube.com/watch?v=x"


def test_the_picture_check_asks_gpt_6_sol_and_falls_back_to_claude(monkeypatch):
    from score import call
    seen = []

    class R:
        text, usd = '{"fit":[]}', 0.001

    def stream(model, *a, **k):
        seen.append(model)
        if model.startswith("gpt-"):
            raise call.Refused("the OpenAI rung has no OpenAI key")
        return R()
    monkeypatch.setattr(call, "stream", stream)
    assert broll_source.NAMED_CHECK_MODEL == "gpt-6-sol"
    txt, usd = broll_source._ask("s", [], "named_check", 10.0, 100, model=broll_source.NAMED_CHECK_MODEL)
    assert seen == ["gpt-6-sol", broll_source.NAMED_MODEL] and usd == 0.001
    seen.clear()

    def late(model, *a, **k):
        seen.append(model)
        raise call.Late("past its deadline")
    monkeypatch.setattr(call, "stream", late)
    try:
        broll_source._ask("s", [], "named_check", 10.0, 100, model="gpt-6-sol")
        raise AssertionError("a late call must not run twice")
    except call.Late:
        assert seen == ["gpt-6-sol"]


def test_the_shell_reads_each_lines_spoken_length_from_the_narrations_words():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                    "plugins", "valmera", "skills", "_shell"))
    try:
        import chain
    except Exception:  # noqa: BLE001 the shell's Mac-only imports: nothing to check here
        return
    words = [dict(seg=0, start=0.0, end=0.4), dict(seg=0, start=0.5, end=1.9), dict(seg=1, start=2.3, end=3.0), dict(seg=2, start=3.4, end=4.0)]
    assert chain.seg_durs(words, 4) == [2.3, 1.1, 0.6, None]


def _identity_world(monkeypatch, faces_by_frame):
    """identity.keep_person with its face reader replaced: faces_by_frame maps a frame's (source, t) to
    [(embedding, area, cx)]."""
    import numpy as np
    from footage import identity
    cv2 = __import__("cv2")
    monkeypatch.setattr(identity, "models", lambda: ("yunet.onnx", "sface.onnx"))
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(cv2.FaceRecognizerSF, "create", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(identity, "full_frame", lambda proxy, t, out: f"{proxy}|{t}")

    def faces(path, det, rec):
        if "|" not in path:                                  # an anchor photograph
            return [(np.array(faces_by_frame["anchor"], dtype=np.float32), [0, 0, 1, 1], 0.2, 0.5)]
        proxy, t = path.split("|")
        return [(np.array(v, dtype=np.float32), [0, 0, 1, 1], a, cx) for v, a, cx in faces_by_frame.get((proxy, float(t)), [])]
    monkeypatch.setattr(identity, "faces", faces)
    return identity


def test_a_host_who_passed_the_look_is_dropped_by_the_founders_face(monkeypatch, tmp_path):
    founder, host = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    world = {("p0", 1.0): [(founder, 0.1, 0.3)], ("p0", 2.0): [(host, 0.1, 0.6)], ("p1", 3.0): [(founder, 0.1, 0.7)], ("p1", 4.0): []}
    identity = _identity_world(monkeypatch, world)
    rows = [dict(src=0, t=1.0, ok=True, grade=4, source="s0"), dict(src=0, t=2.0, ok=True, grade=5, source="s0"),
            dict(src=1, t=3.0, ok=True, grade=4, source="s1"), dict(src=1, t=4.0, ok=True, grade=4, source="s1")]
    out, rep = identity.keep_person(rows, {0: "p0", 1: "p1"}, str(tmp_path))
    kept = {(r["src"], r["t"]) for r in out if r["ok"]}
    assert kept == {(0, 1.0), (1, 3.0), (1, 4.0)} and rep["dropped"] == 1
    assert next(r for r in out if r["t"] == 1.0)["x"] == 0.3                 # the crop follows his face
    assert next(r for r in out if r["t"] == 4.0)["grade"] == 3                # no face read: kept, one grade down


def test_one_programmes_most_frequent_face_is_not_taken_for_the_person(monkeypatch, tmp_path):
    host, founder = [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]
    world = {("p0", 1.0): [(host, 0.1, 0.5)], ("p0", 2.0): [(host, 0.1, 0.5)], ("p0", 3.0): [(host, 0.1, 0.5)], ("p1", 4.0): [(founder, 0.1, 0.5)]}
    identity = _identity_world(monkeypatch, world)
    rows = [dict(src=s, t=t, ok=True, grade=4, source=f"s{s}") for s, t in ((0, 1.0), (0, 2.0), (0, 3.0), (1, 4.0))]
    out, rep = identity.keep_person(rows, {0: "p0", 1: "p1"}, str(tmp_path))
    assert "skipped" in rep["identity"] and all(r["ok"] for r in out)        # no guess: the look's verdict stands
    world["anchor"] = founder                                                # a checked photograph settles it
    rows = [dict(src=s, t=t, ok=True, grade=4, source=f"s{s}") for s, t in ((0, 1.0), (0, 2.0), (0, 3.0), (1, 4.0))]
    out, rep = identity.keep_person(rows, {0: "p0", 1: "p1"}, str(tmp_path), anchors=["photo.jpg"])
    assert [r["t"] for r in out if r["ok"]] == [4.0] and rep["dropped"] == 3


def _ffmpeg():
    import shutil
    return os.environ.get("FOOTAGE_FFMPEG") or shutil.which("ffmpeg")


def test_a_sources_own_black_bars_and_a_timecode_band_never_reach_the_film(tmp_path, monkeypatch):
    import subprocess
    import pytest
    ff = _ffmpeg()
    if not ff:
        pytest.skip("no ffmpeg")
    monkeypatch.setenv("FOOTAGE_FFMPEG", ff)
    src = tmp_path / "pillarboxed.mp4"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=480x480:d=1.2", "-vf", "pad=854:480:187:0:black",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    assert cut.borders(str(src)) == "478:480:188:0"
    plain = tmp_path / "plain.mp4"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)], check=True)
    assert cut.borders(str(plain)) is None
    out = tmp_path / "out.mp4"
    cut.finish(str(src), str(out), 540, 960, 0.5, dict(contrast=1.1, brightness=0.02, saturation=0.9), trim=[0.0, 0.15])
    from footage import shots
    p = shots.probe(str(out))
    assert (p["w"], p["h"]) == (540, 960) and not p["audio"]


def test_a_clip_without_a_channel_in_the_search_gets_it_for_its_credit(monkeypatch):
    import net_fetch
    from footage import search
    seen = {}

    def get_json(url, params=None, timeout_s=None, **k):
        seen.update(url=url, params=params)
        return {"author_name": "CBS Sunday Morning"}
    monkeypatch.setattr(net_fetch, "get_json", get_json)
    assert search.channel_of("https://www.youtube.com/watch?v=xrLN7xcx67g") == "CBS Sunday Morning"
    assert seen["url"].endswith("/oembed") and seen["params"]["url"].endswith("xrLN7xcx67g")
    monkeypatch.setattr(net_fetch, "get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert search.channel_of("https://www.youtube.com/watch?v=x") is None


def test_a_film_youtube_will_not_open_is_looked_for_on_the_archive_by_its_title():
    from footage import run
    a = '1970s FEDERAL EXPRESS PROMO FILM   FEDEX  DASSAULT CARGO FALCON JET  MEMPHIS  87744'
    b = '1970s FEDERAL EXPRESS PROMO FILM FEDEX DASSAULT CARGO FALCON JET MEMPHIS TENNESSEE 87744'
    assert run._title_q(a) == "1970s FEDERAL EXPRESS PROMO FILM FEDEX"            # the title's first words, letters and digits only
    assert run._twin(a, b) and not run._twin(a, "1970s FedEx hub at night, a home movie") and not run._twin("FedEx", "FedEx")


def test_a_line_keeps_its_own_number_and_seconds_already_cut_are_not_cut_again(monkeypatch, tmp_path):
    from footage import run
    lines, named, title = run.lines_of({"title": "T", "segments": [{"i": 4, "say": "a"}, {"i": 9, "say": "b"}, {"say": "c"}]})
    assert [ln["i"] for ln in lines] == [4, 9, 2]                                  # a top-up run names the film's own lines
    seen = {}

    def fake_find(lines, named, about, out_dir, W=1080, H=1920, log=print, anchors=None, avoid=None, **kw):
        seen["avoid"] = avoid
        return {"counts": {}, "spend": {}, "seconds": 0}
    monkeypatch.setattr(run, "find", fake_find)
    res = seam.for_lines([], [], "t", str(tmp_path), avoid=[{"url": "u", "time_range": [1.0, 2.5]}])
    assert res is not None and seen["avoid"] == [{"url": "u", "time_range": [1.0, 2.5]}]


def test_a_youtube_video_no_rung_opens_is_asked_of_the_wayback_machine(monkeypatch):
    """Walled as a bot on every rung (or removed), a YouTube source is tried once more from the Wayback Machine's
    archived copy; another site's failure is not, and FOOTAGE_WAYBACK=0 turns the rung off."""
    from footage import fetch
    calls = []

    class R:
        def __init__(self, code, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err

    def fake_run(cmd, **kw):
        url = cmd[-1]
        calls.append(url)
        if url.startswith(fetch.WAYBACK):
            return R(0, '{"id": "x"}')
        return R(1, "", "ERROR: [youtube] x: Sign in to confirm you're not a bot")
    monkeypatch.setattr(fetch.subprocess, "run", fake_run)
    monkeypatch.setattr(fetch, "_rungs", lambda url: [([], "default")])
    monkeypatch.delenv("FOOTAGE_WAYBACK", raising=False)
    out, rung = fetch.run_ladder("https://www.youtube.com/watch?v=x", ["-J"], done=lambda s: s.strip().startswith("{"))
    assert rung == "wayback" and calls[-1] == fetch.WAYBACK + "https://www.youtube.com/watch?v=x"
    calls.clear()
    try:
        fetch.run_ladder("https://archive.org/details/some-film", ["-J"])
        assert False, "a failed archive.org source has no second copy"
    except fetch.FetchError:
        assert all(not u.startswith(fetch.WAYBACK) for u in calls)
    monkeypatch.setenv("FOOTAGE_WAYBACK", "0")
    try:
        fetch.run_ladder("https://www.youtube.com/watch?v=x", ["-J"])
        assert False
    except fetch.FetchError:
        pass


def test_voice_align_finds_the_chosen_words_in_what_was_heard():
    from footage import voice
    heard = [dict(w=w, start=i * 0.3, end=i * 0.3 + 0.25) for i, w in enumerate(
        "and so I said look I was willing to take a chance because losing wasn't the worst thing yeah".split())]
    i, j, share = voice.align(heard, "I was willing to take a chance because losing wasn't the worst thing.")
    assert [w["w"] for w in heard[i:j]][0] == "I" and heard[j - 1]["w"] == "thing" and share == 1.0
    assert heard[i - 1]["w"] == "look"            # the first "I" (before "said") is not where the sentence starts
    assert voice.align(heard, "") is None
    far = voice.align(heard, "the weather in Memphis was fine that day")
    assert far is None or far[2] < 0.8


def test_a_faster_caller_asks_for_more_shorter_clips_and_the_default_is_unchanged():
    from footage import run
    for dur in (0.8, 1.5, 2.9, 4.4, 7.0):
        n, w = run.line_shots(dur)
        assert n == max(1, min(4, round(dur / run.SHOT_S))) and w == dur / n          # absent: exactly as before
    n, w = run.line_shots(3.0, shot_s=0.45, line_max=10)
    assert n == 9 and w == cut.MIN_S                                                   # ceil(3.0 / 0.45) + 2, window floor 1.0 s
    assert run.line_shots(9.0, shot_s=0.45, line_max=10)[0] == 10
    assert run.line_shots(2.0, shot_s=0.9)[1] == 1.6


def test_a_brief_rides_with_its_line_into_the_plan(monkeypatch):
    from footage import run
    lines, _, _ = run.lines_of({"segments": [{"say": "a", "see": [{"thing": "X", "as_of": "1995", "moment": "the fall"}], "era": 1995, "beat": "turn"},
                                             {"say": "b"}]})
    assert lines[0]["see"][0]["moment"] == "the fall" and lines[0]["era"] == 1995 and "see" not in lines[1]
    got = {}

    def fake_ask(system, content, what, **kw):
        got["content"] = content
        return '{"subjects": [], "lines": []}', 0.0
    monkeypatch.setattr(plan.ask, "ask", fake_ask)
    plan.plan(lines, [], "t")
    assert '"moment": "the fall"' in got["content"] and "b" in got["content"]
    plan.plan([{"i": 0, "say": "a", "pictures": ["X"]}], [], "t")
    assert got["content"].endswith("[]") and "see:" not in got["content"]


def test_the_seam_passes_the_faster_pace_through(monkeypatch, tmp_path):
    from footage import run
    seen = {}

    def fake_find(lines, named, about, out_dir, W=1080, H=1920, log=print, anchors=None, avoid=None, **kw):
        seen.update(kw)
        return {"counts": {}, "spend": {}, "seconds": 0}
    monkeypatch.setattr(run, "find", fake_find)
    seam.for_lines([], [], "t", str(tmp_path), shot_s=0.45, line_max=10, per=5)
    assert seen["shot_s"] == 0.45 and seen["line_max"] == 10 and seen["per"] == 5


# ----------------------------------------------------------------- round 2 (valmera-stories): opt-in strict lines, motion first, round robin

def _good(src, t, lines, subject="A", grade=4):
    return dict(source=src, t=t, lines=lines, subject=subject, grade=grade, ok=True, clean=True)


def _src(key, shots_):
    return dict(key=key, url=f"u{key}", shots=shots_)


def test_assign_without_flags_is_todays_step_six():
    from footage import run
    src_of = {"s0": _src("s0", [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]), "s1": _src("s1", [(0.0, 4.0)])}
    good = [_good("s0", 1.5, [0]), _good("s0", 4.5, []), _good("s1", 2.0, [1]), _good("s0", 7.5, [1], subject="B")]
    lines = [dict(i=0, say="a b c d", pictures=["A"], dur=2.0), dict(i=1, say="e f", pictures=["B"], dur=1.5)]
    rows = run.assign(lines, good, src_of, {}, {})
    assert [len(r["picks"]) for r in rows] == [1, 1] and rows[0]["picks"][0]["t"] == 1.5 and rows[1]["picks"][0]["source"] in ("s1", "s0")


def test_strict_lines_never_fill_a_line_by_subject_alone():
    from footage import run
    src_of = {"s0": _src("s0", [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)])}
    good = [_good("s0", 1.5, [0]), _good("s0", 4.5, []), _good("s0", 7.5, [2])]           # a frame of the subject named for no line
    lines = [dict(i=1, say="x y z", pictures=["A"], dur=3.0)]
    loose = run.assign(lines, good, src_of, {}, {})
    strict = run.assign(lines, good, src_of, {}, {}, strict=True)
    assert loose[0]["picks"] and strict[0]["picks"] == []


def test_round_robin_gives_the_last_line_its_picks_from_a_shared_pool():
    from footage import run
    shots_ = [(k * 2.0, k * 2.0 + 2.0) for k in range(6)]
    src_of = {"s0": _src("s0", shots_)}
    good = [_good("s0", a + 1.0, [0, 1, 2]) for a, _ in shots_]
    lines = [dict(i=i, say="w " * 10, pictures=["A"], dur=3.0) for i in range(3)]
    seq = run.assign(lines, good, src_of, {}, {}, shot_s=0.45, line_max=4)
    rr = run.assign(lines, good, src_of, {}, {}, shot_s=0.45, line_max=4, alloc="round")
    assert len(seq[2]["picks"]) == 0 and len(seq[0]["picks"]) >= 4               # line by line: the first lines take the pool
    assert [len(r["picks"]) for r in rr] == [2, 2, 2]                             # one a round: the pool is shared


def test_motion_first_chooses_the_moving_shot_and_sits_on_its_peak(tmp_path):
    import subprocess
    from footage import motion, run
    ff = os.environ.get("FOOTAGE_FFMPEG") or "/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg"
    if not os.path.exists(ff):
        import shutil
        ff = shutil.which("ffmpeg")
    if not ff:
        import pytest
        pytest.skip("no ffmpeg")
    p = str(tmp_path / "src.mp4")
    # 0-3 s one frozen frame (a still shot), 3-6 s a moving pattern
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=320x240:rate=15:duration=3", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=15:duration=3", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-pix_fmt", "yuv420p", p],
                   check=True)
    os.environ["FOOTAGE_FFMPEG"] = ff
    mo = motion.of_shots(p, [(0.0, 3.0), (3.0, 6.0)])
    assert mo[(0.0, 3.0)]["p90"] < motion.FLOOR <= mo[(3.0, 6.0)]["p90"], mo
    assert 3.0 <= mo[(3.0, 6.0)]["peak"] <= 6.0
    src_of = {"s0": _src("s0", [(0.0, 3.0), (3.0, 6.0)])}
    good = [_good("s0", 1.5, [0], grade=5), _good("s0", 3.6, [0], grade=3)]
    lines = [dict(i=0, say="a b", pictures=["A"], dur=1.0)]
    rows = run.assign(lines, good, src_of, {}, {}, motion_of={"s0": mo})
    pick = rows[0]["picks"][0]
    assert pick["t"] == 3.6 and pick["motion_p90"] >= motion.FLOOR                 # moving before a higher grade
    assert pick["window"][0] <= 3.6 <= pick["window"][1]                         # the checked frame kept inside the window
    peak = mo[(3.0, 6.0)]["peak"]
    plain = cut.window((3.0, 6.0), 3.6, 1.0)
    assert abs(sum(pick["window"]) / 2 - peak) <= abs(sum(plain) / 2 - peak) + 1e-6      # moved toward the motion peak


def test_a_lost_cut_is_reassigned_to_another_source(monkeypatch, tmp_path):
    """reassign_lost: a pick whose cut fails (a bot wall) marks its source dead; its line is assigned again over the frames
    left and cut once more; footage.json records lost and replaced."""
    from footage import fetch, run, shots as fshots, verify
    monkeypatch.setenv("FOOTAGE_VOICES", "0")
    monkeypatch.setattr(run.plan, "plan", lambda lines, named, about: (dict(subjects=[dict(name="A", kind="thing", must_show="A", searches=[dict(where="youtube", q="A")])],
                                                                         lines=[dict(i=0, show="A", subjects=["A"])]), 0.0))
    monkeypatch.setattr(run.search, "run", lambda qs: {0: [dict(id="h0", title="T0", url="u0", where="youtube", channel="c"),
                                                           dict(id="h1", title="Other one", url="u1", where="youtube", channel="c")]})
    monkeypatch.setattr(run.plan, "rank", lambda subjects, hits, per=3: ([(0, h, "w") for h in hits[0]], 0.0))
    monkeypatch.setattr(run, "_open", lambda s, work, log, hear_s=None, read_s=None: dict(s, proxy=None, shots=[(0.0, 4.0), (4.0, 8.0), (8.0, 12.0), (12.0, 16.0)], transcript=[]))
    monkeypatch.setattr(run.look, "moments", lambda sub, need, local: ([], 0.0))
    monkeypatch.setattr(fshots, "frames", lambda proxy, times, d: {t: f"{d}/{t}.jpg" for t in times})
    monkeypatch.setattr(run.look, "check", lambda sub, need, cands, local, work: ([dict(c, ok=True, clean=True, grade=4 if c["source"] == "s0" else 3,
                                                                                           lines=[0], shows="A") for c in cands], 0.0))
    monkeypatch.setattr(fetch, "streams", lambda url: None)

    def source(s, stream, t0, t1, raw, whole=None, downloaded_only=False):
        if s["key"] == "s0":
            raise RuntimeError("bot wall")
        open(raw, "wb").write(b"x")
        return raw, "stream"
    monkeypatch.setattr(run.cut, "source", source)
    monkeypatch.setattr(run.cut, "measure", lambda raw: dict(y=0.5))
    monkeypatch.setattr(run.cut, "target", lambda stats: dict(y=0.5))
    monkeypatch.setattr(run.cut, "eq_for", lambda st, tgt: {})
    monkeypatch.setattr(run.cut, "finish", lambda raw, out, W, H, x, eq, trim=None, preserve_source=False: open(out, "wb").write(b"c"))
    monkeypatch.setattr(verify, "clip", lambda path, sub, dur: (dict(ok=True), 0.0))
    lines = [dict(i=0, say="one two three four", pictures=["A"], dur=1.4)]
    res = run.find(lines, [dict(name="A", **{"is": "thing"})], "t", str(tmp_path / "out"), shot_s=0.45, line_max=4, reassign_lost=True, keep_work=False)
    ln = res["lines"][0]
    assert ln["lost"] >= 1 and ln["replaced"] >= 1 and ln["clips"] and all(c["source"]["url"] == "u1" for c in ln["clips"])


# ----------------------------------------------------------------- round 3 (valmera-stories): motion-first sampling, the brief's per-line checks

def _old_pick_times(shots, said, cap=look.PER_SOURCE, around_s=8.0, min_s=0.8):
    pts = look.points(shots, min_s=min_s)
    near = []
    for t in said:
        for p, _ in pts:
            if abs(p - t) < around_s and p not in near:
                near.append(p)
    near = near[:max(cap * 2 // 3, cap - 8)]
    rest = [p for p, _ in pts if p not in near]
    room = cap - len(near)
    if room > 0 and rest:
        step = max(1, len(rest) // room)
        near += rest[::step][:room]
    return sorted(near)


def test_motion_first_sampling_fills_with_moving_shots_and_caps_still_moments():
    shots_ = [(k * 1.0, k * 1.0 + 1.0) for k in range(300)]
    moving = {shots_[k] for k in range(0, 300, 3)[:97]}
    mv_pts = {round(a + 0.5, 2) for a, _ in moving}
    picks = look.pick_times(shots_, [], cap=36, moving=moving, min_s=0.3)
    assert len(picks) == 36 and set(picks) <= mv_pts                         # all 36 moving
    assert max(picks) - min(picks) > 200                                       # spread over the moving set
    said = [40.0, 150.0, 250.0]
    picks = look.pick_times(shots_, said, cap=36, moving=moving, min_s=0.3)
    still_near = [p for p in picks if p not in mv_pts and any(abs(p - t) < 8.0 for t in said)]
    assert len(still_near) <= 12 and len(picks) == 36
    for sh, sd in (([(i * 2.0, i * 2.0 + 2.0) for i in range(100)], [101.0]), ([(0.0, 40.0)], [])):
        assert look.pick_times(sh, sd, cap=12) == _old_pick_times(sh, sd, cap=12)   # moving=None: exactly as before


def test_the_look_keeps_only_lines_whose_three_brief_checks_pass(monkeypatch, tmp_path):
    from PIL import Image
    jpg = str(tmp_path / "f.jpg")
    Image.new("RGB", (64, 36), (90, 90, 90)).save(jpg)
    seen = {}

    def ask_(system, content, what, budget_s=0, max_tokens=0):
        seen.setdefault("systems", []).append(system)
        seen.setdefault("texts", []).append(content[0]["text"])
        cells = [dict(n=1, ok=True, clean=True, grade=4, lines=[0, 1, 2], shows="x", x=0.5,
                      line_checks=[dict(line=0, version_ok=True, time_ok=True, exact_thing=True),
                                   dict(line=1, version_ok=False, time_ok=True, exact_thing=True)])]
        return json.dumps(dict(cells=cells)), 0.0
    monkeypatch.setattr(look.ask, "ask", ask_)
    sub = dict(name="Toy Story", must_show="the film")
    cands = [dict(src=0, t=1.0, jpg=jpg, said="")]
    srcs = [dict(title="T", channel="c")]
    brief = [dict(i=0, say="a", see=[dict(thing="Toy Story", as_of="1995 release", moment="the window fall")]), dict(i=1, say="b"), dict(i=2, say="c")]
    out, _ = look.check(sub, brief, cands, srcs, str(tmp_path))
    assert out[0]["lines"] == [0] and out[0]["lines_offered"] == [0, 1, 2]          # line 1 fails version, line 2 was not checked
    assert "line_checks" in seen["systems"][0] and "as of: 1995 release" in seen["texts"][0] and "moment: the window fall" in seen["texts"][0]
    plain = [dict(i=0, say="a"), dict(i=1, say="b"), dict(i=2, say="c")]
    out, _ = look.check(sub, plain, cands, srcs, str(tmp_path))
    assert out[0]["lines"] == [0, 1, 2] and "lines_offered" not in out[0] and seen["systems"][1] == look.LOOK_SYSTEM
    assert "[see:" not in seen["texts"][1]
