"""A walled upload the browser cannot decode gets a box-built proxy.

B_RELIABILITY.md (29 Oct 2026): 8 free accounts' HEVC / 4K uploads could not
play before subscribing. upload_proxy.decide queues ONE proxy-only index job
(stamped for the render box) only when nothing else will make a proxy.
"""
import json
from pathlib import Path

import upload_proxy

ROOT = Path(__file__).resolve().parents[1]


class Cur:
    """Answers by the first matching SQL fragment."""

    def __init__(self, answers):
        self.answers = answers
        self.sql = []
        self._next = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        for frag, value in self.answers:
            if frag in flat:
                self._next = value
                return
        self._next = None

    def fetchone(self):
        return self._next


BOX = json.dumps({"enabled": True, "stages": {"owner": True}})


def _cur(**over):
    answers = {
        "key = %s": None,
        "FROM assets WHERE project_id = %s AND kind = 'original'": {
            "id": 41, "kind": "original", "bytes": 300_000_000,
            "sha256": None, "meta": {"filename": "IMG_0001.MOV"}},
        "AS analyses": {"analyses": 0, "proxies": 0, "proxies_live": 0},
        "AS n FROM video_jobs": {"n": 0},
    }
    answers.update(over)
    kv = answers.pop("key = %s") or {}

    class KvCur(Cur):
        def execute(self, sql, params=None):
            if "FROM app_kv" in sql:
                self.sql.append(" ".join(sql.split()))
                key = (params or ("",))[0]
                self._next = ({"value": kv[key]} if kv.get(key) is not None
                              else None)
                return
            return Cur.execute(self, sql, params)
    return KvCur(list(answers.items()))


ERR4 = {"media_error": 4, "is_proxy": True}


def _kv(**rows):
    return {"key = %s": rows}


def test_an_undecodable_walled_upload_is_queued_on_the_box():
    cur = _cur(**_kv(box_routing=BOX))
    assert upload_proxy.decide(cur, 3, 7, 41, ERR4, gated=True) is None
    queued = []
    job, why = upload_proxy.maybe_enqueue(
        cur, 3, 7, 41, ERR4, True,
        lambda c, pid, uid, kind, payload: queued.append(
            (pid, uid, kind, payload)) or 99)
    assert (job, why) == (99, None)
    assert queued == [(7, 3, "index", {"asset_id": 41, "proxy_only": True,
                                       "execution_provider": "box",
                                       "source": "player_error"})]


def test_only_a_decode_failure_of_a_walled_original_counts():
    box = _kv(box_routing=BOX)
    assert upload_proxy.decide(_cur(**box), 3, 7, 41,
                               {"media_error": 2}, True) == "not_undecodable"
    # A subscriber (or a live trial project) always has an analysis, which
    # builds the proxy: never a second encode.
    assert upload_proxy.decide(_cur(**box), 3, 7, 41, ERR4,
                               False) == "not_gated"
    assert upload_proxy.decide(_cur(**box), 3, 7, 40, ERR4,
                               True) == "not_active_original"


def test_fixed_cost_only_never_without_the_box():
    assert upload_proxy.decide(_cur(), 3, 7, 41, ERR4, True) == \
        "box_not_routed"
    off = json.dumps({"enabled": False, "stages": {"owner": True}})
    assert upload_proxy.decide(_cur(**_kv(box_routing=off)), 3, 7, 41, ERR4,
                               True) == "box_not_routed"
    killed = _kv(box_routing=BOX, upload_proxy=json.dumps({"on": False}))
    assert upload_proxy.decide(_cur(**killed), 3, 7, 41, ERR4,
                               True) == "switched_off"


