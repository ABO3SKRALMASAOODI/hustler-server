"""Track 0D acceptance on local inputs (skipped in CI).

AT-0D-2  refsig detectors vs hand labels on references 20, 19, 15, 24, 22
         (plus the held-out reel 08 for the cut detector)
AT-0D-3  label coverage for every VFL window a Wave 1-2 test uses
VFL      every fixture file matches its pinned sha256

References are internal comparison inputs; fixtures and labels live in the
fixture store, never in git.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import pytest                                                 # noqa: E402

from localfix import fixture, local_only, reference           # noqa: E402

from benchmarks.refbench import at0d2                         # noqa: E402
from benchmarks.refbench import vfl as vflmod                 # noqa: E402

LABELS = fixture("labels", "refs")


@pytest.mark.parametrize("ref", ["20", "19", "15", "24"])
def test_at0d2_detectors_meet_the_bar(ref):
    lab = os.path.join(LABELS, ref, at0d2.LABELS[ref])
    if not (os.path.exists(lab) and os.path.exists(reference(f"{ref}_"))):
        pytest.skip("local-only: needs the reference and its hand labels")
    res = at0d2.evaluate(ref)
    assert res["precision"] >= 0.9, res
    assert res["recall"] >= 0.9, res


def test_at0d2_occluded_text_meets_the_bar():
    import refsig
    lab = os.path.join(LABELS, "22", at0d2.LABELS["22"])
    if not (os.path.exists(lab) and os.path.exists(reference("22_"))):
        pytest.skip("local-only: needs reference 22 and its hand labels")
    if not refsig.ocr_status()[0] or not refsig.rvm_model_path():
        pytest.skip("local-only: needs RapidOCR (dev requirements) and the "
                    "RVM model")
    res = at0d2.evaluate("22")
    assert res["precision"] >= 0.9, res
    assert res["recall"] >= 0.9, res


def test_cut_detector_holds_on_the_held_out_reel():
    """Reference 08's shots were labelled after the first blind AT-0D-2 run
    and are never scored by AT-0D-2: every cut-detector change is checked
    here, so a gain on 15 that costs 08 is caught."""
    import json
    import refsig
    lab_p = os.path.join(LABELS, "08", "shots_heldout.json")
    if not (os.path.exists(lab_p) and os.path.exists(reference("08_"))):
        pytest.skip("local-only: needs reference 08 and its held-out labels")
    with open(lab_p) as fh:
        lab = json.load(fh)
    F = refsig.Feats(reference("08_"))
    neg = refsig.negatives(F)
    wf = refsig.white_flashes(F, neg=neg)
    cf = refsig.colour_flashes(F, neg=neg)
    cuts = refsig.cuts(F, punct=set(neg) | set(wf) | set(cf))
    r = at0d2.boundary_pr(cuts, lab["cuts"], lab["transitions"],
                          lab["dont_care"], lab["scope"])
    assert r["precision"] >= 0.9 and r["recall"] >= 0.9, r


@local_only(fixture("V02", "thiel_flying_cars.mp4"))
def test_vfl_fixtures_match_their_hashes():
    res = vflmod.verify()
    bad = {k: v for k, v in res.items() if v != "ok"}
    assert not bad, bad


def _coverage(kind):
    from benchmarks.refbench import labels
    missing, seen = [], 0
    for vid, src in vflmod.sources().items():
        for name, w in src["windows"].items():
            for lab in w.get("labels", []):
                if (lab == "syllables") != (kind == "syllables"):
                    continue
                seen += 1
                res = labels.coverage(vflmod.window(vid, name), lab)
                if not res["ok"]:
                    missing.append((vid, name, res))
    return seen, missing


@local_only(fixture("labels"))
def test_at0d3_person_masks_cover_every_wave_1_2_window():
    seen, missing = _coverage("masks")
    assert seen >= 12 and not missing, missing


@local_only(fixture("labels"))
@pytest.mark.xfail(strict=True, reason="AT-0D-3 syllable onsets need a "
                   "listener: templates and spectrogram sheets are in the "
                   "label store (labels syllable-sheet); remove this marker "
                   "when both windows are filled and reviewed")
def test_at0d3_syllable_onsets_reviewed():
    seen, missing = _coverage("syllables")
    assert seen == 2 and not missing, missing
