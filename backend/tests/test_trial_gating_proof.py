"""GATING PROOF, backend half (T1, 30 Oct 2026): with EVERY trial flag on,
an existing subscriber's upload, Studio and First-cut behaviour is today's.

Owner rule (DECISIONS.md, D1): existing subscribers keep exactly their
current lanes and behaviour. The worker half (lanes, models, effort) is
worker/tests/test_trial_gating_proof.py.

Found by this proof: with free_result on, ``payer_first_cut`` admitted an
EXISTING subscriber's next upload and started an automatic First cut on it.
free_trial.admit_upload now admits a subscriber only when claude_plans says
the subscription began after the release cutoff.
"""
import json
from datetime import datetime, timedelta, timezone

import free_trial
import upload_proxy
from test_free_trial import DB, _fresh, _on

CUTOFF = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
STUDIO_ON = json.dumps({"on": True, "cutoff_at": CUTOFF, "claude_lane": True})
EVERY_FLAG = dict(trial_requests=True, payer_first_cut=True,
                  model="claude-haiku-5-5", per_account_usd_cap=0.12,
                  test_user_ids=[29, 46, 61, 1058])


def _existing():
    """A subscriber who paid before the cutoff, every trial flag on."""
    return DB(stored=_on(**EVERY_FLAG), studio=STUDIO_ON, paid_before=True,
              credit_state=json.dumps({"state": "on"}))


def test_an_existing_subscribers_upload_gets_no_first_cut():
    _fresh()
    db = _existing()
    assert free_trial.live(db, 501) is True                # the trial is ON
    assert free_trial.admit_upload(db, 900, 501, subscribed=True) is None
    assert db.updates == []                                # project untouched
    # So the Studio state carries no free_trial key for it (today's payload)
    assert free_trial.status_for_studio(db, 501, 900, True) is None


def test_with_payer_first_cut_on_only_a_new_subscriber_gets_it():
    _fresh()
    new = DB(stored=_on(**EVERY_FLAG), studio=STUDIO_ON, paid_before=False,
             credit_state=json.dumps({"state": "on"}))
    got = free_trial.admit_upload(new, 901, 777, subscribed=True)
    assert got and got["kind"] == "payer" and got["first_cut"] is True


def test_an_existing_subscriber_never_gets_a_server_proxy_job():
    """Their upload is always analysed, which builds the proxy: the proxy
    job is for walled uploads only."""
    class Cur:
        def execute(self, sql, params=None):
            raise AssertionError("no read is needed to say no")

        def fetchone(self):
            return None
    assert upload_proxy.decide(Cur(), 501, 900, 41, {"media_error": 4},
                               gated=False) == "not_gated"


def test_a_subscribers_request_is_never_a_trial_request():
    _fresh()
    db = _existing()
    # No admission on the project: the trial lane is not theirs.
    assert free_trial.trial_project(db, 501, 900) is None
    assert free_trial.request_allowance(db, 501, 900)["allowed"] is False
