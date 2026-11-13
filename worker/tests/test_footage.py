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


def test_the_switch_row_is_read_off_the_callers_path(monkeypatch):
    seam._CFG.update(cfg=None, at=0.0, busy=False)
    slow = []

    def read():
        time.sleep(0.3)
        slow.append(1)
        seam._CFG.update(cfg={"mode": "on", "test_user_ids": []}, at=time.monotonic(), busy=False)
    monkeypatch.setattr(seam, "_read_cfg", read)
    t0 = time.monotonic()
    assert seam.customer_cfg()["mode"] == "off"          # before the first read: off, at once
    assert time.monotonic() - t0 < 0.1
    time.sleep(0.5)
    assert slow and seam.customer_cfg()["mode"] == "on"
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
    item = dict(provider="footage", kind="video", local_file=str(clip), footage=dict(url="https://www.youtube.com/watch?v=x", time_range=[1, 2]))
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
