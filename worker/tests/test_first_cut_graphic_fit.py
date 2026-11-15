"""Automatic first-film sizes may fit around faces; typed sizes stay pinned."""
import time
from types import SimpleNamespace

import pytest

import first_cut
import keepout
import motion_templates
import motion_tools


def test_automatic_hook_can_find_a_face_clear_seat(monkeypatch):
    # Measured moving-face envelope and ink from the copied firework replay.
    faces = [[.5335, .2719, .8007, .4705], [.2618, .2924, .5273, .4794],
             [.1699, .1381, .579, .4358], [.3031, .1234, .7492, .5481]]
    track = [(i * .5, [face]) for i, face in enumerate(faces)]
    item = dict(template="hook_title", params=motion_templates.check_params(
        "hook_title", dict(text="Hei *aademi* hei aademi", font="Anton",
                           size=1.3, y=.42, uppercase=True)))
    spec = motion_templates.spec("hook_title")
    box = (.067, .315, .959, .525)
    ctx = SimpleNamespace()
    seen = []

    def execute(c, name, args):
        pin = motion_tools._pinned_size(spec, item, c)
        picks = keepout.candidates(
            "hook_title", spec, item["params"], box, [], keepout.zones_of(track),
            720, 1280, mouths=keepout.zones_of(track, keepout.mouth_zone),
            chins=keepout.zones_of(track, keepout.chin_zone), pin_size=bool(pin))
        seen.extend(picks)
        assert pin is None
        return "ok"

    monkeypatch.setattr(first_cut.agent_tools, "execute", execute)
    first_cut._run(ctx, [], "add_motion_graphic", item, time.monotonic())
    assert seen
    assert all(not keepout.assess(b, track)["hit"] for _, _, b in seen)
    assert all(not keepout.safe_issues(b, 720, 1280) for _, _, b in seen)
    assert not hasattr(ctx, "first_cut_auto_graphic")
    assert motion_tools._pinned_size(spec, item, ctx) == "size"


@pytest.mark.parametrize("fails", [False, True])
def test_auto_fit_scope_is_restored_after_each_tool(monkeypatch, fails):
    ctx = SimpleNamespace(first_cut_auto_graphic=False)

    def execute(c, name, args):
        assert c.first_cut_auto_graphic is (name == "add_motion_graphic")
        if fails:
            raise RuntimeError("probe unavailable")
        return "ok"

    monkeypatch.setattr(first_cut.agent_tools, "execute", execute)
    for name in ["add_motion_graphic", "set_frame"]:
        first_cut._run(ctx, [], name, {}, time.monotonic())
        assert ctx.first_cut_auto_graphic is False


def test_automatic_hook_real_ink_clears_moving_faces():
    import motion_engine
    if not motion_engine.available():
        pytest.skip("no headless Chromium")
    item = dict(id="hook", template="hook_title", start=0., end=3.,
                params=motion_templates.check_params("hook_title", dict(
                    text="Hei *aademi* hei aademi", font="Anton", size=1.3,
                    y=.42, uppercase=True, entrance="cut", exit="blur")))
    track = [(i * .5, [face]) for i, face in enumerate([
        [.5335, .2719, .8007, .4705], [.2618, .2924, .5273, .4794],
        [.1699, .1381, .579, .4358], [.3031, .1234, .7492, .5481]])]
    spec = motion_templates.spec("hook_title")
    rep = motion_tools._probe_item(item, 720, 1280)
    assert rep and not rep.get("errors")
    ink = keepout.settled_ink(rep, motion_tools._probe_times(item))
    assert keepout.assess(ink, track)["hit"]
    fixed = motion_tools._relocate(
        item, spec, ink, keepout.zones_of(track), track, 720, 1280,
        ([], []), pin=motion_tools._pinned_size(
            spec, item, SimpleNamespace(first_cut_auto_graphic=True)))
    assert fixed, "Automatic title must find a measured readable placement"
    _, actual, params, _ = fixed
    assert not keepout.assess(actual, track)["hit"]
    assert not keepout.safe_issues(actual, 720, 1280)
    assert params["text"] == item["params"]["text"]
    assert actual[3] - actual[1] >= .07


@pytest.mark.parametrize('pinned', [False, True])
def test_empty_placement_search_still_measures_smaller_automatic_layout(monkeypatch, pinned):
    item = dict(id='hook', template='hook_title', start=0., end=3.,
                params=motion_templates.check_params('hook_title', dict(
                    text='Keep every word in this title', size=1.3, y=.7)))
    original = motion_templates.spec('hook_title')
    # Only the two controls exercised here; align alternatives are covered
    # by the browser regression above.
    spec = dict(original, params={k: original['params'][k] for k in ('size', 'y')})
    track = [(0., [[.25, .3, .75, .95]])]
    zones = keepout.zones_of(track)
    large = (.06, .48, .94, .8)
    small = (.2, .65, .8, .75)
    assert not keepout.candidates('hook_title', spec, item['params'], large,
                                 [], zones, 720, 1280, pin_size=pinned)
    measured = []

    def ladder(*args):
        measured.append(True)
        return [({'size': .78}, small)]

    def probe(trials, *args):
        return [dict(visible_frames=1, box=keepout.shift(
            small, 0., t['params']['y'] - .7)) for t in trials]

    monkeypatch.setattr(motion_tools, '_size_ladder', ladder)
    monkeypatch.setattr(motion_tools, '_probe_items', probe)
    monkeypatch.setattr(keepout, 'settled_ink', lambda rep, times: rep['box'])
    fixed = motion_tools._relocate(item, spec, large, zones, track,
                                   720, 1280, ([], []), pin='size' if pinned else None)
    if pinned:
        assert fixed is None and not measured
    else:
        assert measured and fixed
        _, ink, params, _ = fixed
        assert not keepout.assess(ink, track)['hit']
        assert not keepout.safe_issues(ink, 720, 1280)
        assert params['text'] == item['params']['text']
