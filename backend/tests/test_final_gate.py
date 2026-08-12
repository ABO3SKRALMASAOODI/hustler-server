"""Whether a rendered final is downloadable — and why it can never deadlock.

The gate in backend/routes/video.py does not decide truth. It issues a REQUEST:
hide a final so the studio posts /render/final and the worker's render cache is
finally asked to bust. That request is only safe while the two services agree on
what "current" means. When they disagree there is no exit — the gate hides the
file, the studio asks, the worker answers `cached: true` with the same asset, the
gate hides it again — and Download becomes a button that spins forever and
produces nothing.

That is not a hypothetical. It shipped twice. The second time it took out every
export on the platform: the backend deployed round 48, which demanded a `trans_v`
stamp on EVERY final, while the render path (the remote executor image) still
predated round 48 and stamped none. 41 of 41 finals rendered in the following
week were reported as stale. One customer pressed Download seventeen times.

So these tests pin the two properties that make the failure impossible rather
than merely fixed:

  1. the transition rule is the SAME rule the worker runs — busting only EDLs
     that actually carry a transition, never every final on the platform;
  2. an asset the worker has already declined to re-render is served, whatever
     the stamps say.

    cd backend && python -m pytest tests -q
"""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                   # noqa: E402


# The stamps a render made by TODAY's pipeline carries.
CURRENT = {"outro_v": video.OUTRO_VERSION,
           "trans_v": video.TRANSITION_VERSION,
           "wm_v": 0}
# What the stale executor image actually wrote: no trans_v at all.
STALE_STAMP = {"outro_v": video.OUTRO_VERSION, "wm_v": 0}


