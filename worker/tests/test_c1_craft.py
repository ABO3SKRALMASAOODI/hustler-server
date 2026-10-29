"""C1 (29 Oct 2026): the in-app agent's craft defaults and the verifier false
positives that sent it into repair loops (REFERENCE_PLAYBOOK.md, BAKEOFF.md)."""
from types import SimpleNamespace as NS

import agent_tools
import render_qc
import webrecord


# ---- render_qc: changes the footage itself carries are not framing pops ----

def _plan(others=(), src_of=None):
    return {"fps": 30.0, "others": list(others), "src_wh": [1920, 1080],
            "src_of": src_of or (lambda t: t + 10.0)}


def test_a_jump_inside_an_insert_is_that_clip_s_own():
    owned = render_qc.SourceChanges(_plan(others=[(2.0, 6.0)]), None, 1e18)
    assert owned.owned([4 * 30])                 # mid-insert: the capture's page change
    assert not owned.owned([int(2.02 * 30)])     # on its edge: the edit's join


def test_a_jump_the_source_also_makes_is_dropped(monkeypatch):
    owned = render_qc.SourceChanges(_plan(), "/x.mp4", 1e18)
    seen = []

    def at(src_t):
        seen.append(src_t)
        return src_t < 12.0                       # the source whips at 11 s
    monkeypatch.setattr(owned, "_source_jumps_at", at)
    mine, theirs = owned.split([["jump", 30, None], ["jump", 90, None]],
                               lambda r: [r[1]])
    assert theirs == [["jump", 30, None]] and mine == [["jump", 90, None]]
    assert seen == [11.0, 13.0]


def test_a_jump_with_no_source_frame_stays_the_edit_s():
    owned = render_qc.SourceChanges(_plan(src_of=lambda t: None), None, 1e18)
    assert not owned.owned([30])


def test_the_footage_stall_names_one_call_fix():
    plan_ = {"program_s": 20.0, "tail": {}}
    code, words = render_qc._stall_cause("held", 19.0, 19.6, plan_)
    assert code == "footage"
    assert "add_zoom(start=18.00, end=20.00, mode='push_in'" in words
    assert "Do not trim" in words


# ---- agent_tools: a finding that survives three renders is advisory ------

def test_a_persisting_picture_finding_turns_advisory():
    ctx = NS()
    f1 = ["JUMP OFF A CUT at 4.20s (frame 126): the whole picture changes"]
    f2 = ["JUMP OFF A CUT at 4.23s (frame 127): the whole picture changes"]
    assert agent_tools._persisting_picture_findings(ctx, f1) == (f1, [])
    assert agent_tools._persisting_picture_findings(ctx, f2) == (f2, [])
    keep, persisted = agent_tools._persisting_picture_findings(ctx, f1)
    assert keep == [] and persisted == f1
    other = ["CLIPPED FACE at 9.00s"]
    assert agent_tools._persisting_picture_findings(ctx, other) == (other, [])


def test_a_talking_head_is_cropped_not_fitted():
    big = [[0.40, 0.20, 0.55, 0.50]] * 5
    tiny = [[0.85, 0.80, 0.90, 0.88]] * 5         # a face-cam in a screen recording
    assert agent_tools._talking_head(big, 0.9)
    assert not agent_tools._talking_head(tiny, 0.9)
    assert not agent_tools._talking_head(big, 0.2)
    assert not agent_tools._talking_head([], 1.0)


# ---- webrecord: the automatic tour -------------------------------------

def test_the_tour_step_is_a_step():
    steps = webrecord._norm_steps([{"do": "wait", "seconds": 0.8},
                                   {"do": "tour", "by": 2}])
    assert steps[1] == {"do": "tour", "by": 2.0}


def test_the_tour_prefers_pricing_and_points_at_the_main_cta():
    s = webrecord._Session.__new__(webrecord._Session)
    items = [{"t": "Blog", "y": 0.05, "area": .001},
             {"t": "Features", "y": 0.05, "area": .001},
             {"t": "Pricing", "y": 0.05, "area": .001},
             {"t": "Pricing", "y": 0.7, "area": .001},       # footer duplicate
             {"t": "Get started", "y": 0.4, "area": .01, "button": True},
             {"t": "Sign up", "y": 0.05, "area": .002},
             {"t": "Try", "y": 0.98, "area": .02}]          # off the bottom edge
    assert [i["t"] for i in s._sections(items)] == ["Pricing", "Features"]
    assert s._cta(items)["t"] == "Get started"
    assert s._section_rank("Pricing & plans") == 0
    assert s._section_rank("Read our pricing philosophy and the long history") is None


def test_the_tour_never_shows_a_sign_in_or_another_site():
    assert webrecord._off_tour("https://stripe.com/", "https://dashboard.stripe.com/login")
    assert webrecord._off_tour("https://acme.io/", "https://acme.io/signin")
    assert webrecord._off_tour("https://acme.io/", "https://other.com/pricing")
    assert not webrecord._off_tour("https://www.acme.io/", "https://acme.io/pricing")
    assert not webrecord._off_tour("https://acme.io/", "https://acme.io/features#auth-free")
