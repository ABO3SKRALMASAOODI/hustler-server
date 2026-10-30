"""E3 (30 Oct 2026): the render memo answers a repeated measurement from
disk with EXACTLY what the measurement returns, and is off unless the lane
turns it on (RENDER_MEMO=1, the box's slots).

The picture check and the motion layer's browser probes were the largest
re-done work of a factory short's 3-4 previews and its final (a quiet-box
profile of a 30 s approval preview: 174 CPU-seconds, 66 in the picture
check, 39 in the motion layer). These tests hold the contract that makes
remembering them safe: a hit equals a fresh answer (types included), the
key moves with every input and with the code that computes it, failed or
budget-cut answers are never stored, and nothing changes while it is off.
"""

import inspect
import json
import os
import sys

import numpy as np
import pytest

import render_memo


@pytest.fixture
def memo(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_MEMO", "1")
    monkeypatch.setenv("RENDER_MEMO_DIR", str(tmp_path / "memo"))
    return tmp_path / "memo"


def _files(root):
    return [os.path.join(d, f) for d, _s, fs in os.walk(root) for f in fs
            if f.endswith(".json")]


def test_pack_round_trips_to_equal_objects_of_the_same_types():
    grid = np.array([[True, False], [False, True]])
    value = {
        "faces": [([0.1, 0.2, 0.3, 0.4], -1), ([0.5, 0.25, 0.75, 0.5], 0)],
        "eyes": (2, [0.1, 0.2, 0.3, 0.4]),
        "none": (None, None),
        "grid": grid,
        "scalar": np.float32(0.125),
        "count": np.int64(7),
        "keyed": {3: "three", (1, 2): [1.5]},
        "nan": float("nan"),
        "set": {1, 2},
        "tag": {"__t__": "user data"},
    }
    back = render_memo.unpack(json.loads(json.dumps(render_memo.pack(value))))
    assert back["faces"] == value["faces"]
    assert all(isinstance(f, tuple) for f in back["faces"])
    assert back["eyes"] == (2, [0.1, 0.2, 0.3, 0.4]) and \
        isinstance(back["eyes"], tuple)
    assert back["none"] == (None, None)
    assert back["grid"].dtype == grid.dtype and (back["grid"] == grid).all()
    assert back["scalar"] == value["scalar"] and \
        type(back["scalar"]) is np.float32
    assert back["count"] == 7 and type(back["count"]) is np.int64
    assert back["keyed"] == value["keyed"]
    assert back["nan"] != back["nan"]
    assert back["set"] == {1, 2}
    assert back["tag"] == {"__t__": "user data"}


def test_off_by_default_computes_every_time_and_writes_nothing(
        tmp_path, monkeypatch):
    monkeypatch.delenv("RENDER_MEMO", raising=False)
    monkeypatch.setenv("RENDER_MEMO_DIR", str(tmp_path / "memo"))
    calls = []
    for _ in range(2):
        assert render_memo.memoize("ns", "k" * 48,
                                   lambda: calls.append(1) or [1]) == [1]
    assert len(calls) == 2
    assert not (tmp_path / "memo").exists()


def test_a_hit_is_the_stored_answer_and_the_computation_runs_once(memo):
    calls = []

    def compute():
        calls.append(1)
        return [([0.1, 0.2, 0.3, 0.4], 1)]
    k = render_memo.key("ns", "frame", b"\x00\x01")
    first = render_memo.memoize("faces", k, compute)
    second = render_memo.memoize("faces", k, compute)
    assert first == second and isinstance(second[0], tuple)
    assert len(calls) == 1
    assert len(_files(memo)) == 1


def test_keys_move_with_every_input():
    a = np.zeros((4, 4), np.uint8)
    b = a.copy()
    b[0, 0] = 1
    base = render_memo.key("faces", "code", render_memo.array_part(a))
    assert base == render_memo.key("faces", "code", render_memo.array_part(
        a.copy()))
    assert base != render_memo.key("faces", "code", render_memo.array_part(b))
    assert base != render_memo.key("faces", "code2",
                                   render_memo.array_part(a))
    assert base != render_memo.key("faces", "code", render_memo.array_part(
        a.reshape(2, 8)))
    assert base != render_memo.key("other", "code",
                                   render_memo.array_part(a))


def test_code_digest_moves_when_the_source_changes(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("X = 1\n")
    one = render_memo.code_digest(str(src))
    src.write_text("X = 2\n")
    os.utime(src, ns=(1, 1))
    assert render_memo.code_digest(str(src)) != one


def test_a_damaged_answer_is_a_miss_and_is_recomputed(memo):
    k = render_memo.key("ns", 1)
    render_memo.memoize("faces", k, lambda: [1])
    for path in _files(memo):
        with open(path, "w") as f:
            f.write("{broken")
    assert render_memo.memoize("faces", k, lambda: [2]) == [2]


def test_encode_miss_keeps_an_answer_out(memo):
    k = render_memo.key("ns", 2)
    render_memo.memoize("kofaces", k, lambda: None,
                        encode=lambda r: render_memo.MISS)
    assert not _files(memo)


def test_prune_keeps_the_newest_answers(memo):
    for i in range(12):
        k = render_memo.key("ns", i)
        render_memo.put("faces", k, [i])
        p = render_memo._path("faces", k)
        os.utime(p, (1000 + i, 1000 + i))
    assert render_memo.prune(5) == 7
    left = sorted(json.load(open(p))["a"][0] for p in _files(memo))
    assert left == [7, 8, 9, 10, 11]


# ── follow.detect_memo ─────────────────────────────────────────────────────

def test_detect_memo_is_detect_when_off(monkeypatch):
    import follow
    monkeypatch.delenv("RENDER_MEMO", raising=False)
    seen = []
    monkeypatch.setattr(follow, "detect", lambda g, cv2=None, cascades=None,
                        face_px=None, roi=None: seen.append(roi) or
                        [([0.1, 0.1, 0.2, 0.2], 0)])
    g = np.zeros((32, 18), np.uint8)
    assert follow.detect_memo(g, roi=[0, 0, 1, 1]) == \
        [([0.1, 0.1, 0.2, 0.2], 0)]
    assert follow.detect_memo(g, roi=[0, 0, 1, 1])
    assert len(seen) == 2


def test_detect_memo_hit_equals_detect_and_keys_on_frame_and_roi(
        memo, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    import follow
    calls = []

    def fake(gray, cv2=None, cascades=None, face_px=None, roi=None):
        calls.append((int(gray.sum()), roi))
        return [([0.1234, 0.2, 0.3, 0.4], -1), ([0.5, 0.5, 0.6, 0.7], 0)]
    monkeypatch.setattr(follow, "detect", fake)
    g = np.full((64, 36), 7, np.uint8)
    first = follow.detect_memo(g, cv2, None)
    again = follow.detect_memo(g.copy(), cv2, None)
    assert first == again == fake(g)
    assert all(type(f) is tuple for f in again)
    assert len(calls) == 2              # one memo miss + the fake(g) above
    follow.detect_memo(g, cv2, None, roi=[0.0, 0.0, 0.5, 0.5])
    g2 = g.copy()
    g2[0, 0] = 8
    follow.detect_memo(g2, cv2, None)
    assert len(calls) == 4


def test_real_haar_detection_is_identical_from_the_memo(memo):
    cv2 = pytest.importorskip("cv2")
    import follow
    import subject
    cascades = subject._cascades(cv2)
    if not cascades:
        pytest.skip("no cascades in this OpenCV")
    rng = np.random.default_rng(3)
    frames = [(rng.random((200, 112)) * 255).astype(np.uint8)
              for _ in range(3)]
    fresh = [follow.detect(f, cv2, cascades) for f in frames]
    cold = [follow.detect_memo(f, cv2, cascades) for f in frames]
    warm = [follow.detect_memo(f, cv2, cascades) for f in frames]
    assert fresh == cold == warm
    assert render_memo.stats()["hits"] >= 3


# ── render_qc.eye_counts ───────────────────────────────────────────────────

def test_eye_counts_answers_are_identical_from_the_memo(memo, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    import follow
    import render_qc
    frames = [np.full((40, 24), v, np.uint8) for v in (10, 20, 10)]
    monkeypatch.setattr(render_qc, "_stream", lambda cmd, w, h, d: iter(
        [f.copy() for f in frames]))
    detected = []

    def fake(g, cv2=None, cascades=None, face_px=None, roi=None):
        detected.append(int(g[0, 0]))
        if g[0, 0] == 20:
            return []
        return [([0.1, 0.1, 0.9, 0.9], 0)]
    monkeypatch.setattr(follow, "detect", fake)
    plan = {"W": 24, "H": 40, "fps": 30.0}
    monkeypatch.delenv("RENDER_MEMO")
    off = render_qc.eye_counts("x.mp4", plan, 1e18, cv2=cv2)
    monkeypatch.setenv("RENDER_MEMO", "1")
    cold = render_qc.eye_counts("x.mp4", plan, 1e18, cv2=cv2)
    n = len(detected)
    warm = render_qc.eye_counts("x.mp4", plan, 1e18, cv2=cv2)
    assert off == cold == warm
    assert all(type(r) is tuple for r in warm)
    assert len(detected) == n            # every frame of the warm pass hit
    assert warm[1] == (None, None) and warm[0][1] == [0.1, 0.1, 0.9, 0.9]


# ── keepout.detect_faces through the picture check ─────────────────────────

def test_source_frame_faces_are_remembered_by_file_bytes(
        memo, monkeypatch, tmp_path):
    pytest.importorskip("cv2")
    import keepout
    import render_qc
    calls = []

    def fake(path, width=480):
        calls.append(path)
        data = open(path, "rb").read()
        return None if data == b"unreadable" else [[0.1, 0.2, 0.3, 0.4]]
    monkeypatch.setattr(keepout, "detect_faces", fake)
    a, b, c = (tmp_path / n for n in ("a.jpg", "b.jpg", "c.jpg"))
    a.write_bytes(b"frame-one")
    b.write_bytes(b"frame-one")          # the same frame, another workdir
    c.write_bytes(b"unreadable")
    assert render_qc._detect_faces_memo(str(a)) == [[0.1, 0.2, 0.3, 0.4]]
    assert render_qc._detect_faces_memo(str(b)) == [[0.1, 0.2, 0.3, 0.4]]
    assert len(calls) == 1
    # None means "could not measure": never remembered
    assert render_qc._detect_faces_memo(str(c)) is None
    assert render_qc._detect_faces_memo(str(c)) is None
    assert len(calls) == 3


# ── motion_engine.probe ────────────────────────────────────────────────────

class _Job:
    def __init__(self, k):
        self._k = k

    def key(self):
        return self._k


def _probe_env(monkeypatch):
    import motion_engine
    monkeypatch.setattr(motion_engine, "available", lambda: True)
    runs = []

    def run(jobs, times_list, budget_s, measure):
        runs.append([j.key() for j in jobs])
        out = []
        for j, ts in zip(jobs, times_list):
            if j.key() == "broken":
                out.append({"errors": ["probe budget spent"],
                            "visible_frames": 0, "samples": len(ts),
                            "bboxes": [], "ink": [], "cover": []})
            else:
                out.append({"errors": [], "visible_frames": len(ts),
                            "samples": len(ts),
                            "bboxes": [[0.1, 0.2, 0.3, 0.4]],
                            "ink": [None] * len(ts),
                            "cover": [[0.1, 0.2, 0.3, 0.4]] * len(ts),
                            **({"measures": [{"box": None, "grid":
                                              np.zeros((2, 2), bool)}]
                                * len(ts)} if measure else {})})
        return out
    monkeypatch.setattr(motion_engine, "_probe_run", run)
    return motion_engine, runs


def test_probe_reports_are_remembered_per_composition(memo, monkeypatch):
    me, runs = _probe_env(monkeypatch)
    jobs = [_Job("a" * 40), _Job("b" * 40)]
    first = me.probe(jobs, [[0.5, 1.0], [0.25]])
    second = me.probe(jobs, [[0.5, 1.0], [0.25]])
    assert first == second
    assert runs == [["a" * 40, "b" * 40]]
    # another composition or other times are new questions; the rest hit
    me.probe([_Job("a" * 40), _Job("c" * 40)], [[0.5, 1.0], [0.25]])
    me.probe([_Job("a" * 40)], [[0.5]])
    assert runs[1:] == [["c" * 40], ["a" * 40]]


def test_probe_never_remembers_a_failed_or_cut_report(memo, monkeypatch):
    me, runs = _probe_env(monkeypatch)
    me.probe([_Job("broken")], [[0.5]])
    me.probe([_Job("broken")], [[0.5]])
    assert runs == [["broken"], ["broken"]]


def test_probe_with_a_measure_keys_on_the_measuring_code(memo, monkeypatch):
    import render_qc
    me, runs = _probe_env(monkeypatch)
    one = me.probe([_Job("a" * 40)], [[0.5]], measure=render_qc.ink_lines)
    two = me.probe([_Job("a" * 40)], [[0.5]], measure=render_qc.ink_lines)
    assert runs == [["a" * 40]]
    g1, g2 = one[0]["measures"][0]["grid"], two[0]["measures"][0]["grid"]
    assert g1.dtype == g2.dtype and (g1 == g2).all()
    # the plain probe of the same composition is another question
    me.probe([_Job("a" * 40)], [[0.5]])
    assert runs == [["a" * 40], ["a" * 40]]


def test_probe_is_unchanged_when_the_memo_is_off(monkeypatch):
    monkeypatch.delenv("RENDER_MEMO", raising=False)
    me, runs = _probe_env(monkeypatch)
    me.probe([_Job("a" * 40)], [[0.5]])
    me.probe([_Job("a" * 40)], [[0.5]])
    assert runs == [["a" * 40], ["a" * 40]]


# ── playwright's stack capture ─────────────────────────────────────────────

def test_fast_playwright_stack_is_what_the_client_parses():
    pytest.importorskip("playwright")
    import motion_engine
    from playwright._impl import _connection
    before = _connection.inspect       # an earlier render may have patched it
    real = inspect
    _connection.inspect = real
    motion_engine._PW_FAST["done"] = False
    try:
        motion_engine._fast_playwright_stacks()
        fast = _connection.inspect
        assert fast is not real and fast.FrameInfo is inspect.FrameInfo

        def capture():
            return fast.stack(), inspect.stack()
        mine, theirs = capture()
        assert [(f.frame, f.filename, f.lineno) for f in mine] == \
            [(f.frame, f.filename, f.lineno) for f in theirs]
        for internal in (False, True):
            assert _connection._extract_stack_trace_information_from_stack(
                mine, internal) == \
                _connection._extract_stack_trace_information_from_stack(
                    theirs, internal)
    finally:
        _connection.inspect = before
        motion_engine._PW_FAST["done"] = False


def test_fast_playwright_stack_can_be_turned_off(monkeypatch):
    pytest.importorskip("playwright")
    import motion_engine
    from playwright._impl import _connection
    before = _connection.inspect
    real = inspect
    _connection.inspect = real
    monkeypatch.setenv("MOTION_PW_FAST_STACK", "0")
    motion_engine._PW_FAST["done"] = False
    try:
        motion_engine._fast_playwright_stacks()
        assert _connection.inspect is real
    finally:
        _connection.inspect = before
        motion_engine._PW_FAST["done"] = False
