"""record_website_demo steps: the documented shape, plus the unambiguous
spellings models actually write (25 Oct 2026: a trial-lane run failed four
times on {"action": "scroll"} because compact tool schemas drop the `do`
example, and the pinned core-catalog size keeps the schema as it is)."""

import pytest

import webrecord


def test_aliases_and_shorthand_mean_one_step():
    steps = webrecord._norm_steps([
        {"action": "goto", "url": "https://valmera.io", "note": "hero"},
        {"action": "wait", "seconds": 2},
        {"type": "scroll", "amount": 400},
        {"goto": "https://valmera.io/pricing"},
        {"wait": 1.5},
        {"scroll": "Pricing"},
        {"click": "Start free"},
        {"do": "type", "selector": "input[type=email]", "text": "a@b.co"},
    ])
    assert [s["do"] for s in steps] == ["goto", "wait", "scroll", "goto",
                                        "wait", "scroll", "click", "type"]
    assert steps[2]["by"] == 400.0 and steps[5]["to"] == "Pricing"
    assert steps[6]["text"] == "Start free"


def test_unknown_shape_names_the_canonical_form():
    with pytest.raises(webrecord.WebRecordError) as err:
        webrecord._norm_steps([{"foo": 1}])
    assert '"do"' in str(err.value)