class _Cur:
    """Canned answers for the three queries _final_gate issues, matched on a
    distinctive fragment of each so the test breaks loudly if a query is
    rewritten into something that no longer asks the same question."""

    def __init__(self, *, transition_versions=(), confirmed=(), paid=False,
                 pipeline_emits=True):
        self._tr = list(transition_versions)
        self._conf = list(confirmed)
        self._paid = paid
        self._emits = pipeline_emits
        self._rows = []
        self._one = None

    def execute(self, sql, params=None):
        if "bool_or" in sql:                  # the pipeline probe
            self._one = {"emits": self._emits}
        elif "'transition'" in sql:
            self._rows = [{"version": v, "has_transition": True}
                          for v in self._tr]
        elif "render_asset_id" in sql:
            self._rows = [{"aid": a} for a in self._conf]
        elif "FROM users" in sql:
            self._one = {"is_subscribed": 1 if self._paid else 0,
                         "plan": "ai" if self._paid else "free"}
        elif "to_regclass" in sql:
            self._one = {"t": None}          # no video_settings table: defaults
        else:                                 # pragma: no cover - unexpected
            raise AssertionError(f"unexpected query: {sql[:120]}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


def _gate(paid=True, **kw):
    """Paid by default so the watermark stamp is satisfied (wm_v 0 is correct
    for a subscriber) and each test isolates the one rule it is about. The
    watermark tests below say `paid=` explicitly.

    The probe cache is process-global with a 60s TTL, so it MUST be cleared per
    gate or the first test to run would decide the answer for all the others.
    """
    video._pipeline_probe.clear()
    return video._final_gate(_Cur(paid=paid, **kw), project_id=1, user_id=7)


# ── the rule has to be the worker's rule ────────────────────────────────────

def test_final_with_no_transition_in_its_edl_is_downloadable_unstamped():
    """The bug that took Download down for everyone.

    worker/renderer.transitions_current busts ONLY an EDL carrying a
    transition. This side demanded the stamp unconditionally, so the instant
    renders stopped carrying it not one export in the product was current.
    """
    ok = _gate(transition_versions=())        # this EDL has no transition
    assert ok(asset_id=100, meta=STALE_STAMP, version=3)


def test_final_whose_edl_has_a_transition_still_busts_when_unstamped():
    """The rule it must not lose while fixing that: a render made before scene
    scoping puts a junction effect on every cut — 45 whip pans through one
    continuous shot — and must be re-encoded, not handed over."""
    ok = _gate(transition_versions=(3,))
    assert not ok(asset_id=100, meta=STALE_STAMP, version=3)


def test_a_correctly_stamped_final_is_downloadable_either_way():
    for versions in ((), (3,)):
        ok = _gate(transition_versions=versions)
        assert ok(asset_id=100, meta=CURRENT, version=3)


def test_the_transition_rule_is_per_version_not_per_project():
    """A project holds many EDL versions; only the ones carrying a transition
    may be busted. Blanketing the project would re-break the general case."""
    ok = _gate(transition_versions=(6,))
    assert ok(asset_id=100, meta=STALE_STAMP, version=5)
    assert not ok(asset_id=101, meta=STALE_STAMP, version=6)


# ── the loop-breaker ────────────────────────────────────────────────────────

def test_an_asset_the_worker_declined_to_rerender_is_served():
    """`cached: true` is the worker saying, with full sight of the EDL and its
    own pipeline stamps, that this file is current. Asking again cannot produce
    anything else, so the backend's opinion stops being actionable."""
    ok = _gate(transition_versions=(3,), confirmed=(100,))
    assert ok(asset_id=100, meta=STALE_STAMP, version=3)


def test_the_loop_breaker_is_scoped_to_the_confirmed_asset():
    """It frees the specific file the worker answered for — not every stale
    render in the project, which would disable the gates wholesale."""
    ok = _gate(transition_versions=(3,), confirmed=(100,))
    assert ok(asset_id=100, meta=STALE_STAMP, version=3)
    assert not ok(asset_id=101, meta=STALE_STAMP, version=3)


def test_a_genuinely_stale_render_still_re_encodes_exactly_once():
    """Keyed on `cached`, not on "some job produced this asset" — otherwise the
    first render would satisfy the gate and the stamps would never bust
    anything. Only the SECOND identical answer is a loop."""
    ok = _gate(transition_versions=(3,), confirmed=())
    assert not ok(asset_id=100, meta=STALE_STAMP, version=3)


def test_the_loop_breaker_covers_every_stamp_not_just_transitions():
    """The deadlock is a class, not an incident: it shipped for the watermark
    before it shipped for transitions. Whatever the disagreement, the user's
    file wins once the worker has refused to rebuild it."""
    ok = _gate(confirmed=(100,))
    assert ok(asset_id=100, meta={"outro_v": 99, "wm_v": 5}, version=3)


# ── a gate may not demand what the pipeline cannot write ────────────────────

def test_a_stamp_the_pipeline_never_writes_does_not_hide_anything():
    """The wasted press, measured.

    With the executor frozen a round behind, a user pressed Download, the
    pipeline built a brand new final at his request, and the gate hid it one
    second later for missing a stamp that build does not write. The only way
    out was a second press that proved, via `cached: true`, what was already
    knowable: nothing newer can be made.
    """
    ok = _gate(transition_versions=(3,), pipeline_emits=False)
    assert ok(asset_id=100, meta=STALE_STAMP, version=3)


def test_the_gate_re_arms_by_itself_once_the_pipeline_stamps_again():
    """No flag to remember to unset: redeploy the executor, renders carry the
    stamp, and the check resumes busting the old ones exactly once."""
    ok = _gate(transition_versions=(3,), pipeline_emits=True)
    assert not ok(asset_id=100, meta=STALE_STAMP, version=3)


def test_the_probe_never_disables_the_other_stamps():
    """Scoped to trans_v on purpose. The watermark gate is revenue, not craft —
    a free user's unmarked export must still re-encode, and the loop-breaker
    already stops that gate from spinning forever."""
    ok = _gate(paid=False, pipeline_emits=False)
    assert not ok(asset_id=100, meta=STALE_STAMP, version=3)      # wm_v 0/wants 2
    ok = _gate(pipeline_emits=False)
    assert not ok(asset_id=100,
                  meta={"outro_v": video.OUTRO_VERSION - 1, "wm_v": 0},
                  version=3)                                       # old end card


def test_a_first_install_with_no_renders_keeps_its_gates_armed():
    """bool_or over an empty sample is NULL, which is "no evidence", not
    "the pipeline is broken"."""
    cur = _Cur(paid=True)
    cur.execute("SELECT bool_or(meta ? %s) ...")
    cur._one = {"emits": None}
    video._pipeline_probe.clear()
    assert video._pipeline_emits(cur, "trans_v") is True


def test_the_probe_result_is_cached_not_queried_per_asset():
    """/state is polled every 2s per open studio; this must not become a query
    per render row."""
    video._pipeline_probe.clear()
    cur = _Cur(pipeline_emits=False)
    calls = []
    inner = cur.execute
    cur.execute = lambda sql, params=None: (calls.append(sql), inner(sql, params))[1]
    assert video._pipeline_emits(cur, "trans_v") is False
    assert video._pipeline_emits(cur, "trans_v") is False
    assert len(calls) == 1


# ── the other two stamps still bite ─────────────────────────────────────────

def test_a_stale_end_card_still_re_exports():
    ok = _gate()
    assert not ok(asset_id=100,
                  meta={"outro_v": video.OUTRO_VERSION - 1,
                        "trans_v": video.TRANSITION_VERSION, "wm_v": 0},
                  version=3)


def test_a_card_less_render_is_not_treated_as_stale():
    """outro_v == 0 means the worker rendered deliberately without a card.
    There is no newer card for it to be missing."""
    ok = _gate()
    assert ok(asset_id=100,
              meta={"outro_v": 0, "trans_v": video.TRANSITION_VERSION,
                    "wm_v": 0}, version=3)


def test_a_free_users_unmarked_export_still_re_exports():
    ok = _gate(paid=False)
    assert not ok(asset_id=100, meta=CURRENT, version=3)     # wm_v 0, wants 2


def test_a_paid_users_marked_export_re_exports_after_upgrade():
    """They paid to remove the watermark; the cached marked file is not what
    they bought."""
    ok = _gate(paid=True)
    assert not ok(asset_id=100,
                  meta={"outro_v": video.OUTRO_VERSION,
                        "trans_v": video.TRANSITION_VERSION,
                        "wm_v": video.WATERMARK_VERSION},
                  version=3)


def test_a_watermark_position_change_re_exports_the_marked_final():
    meta = {"wm_v": video.WATERMARK_VERSION, "wm_p": "frame"}
    scene = {"enabled": True, "force": False, "scene_top": True}
    assert not video._watermark_is_current(meta, is_paid=False,
                                           settings=scene)
    assert video._watermark_is_current(
        {"wm_v": video.WATERMARK_VERSION, "wm_p": "scene"},
        is_paid=False, settings=scene)


def test_watermark_position_never_reencodes_a_clean_paid_final():
    scene = {"enabled": True, "force": False, "scene_top": True}
    assert video._watermark_is_current({"wm_v": 0, "wm_p": "frame"},
                                       is_paid=True, settings=scene)


# ── the two halves must not drift again ─────────────────────────────────────

def test_the_backend_and_worker_transition_rules_are_the_same_rule():
    """Both must grandfather an EDL with no transition, and both must bust one
    that has it. This is the assertion whose absence cost the platform a day of
    exports; the worker's own suite asserts the version CONSTANTS match, which
    was never enough — the constants agreed the whole time, the RULES did not.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    src = open(os.path.join(root, "worker", "renderer.py"),
               encoding="utf-8").read()
    fn = src.split("def transitions_current(", 1)[1].split("\ndef ", 1)[0]
    # the worker returns True early for an EDL with no transition
    assert "return True" in fn
    assert "get('transition')" in fn or 'get("transition")' in fn

    for has_transition, expected in ((False, True), (True, False)):
        ok = _gate(transition_versions=(3,) if has_transition else ())
        assert ok(asset_id=1, meta=STALE_STAMP, version=3) is expected


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