def test_one_proxy_per_upload_and_a_global_bound():
    box = _kv(box_routing=BOX)
    live = dict(box, **{"AS analyses": {"analyses": 0, "proxies": 1,
                                        "proxies_live": 1}})
    assert upload_proxy.decide(_cur(**live), 3, 7, 41, ERR4,
                               True) == "already_queued"
    used = dict(box, **{"AS analyses": {"analyses": 0, "proxies": 2,
                                        "proxies_live": 0}})
    assert upload_proxy.decide(_cur(**used), 3, 7, 41, ERR4,
                               True) == "attempts_used"
    analysed = dict(box, **{"AS analyses": {"analyses": 1, "proxies": 0,
                                            "proxies_live": 0}})
    assert upload_proxy.decide(_cur(**analysed), 3, 7, 41, ERR4,
                               True) == "analysis_makes_proxy"
    busy = dict(box, **{"AS n FROM video_jobs": {"n": 4}})
    assert upload_proxy.decide(_cur(**busy), 3, 7, 41, ERR4, True) == "busy"
    huge = dict(box, **{
        "FROM assets WHERE project_id = %s AND kind = 'original'": {
            "id": 41, "bytes": 11 * 1024 ** 3, "sha256": None, "meta": {}}})
    assert upload_proxy.decide(_cur(**huge), 3, 7, 41, ERR4, True) == "size"
    pending = dict(box, **{
        "FROM assets WHERE project_id = %s AND kind = 'original'": {
            "id": 41, "bytes": 10, "sha256": None,
            "meta": {"upload_state": "pending"}}})
    assert upload_proxy.decide(_cur(**pending), 3, 7, 41, ERR4,
                               True) == "source_not_ready"


def test_an_audio_only_original_is_never_proxied():
    """SWEEP_UPLOAD #43 (30 Oct 2026): an audio-only .mp4 uploaded as the
    video has no picture to proxy (the browser plays it as it is). Once the
    worker's proxy-only probe has marked it, no job is queued again, so no
    failed box job is ever filed for it."""
    box = _kv(box_routing=BOX)
    for meta in ({"no_video": True}, json.dumps({"no_video": True})):
        audio = dict(box, **{
            "FROM assets WHERE project_id = %s AND kind = 'original'": {
                "id": 41, "kind": "original", "bytes": 115_000_000,
                "sha256": None, "meta": meta}})
        assert upload_proxy.decide(_cur(**audio), 3, 7, 41, ERR4,
                                   True) == "audio_only"
        queued = []
        job, why = upload_proxy.maybe_enqueue(
            _cur(**audio), 3, 7, 41, ERR4, True,
            lambda *a: queued.append(a) or 99)
        assert (job, why, queued) == (None, "audio_only", [])
    # A video original is unaffected (sensitivity).
    assert upload_proxy.decide(_cur(**box), 3, 7, 41, ERR4, True) is None


def test_a_proxy_job_is_never_mistaken_for_the_analysis():
    """After checkout the walled upload's analysis must start (the state
    poll's "subscription unlocked; analysis not started" heal), the Studio's
    "Analyzing" bar must not show for a proxy job, and the concierge must not
    say "still analysing". Every analysis lookup excludes proxy-only jobs."""
    frag = "COALESCE(payload->>'proxy_only', '') <> 'true'"
    state = (ROOT / "video_services" / "project_state.py").read_text()
    assert frag in state.split("def index_job_state", 1)[1].split("def ", 1)[0]
    upload = (ROOT / "video_services" / "upload.py").read_text()
    assert frag in upload
    video = (ROOT / "routes" / "video.py").read_text()
    for chunk in video.split("type = 'index'")[1:]:
        head = chunk[:160]
        assert "proxy_only" in head, head
    assert "THEN 'upload_proxy' ELSE type END AS jtype" in video
    mcp = (ROOT / "routes" / "mcp.py").read_text()
    for chunk in mcp.split("type = 'index'")[1:]:
        assert "proxy_only" in chunk[:160], chunk[:160]


def test_the_client_event_route_hands_a_decode_failure_to_upload_proxy():
    video = (ROOT / "routes" / "video.py").read_text()
    route = video.split("def client_event(user_id, project_id):", 1)[1]
    route = route.split("@video_bp.route", 1)[0]
    assert 'kind == "player_error"' in route
    assert "_server_proxy_for_player_error" in route
    helper = video.split("def _server_proxy_for_player_error", 1)[1]
    helper = helper.split("\n@video_bp.route", 1)[0]
    assert "_subscribe_gate_applies(cur, user_id)" in helper
    assert "not _trial_admission(cur, user_id, project_id)" in helper
    assert "FOR UPDATE" in helper
