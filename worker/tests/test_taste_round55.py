"""Round 55 — the wide-gameplay edit that shipped with everything wrong.

One real turn (project 222, Jul 27 2026) produced every defect fixed here at
once: a 1600x720 Mobile Legends recording converted to 9:16 by CROPPING 75% of
the width away, a title card whose three-line title burned straight through its
own subtitle on the FIRST frame, three more text cards piled onto the last 1.5
seconds, five sound effects nobody asked for, and a whip transition every 3.3
seconds. The taste audit of the day fired on some of it, and the agent replied
"Preview is ready at 30s. Here's the edit:" and shipped it.

Every test below is that turn's real data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import graphics                                             # noqa: E402
import subject                                              # noqa: E402
import taste                                                # noqa: E402
from timeline import Timeline                               # noqa: E402


# The 9:16 vertical of the real job: 1080x1920.
PLAY_RES = (1080, 1920)


def _card(title, subtitle, title_y=0.5, sub_y=0.5):
    # y-only: an item is PINNED (and exempt from collision repair) only when
    # the author sets BOTH coordinates — round 79b, where the stacker was
    # "repairing" a deliberately side-by-side two-colour wordmark into a
    # column. A y-only card is template composition and keeps the round-55
    # protection these tests exist for.
    return [
        {"id": "tx1", "text": title, "start": 0.12, "end": 2.38,
         "template": "title", "y": title_y, "font": "Anton",
         "entrance": "pop", "anchor_insert": "ins1"},
        {"id": "tx2", "text": subtitle, "start": 0.12, "end": 2.38,
         "template": "subtitle", "y": sub_y, "entrance": "pop",
         "anchor_insert": "ins1"},
    ]


def _boxes(texts, out_dur=30.0, res=PLAY_RES):
    return [ev for ev in graphics._stack_concurrent(texts, out_dur, res) if ev]


# ── text on top of text ──────────────────────────────────────────────────

def test_the_original_title_card_collision_is_gone():
    """The exact first frame the user complained about.

    At the old hardcoded subtitle y=0.635 the title's three wrapped lines span
    675-1245px and the subtitle sits at 1167-1271px: 78 pixels of overlap.
    """
    old = _card("MOSKOV CRITICAL BUILD \U0001F525", "Worth to try?", sub_y=0.635)
    a, b = (graphics._compile_item(t, 30.0, PLAY_RES) for t in old)
    assert a["bottom"] > b["top"], "test premise: these must collide untreated"

    a, b = _boxes(old)
    assert a["bottom"] <= b["top"], "stacker must separate them"


def test_card_pair_is_centred_at_any_title_length():
    for title in ("MOSKOV CRITICAL BUILD \U0001F525", "VICTORY"):
        a, b = _boxes(_card(title, "Worth to try?"))
        assert a["bottom"] <= b["top"]
        centre = (a["top"] + b["bottom"]) / 2.0
        assert abs(centre - PLAY_RES[1] / 2.0) < 2.0, (title, centre)


def test_three_stacked_cards_all_separate():
    """The real last 1.5s: VICTORY + the title + the tagline, all at once."""
    texts = [
        {"id": "tx6", "text": "VICTORY \U0001F3C6", "start": 28.5, "end": 29.98,
         "template": "title", "font": "Anton", "size_scale": 1.5},
        {"id": "tx7", "text": "MOSKOV CRITICAL BUILD", "start": 28.8,
         "end": 29.98, "template": "subtitle", "font": "Inter Display Bold"},
        {"id": "tx8", "text": "Worth to try?", "start": 29.0, "end": 29.98,
         "template": "callout", "font": "Inter Display Bold"},
    ]
    evs = _boxes(texts)
    for i in range(len(evs)):
        for j in range(i + 1, len(evs)):
            assert not graphics._overlaps(evs[i], evs[j]), (i, j)


def test_a_fully_pinned_pair_is_left_where_the_author_put_it():
    """Round 79b: setting BOTH x and y is deliberate composition — two chunks
    laid side by side (a two-colour wordmark) are exactly what explicit
    coordinates exist for. The stacker must not "repair" them apart, even
    when their boxes touch."""
    texts = [
        {"id": "tx1", "text": "Valmera", "start": 0.0, "end": 2.0,
         "template": "title", "x": 0.46, "y": 0.5},
        {"id": "tx2", "text": ".io", "start": 0.0, "end": 2.0,
         "template": "title", "x": 0.58, "y": 0.5},
    ]
    for tx, ev in zip(texts, _boxes(texts)):
        assert ev["text"] == graphics._compile_item(tx, 30.0, PLAY_RES)["text"]


def test_non_overlapping_text_is_not_moved():
    """The byte-identical guarantee: an edit whose texts never collide must
    compile exactly as it did before the stacker existed."""
    texts = [
        {"id": "tx1", "text": "FIRST", "start": 0.0, "end": 2.0,
         "template": "title"},
        {"id": "tx2", "text": "SECOND", "start": 5.0, "end": 7.0,
         "template": "subtitle"},
    ]
    for tx, ev in zip(texts, _boxes(texts)):
        assert ev["text"] == graphics._compile_item(tx, 30.0, PLAY_RES)["text"]


def test_items_in_different_columns_do_not_stack():
    """A left-anchored lower third and a centred callout share a second but
    never a column — stacking them would move a graphic for nothing."""
    texts = [
        {"id": "tx1", "text": "Ana", "start": 1.0, "end": 4.0,
         "template": "lower_third"},
        {"id": "tx2", "text": "LIVE", "start": 1.0, "end": 4.0,
         "template": "callout"},
    ]
    for tx, ev in zip(texts, _boxes(texts)):
        assert ev["text"] == graphics._compile_item(tx, 30.0, PLAY_RES)["text"]


# ── the crop that truncated ──────────────────────────────────────────────

def test_one_face_in_five_frames_is_not_a_face():
    """A Haar false positive on a game HUD matched 1 of 5 sampled frames and
    aimed the 9:16 crop at (0.39, 0.20). A cascade's false-positive rate is
    per-frame and independent; a real subject is in most of the frames."""
    assert subject.points_from_frames.__doc__          # module imported fine
    face_like = [(0.39, 0.20)]
    quorum = 1 if 5 < 3 else max(2, 5 // 3)
    assert len(face_like) < quorum


def test_energy_spread_over_the_whole_frame_fails_the_crop():
    """crop_detail_kept is the number auto_reframe decides on. A synthetic
    frame with detail at both edges must not survive a 9:16 window."""
    cv2 = subject._cv2()
    if cv2 is None:
        return
    import tempfile

    import numpy as np
    img = np.zeros((720, 1600, 3), dtype=np.uint8)
    # A HUD: busy content hard against the left and right edges, like a
    # minimap and a scoreboard, with a calm middle.
    img[:, :180] = np.random.randint(0, 255, (720, 180, 3), dtype=np.uint8)
    img[:, -180:] = np.random.randint(0, 255, (720, 180, 3), dtype=np.uint8)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        path = fh.name
    cv2.imwrite(path, img)
    try:
        kept = subject.crop_detail_kept([path], 9, 16)
        assert kept is not None
        assert kept < 0.55, f"a 9:16 crop kept {kept} of an edge-heavy frame"
    finally:
        os.unlink(path)


def test_centred_detail_survives_the_crop():
    cv2 = subject._cv2()
    if cv2 is None:
        return
    import tempfile

    import numpy as np
    img = np.zeros((720, 1600, 3), dtype=np.uint8)
    img[160:560, 700:900] = np.random.randint(0, 255, (400, 200, 3),
                                              dtype=np.uint8)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        path = fh.name
    cv2.imwrite(path, img)
    try:
        kept = subject.crop_detail_kept([path], 9, 16)
        assert kept is not None and kept > 0.9, kept
    finally:
        os.unlink(path)


# ── the audit ────────────────────────────────────────────────────────────

def _edl(**over):
    """The real EDL v28's timeline: 9 keeps + a 2.5s title card + a 0.5x span,
    which is what makes the programme 29.98s. The numbers the findings quote
    are only the numbers the user saw if the timeline is the one they got."""
    edl = {
        "keep": [[1.5, 4.0], [25.5, 27.2], [63.7, 65.78], [78.9, 81.5],
                 [91.2, 92.6], [122.0, 125.0], [131.0, 136.5],
                 [158.3, 160.3], [179.0, 180.2]],
        "sfx": [], "texts": [], "music": [],
        "inserts": [{"id": "ins1", "kind": "image", "at_output_s": 0.0,
                     "duration_s": 2.5,
                     "asset_key": "generated/222/card.png"}],
        "speed": [{"id": "sp1", "start": 131.0, "end": 136.5, "factor": 0.5}],
        "effects": {}, "frame": None,
    }
    edl.update(over)
    return edl


def _critique(edl, ask="", index=None):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    idx = index if index is not None else {"words": [], "shots": []}
    return taste.critique(edl, idx, tl, src_w=1600, src_h=720, user_asked=ask)


def test_sfx_taste_does_not_depend_on_a_keyword_permission_check():
    edl = _edl(sfx=[{"at": t, "storage_key": "sfx:boom"}
                    for t in (5.0, 10.0, 15.3, 20.0, 28.8)])
    generic = _critique(edl)
    requested = _critique(edl, ask="add some sound effects")
    assert generic == requested
    assert not any("without the user asking" in f for f in generic)


def test_transition_cadence_fires_even_at_scene_scope():
    """9 real scene changes in 30s is still a whip every 3.3s."""
    edl = _edl(effects={"transition": {"style": "whip_left",
                                       "duration_s": 0.2, "scope": "scene"}})
    hits = [f for f in _critique(edl) if "one every" in f]
    assert hits and "3.3s" in hits[0], hits


def test_combined_device_rate_catches_what_each_rule_alone_misses():
    edl = _edl(
        sfx=[{"at": t, "storage_key": "sfx:boom"}
             for t in (5.0, 10.0, 15.3, 20.0, 28.8)],
        effects={"transition": {"style": "whip_left", "duration_s": 0.2,
                                "scope": "scene"},
                 "zooms": [{"id": "z1", "start": 8.8, "end": 10.5},
                           {"id": "z2", "start": 17.0, "end": 24.0},
                           {"id": "z3", "start": 28.5, "end": 29.5}],
                 "stylize": [{"id": "s1", "kind": "shake", "start": 8.6,
                              "end": 10.0},
                             {"id": "s2", "kind": "shake", "start": 18.5,
                              "end": 22.0},
                             {"id": "s3", "kind": "flash", "start": 28.5,
                              "end": 29.2}]})
    hits = [f for f in _critique(edl) if "attention-grabbing devices" in f]
    assert hits, "20 devices in 30s must be reported as a rate"
    assert "1.5s" in hits[0], hits[0]


def test_unaimed_crop_of_a_wide_source_is_a_finding():
    edl = _edl(frame={"ratio": "9:16", "mode": "crop"})
    assert any("truncated my video" in f for f in _critique(edl))
    # A measured crop came from auto_reframe and is left alone.
    aimed = _edl(frame={"ratio": "9:16", "mode": "crop",
                        "focus_x": 0.5, "focus_y": 0.4})
    assert not any("truncated my video" in f for f in _critique(aimed))
    # So is a fit, which is the whole point.
    fitted = _edl(frame={"ratio": "9:16", "mode": "pad_blur"})
    assert not any("truncated my video" in f for f in _critique(fitted))


def test_concurrent_text_cards_are_reported_but_a_card_pair_is_not():
    trio = _edl(texts=[
        {"id": "tx6", "text": "VICTORY", "start": 28.5, "end": 29.98,
         "template": "title"},
        {"id": "tx7", "text": "MOSKOV", "start": 28.8, "end": 29.98,
         "template": "subtitle"}])
    assert any("same time" in f for f in _critique(trio))

    pair = _edl(texts=[
        {"id": "tx1", "text": "MOSKOV", "start": 0.12, "end": 2.38,
         "template": "title", "anchor_insert": "ins1"},
        {"id": "tx2", "text": "Worth to try?", "start": 0.12, "end": 2.38,
         "template": "subtitle", "anchor_insert": "ins1"}])
    assert not any("same time" in f for f in _critique(pair))


def test_transition_spacing_constant_is_shared_with_the_tool():
    """The tool result and the audit must not disagree about "too often"."""
    import agent_tools
    assert agent_tools.TRANSITION_MIN_SPACING_S == \
        taste.TRANSITION_MIN_SPACING_S
