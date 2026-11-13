"""A trial account's first video gets its First cut, whatever its size and
however it arrives (13 Nov 2026).

  * user 3023 uploaded a 3.6 GB phone recording: free_trial's 2 GiB rule
    refused the admission, the upload met "Upgrade to start editing it" and no
    analysis ran. The First cut reads at most the first 30 minutes and the
    index streams a large original or adopts the browser's proxy: no size rule.
  * user 3025's first video was a Drive link: a link never asked the trial for
    admission, so it met the wall an uploaded file would not have.
  * the wall now says why the trial refused (the ops loop files it), and the
    balance tells the Studio when a never-paid account's next upload is
    admitted, so that upload prepares the browser's proxy.
  * user 3030's second upload stays walled: D2 admits one upload per account.
"""
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import free_trial  # noqa: E402
from routes import video as v  # noqa: E402
from test_free_trial import DB, _fresh, _on  # noqa: E402
from test_lane_p_first_film import LaneDB  # noqa: E402

PHONE_4K = 3_624_222_629          # asset 41226, user 3023


def test_a_first_upload_of_any_size_is_admitted():
    for nbytes in (PHONE_4K, 2 * 1024 ** 3 + 1, 40 * 1024 ** 3):
        _fresh()
        db = DB(stored=_on())
        got = free_trial.admit_upload(db, 3023, 5739, subscribed=False,
                                      nbytes=nbytes)
        assert got and got["kind"] == "trial" and got["first_cut"] is True
        assert db.updates == [got]
    assert "max_upload_bytes" not in free_trial.DEFAULTS


def test_the_refusal_says_why_and_d2_keeps_one_upload_per_account():
    _fresh()
    # user 3030: the second project of an account that has its admission
    assert free_trial.admit_upload_why(
        DB(stored=_on(), other_admission=True), 3030, 5747,
        subscribed=False) == (None, "other_project")
    _fresh()
    assert free_trial.admit_upload_why(DB(stored=None), 3023, 5739,
                                       subscribed=False) == (None, "off")
    _fresh()
    assert free_trial.admit_upload_why(
        DB(stored=_on(), today=40), 3023, 5739,
        subscribed=False) == (None, "daily_count_cap")
    _fresh()
    assert free_trial.admit_upload_why(
        DB(stored=_on(), device_others=2), 3023, 5739,
        subscribed=False) == (None, "device_limit")
    assert "other_project" not in free_trial.SWITCH_REFUSALS
    assert "off" in free_trial.SWITCH_REFUSALS


def test_the_studio_learns_a_never_paid_accounts_first_upload_is_admitted():
    _fresh()
    assert free_trial.first_cut_on_upload(LaneDB(stored=_on()), 3023,
                                          subscribed=False) is True
    _fresh()
    assert free_trial.first_cut_on_upload(LaneDB(stored=None), 3023,
                                          subscribed=False) is False
    _fresh()
    assert free_trial.first_cut_on_upload(
        LaneDB(stored=_on(), user={"email": "x@mailinator.com",
                               "is_verified": 1}),
        3023, subscribed=False) is False


class _Cur:
    def __init__(self):
        self.sql, self._one, self.jobs, self.messages = [], None, [], []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.sql.append(s)
        self._one = None
        if s.startswith("INSERT INTO assets"):
            self._one = {"id": 41206}
        elif s.startswith("INSERT INTO chat_messages"):
            self.messages.append(params)
            self._one = {"id": 77}

    def fetchone(self):
        return self._one


def _admission(result, seen):
    def admit(cur, user_id, project_id, subscribed, nbytes=None):
        seen.append((user_id, project_id, subscribed, nbytes))
        return result
    return admit


def test_the_wall_gives_way_to_the_trial_and_says_why_when_it_stays(
        monkeypatch):
    seen = []
    monkeypatch.setattr(v, "_subscribe_gate_applies", lambda c, u: True)
    monkeypatch.setattr(free_trial, "admit_upload_why", _admission(
        ({"kind": "trial", "first_cut": True}, None), seen))
    why = {}
    assert v._upload_wall(_Cur(), 3023, 5739, PHONE_4K, why=why) is False
    assert why == {} and seen == [(3023, 5739, False, PHONE_4K)]
    monkeypatch.setattr(free_trial, "admit_upload_why", _admission(
        (None, "other_project"), seen))
    assert v._upload_wall(_Cur(), 3030, 5747, why=why) is True
    assert why == {"trial_refusal": "other_project"}


def test_a_link_is_the_accounts_first_video_exactly_as_an_upload(monkeypatch):
    """User 3025: a Drive link as the first video is admitted and analysed."""
    seen, locks = [], []
    cur = _Cur()
    monkeypatch.setattr(v, "_subscribe_gate_applies", lambda c, u: True)
    monkeypatch.setattr(free_trial, "admit_upload_why", _admission(
        ({"kind": "trial", "first_cut": True}, None), seen))
    monkeypatch.setattr(v, "_enqueue", lambda c, p, u, t, payload:
                        cur.jobs.append((t, payload)) or 555)
    monkeypatch.setattr(v, "_record_upload_subscription_lock",
                        lambda c, session, asset: locks.append(asset) or 88)
    link = "https://drive.google.com/file/d/175g5/view?usp=drive_link"
    out = v._register_link_original(cur, {"chat_session_id": 40}, 5741, 3025,
                                    link, link)
    assert out == {"asset_id": 41206, "subscribe_gated": False,
                   "index_job_id": 555}
    assert cur.jobs == [("index", {"asset_id": 41206})] and locks == []
    assert seen == [(3025, 5741, False, None)]
    # a link the trial refuses meets today's wall, with the reason kept aside
    cur2 = _Cur()
    monkeypatch.setattr(free_trial, "admit_upload_why", _admission(
        (None, "device_limit"), seen))
    out = v._register_link_original(cur2, {"chat_session_id": 40}, 5741, 3025,
                                    link, link)
    assert out["subscribe_gated"] is True and out["index_job_id"] is None
    assert out["trial_refusal"] == "device_limit" and locks == [41206]
