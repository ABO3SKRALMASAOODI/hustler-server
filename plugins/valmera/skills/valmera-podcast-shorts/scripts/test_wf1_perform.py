"""Display rows whose type performs (palette devices, worker/scene/palette): a device that draws its words takes the
row's place, a decoration rides the row as set, and every ask ends inside its row or its sentence."""
import pytest

import wf1_film as film
from test_wf1_take import _sup, cuts_two_shot, quiet_engines  # noqa: F401 - the fixture


def _build(tmp_path, display):
    words = _sup(tmp_path, cuts_two_shot())
    script = dict(title="Coach", segments=[dict(say=" ".join(w["w"] for w in words["words"]["words"] if w["seg"] == i), pictures=[])
                                           for i in (0, 1)])
    return film.build(script, words, "footage-cut", dict(turn_seg=1, coverage_final=True, display=display))


def _display_words(l1):
    return [it for it in l1["items"] if it["k"] == "word" and str(it.get("v", "")).startswith("dsp")]


def _caps(l1):
    return [it for it in l1["items"] if it["k"] == "word" and it.get("role") == "cap"]


def _seg_end(l1, seg):
    return max(w[2] for w in l1["said"] if w[3] == seg)


def test_a_row_a_device_draws_leaves_no_display_word_and_a_decoration_rides_its_row(tmp_path, quiet_engines):  # noqa: F811
    display = [dict(seg=0, text="coach me", role="hook", perform=dict(dev="typewriter")),
               dict(seg=1, text="hard problem", role="payoff", perform=dict(dev="highlight", t="hard"))]
    l1, beats = _build(tmp_path, display)
    asks = {a["dev"]: a for a in l1["devices"]}
    assert set(asks) == {"typewriter", "highlight"}
    shown = " ".join(it["t"] for it in _display_words(l1)).lower()
    assert "coach" not in shown                                  # the device draws it: no product display line
    assert "hard" in shown                                       # the decoration marks the row as set
    assert asks["highlight"]["until"] <= _seg_end(l1, 1) + 10    # ends with its row, inside its sentence
    assert asks["typewriter"]["bound"] <= _seg_end(l1, 0) + 10 and "until" not in asks["typewriter"]
    capt = {it["t"].lower().strip(".,") for it in _caps(l1)}
    assert "coach" not in capt and "me" not in capt              # its words are not said twice


def test_a_chapter_row_is_its_own_title(tmp_path, quiet_engines):  # noqa: F811
    l1, _ = _build(tmp_path, [dict(seg=0, text="coach me", role="hook", perform=dict(dev="chapter"))])
    assert l1["devices"][0]["dev"] == "chapter"
    assert "coach" not in " ".join(it["t"] for it in _display_words(l1)).lower()     # never the words twice on the plate


def test_a_device_that_draws_part_of_its_row_leaves_the_rest_in_the_caption(tmp_path, quiet_engines):  # noqa: F811
    l1, _ = _build(tmp_path, [dict(seg=0, text="coach me", role="hook", perform=dict(dev="behind", t="COACH"))])
    capt = [it["t"].lower().strip(".,") for it in _caps(l1)]
    assert "coach" not in capt and "me" in capt


def test_a_word_left_to_the_caption_inks_on_its_own_word():
    """m06b frame 689: 'I love' drawn by the device, 'you.' inked at the line's start, ten frames before it is said."""
    F = film.F
    script = {"segments": [{"say": "I love you so much"}]}
    toks = script["segments"][0]["say"].split()
    words = {"duration": 3, "words": [dict(w=t, start=0.35 * i, end=0.35 * i + 0.3, seg=0) for i, t in enumerate(toks)]}
    clock = F.Clock(words, script)
    plan = F.Plan({"cap": dict(font="InterDisplay-Bold.ttf", px=58, track=0)}, {})
    F.caption_build(plan, clock, "cap", .65, omit=F.said_span(clock, 0, "I love"))
    caps = [i for i in plan.items if i["k"] == "word"]
    assert [i["t"] for i in caps][:1] == ["you"]
    you = caps[0]
    assert you["on"] >= F.Clock.f(words["words"][2]["start"]) - 1                # on its own word, never before it is said


@pytest.mark.parametrize("dev", ["readalong", "track", "fineprint"])
def test_a_decoration_of_the_sentence_ends_with_its_sentence(tmp_path, quiet_engines, dev):  # noqa: F811
    l1, _ = _build(tmp_path, [dict(seg=0, text="coach me", role="hook", perform=dict(dev=dev))])
    ask = l1["devices"][0]
    assert ask["until"] <= _seg_end(l1, 0) + 10 < l1["dur_f"]


def test_contrast_plate_belongs_to_the_word_after_crop_and_brand_seating(tmp_path, quiet_engines, monkeypatch):
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    l1, _ = _build(tmp_path, [dict(seg=1, text="between", role="contrast", key="between")])
    owners = [it for it in _display_words(l1) if it.get("backplate")]
    assert [it["t"] for it in owners] == ["BETWEEN"]
    accent = owners[0]["backplate"]
    assert not any(it.get("pic") == "plate" and it.get("plate", {}).get("c0") == accent for it in l1["items"])
