"""WAVE_A (31 Oct 2026): the Opus review of the Sonnet brute-force commits
(stutter cuts across a sentence mark, fractional subject boxes) and "make
the title bigger changes nothing" (hook_title base_size growth, the honest
type-size note, the reply guard)."""
import first_cut
import hook_fit
import motion_engine
import motion_templates
import motion_tools
import pytest
import short_clip
import trial_reply


def _words(spec, step=0.4):
    out, t = [], 10.0
    for w in spec.split():
        out.append({"w": w, "t0": round(t, 2), "t1": round(t + step - 0.05, 2)})
        t += step
    return out


# ---- 91d7819d: a stutter is never a sentence boundary or grammar ----------

def test_a_repeat_across_a_sentence_mark_is_speech_not_a_stutter():
    for spec in ("I love it. It works", "thank you. You did it", "come in. In fact",
                 "you, you were right", "it is. Is it"):
        assert first_cut.stutter_spans(_words(spec), [[9.0, 30.0]]) == [], spec
    # words that cannot end a clause are still cut across a mark
    for spec in ("go to the. The store", "work and, and play", "I, I think"):
        assert len(first_cut.stutter_spans(_words(spec), [[9.0, 30.0]])) == 1, spec


def test_a_double_copula_after_what_is_grammar():
    assert first_cut.stutter_spans(_words("what it is is a tool"), [[9.0, 30.0]]) == []
    assert first_cut.stutter_spans(_words("all it was was luck"), [[9.0, 30.0]]) == []
    assert len(first_cut.stutter_spans(_words("the thing is is deep"), [[9.0, 30.0]])) == 1


# ---- dd4b4105: a fractional box is not a 1-pixel speck in the corner -------

def test_subject_boxes_in_fractions_are_read_as_fractions():
    got = short_clip.parse_boxes(
        '{"boxes": [{"n": 1, "box": [0.3, 0.2, 0.6, 0.9]}, '
        '{"n": 2, "box": [150, 180, 310, 820]}, {"n": 3, "box": [10, 10, 12, 12]}]}',
        {1: (512, 910), 2: (512, 910), 3: (512, 910)})
    assert got[1] == (0.3, 0.2, 0.6, 0.9)
    assert abs(got[2][0] - 150 / 512) < 1e-6
    assert 3 not in got                     # a 2-pixel box points at nothing


# ---- hook_title: a bigger size grows the type ------------------------------

def _p(text, size, y=0.62, **kw):
    p = {"text": text, "font": "Anton", "uppercase": True, "treatment": "serif",
         "size": size, "y": y}
    p.update(kw)
    return motion_templates.check_params("hook_title", p)


def test_a_title_without_base_size_lays_out_as_always():
    est = hook_fit.estimate(_p("An intelligence will *solve* everything", 1.3), 1080, 1920)
    same = hook_fit.estimate(_p("An intelligence will *solve* everything", 1.6), 1080, 1920)
    assert est["type_px"] == est["base_px"] == same["type_px"]   # width-limited: no change


def test_a_bigger_size_re_breaks_into_more_larger_lines_inside_the_band():
    base = hook_fit.estimate(_p("Stop doing / *this*", 1.6), 1080, 1920)
    big = hook_fit.estimate(_p("Stop doing / *this*", 1.84, base_size=1.6,
                               band_top=0.45, band_bottom=0.86), 1080, 1920)
    assert big["type_px"] >= base["type_px"] * 1.1 and big["lines"] == 3
    small = hook_fit.estimate(_p("Stop doing / *this*", 1.36, base_size=1.6), 1080, 1920)
    assert small["type_px"] < base["type_px"] * 0.9
    # no room under the face for a fourth line: nothing changes, honestly
    full = hook_fit.estimate(_p("New to *Python?* Start here", 1.5, y=0.66, base_size=1.3,
                                band_top=0.5, band_bottom=0.86), 1080, 1920)
    assert full["type_px"] == full["base_px"]


def test_an_accent_span_is_never_split_across_lines():
    toks = hook_fit._tokens(["the *very best* idea"])
    for ls in hook_fit._splits(toks, 3):
        assert not any(line.count("*") % 2 for line in ls), ls


@pytest.mark.skipif(not motion_engine.available(), reason="no Chromium here")
def test_the_template_draws_a_bigger_title_and_reports_it():
    def info(params):
        item = {"id": "mg1", "template": "hook_title", "start": 0.0, "end": 3.0,
                "params": params}
        rep = motion_tools._probe_item(item, 1080, 1920)
        assert rep and not rep["errors"], rep
        return rep["info"]
    a = info(_p("ROCKET *RETURNS*", 1.6))
    b = info(_p("ROCKET *RETURNS*", 1.84, base_size=1.6, band_top=0.47, band_bottom=0.86))
    assert a["type_px"] == a["base_px"] and a["lines"] == 1
    assert b["type_px"] >= a["type_px"] * 1.1 and b["lines"] == 2 and b["base_px"] == a["type_px"]


# ---- the reply says only what the picture did ------------------------------

def _facts(scale):
    before = {"motion": [{"id": "mg1", "template": "hook_title", "params": {"size": 1.3}}]}
    after = {"motion": [{"id": "mg1", "template": "hook_title",
                         "params": {"size": 1.5, "base_size": 1.3}}]}
    return trial_reply.Facts(before=before, after=after, wrote=True, type_scale=scale)


def test_bigger_is_never_claimed_when_the_type_drew_the_same():
    s = "I made the opening title bigger."
    assert trial_reply.unbacked(s, _facts({"mg1": 1.0})) == "unchanged"
    assert trial_reply.unbacked(s, _facts({"mg1": 1.12})) is None
    assert trial_reply.unbacked(s, _facts({})) is None
