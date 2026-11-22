import numpy as np

from scene.palette.kit import item_box
from scene.text_layout import resolve
from score import sound
from test_scene_engine import film, one, plate


def word(name, text, voice, x, y, frames, role):
    return dict(
        k="word",
        id=name,
        t=text,
        v=voice,
        x=x,
        y=y,
        op=1,
        s=1,
        ink="#FFFFFF",
        f=frames,
        role=role,
    )


def test_wang_02_frame_42_keeps_counter_label_and_ladder():
    # "BIGGER" touches the orange AI PRODUCTS lockup over the Pocket Grid clip
    # at 1.40 s. The counter (due at frame 312) was already visible at 25.
    voices = {
        k: dict(font="ArchivoBlack-Regular.ttf", px=px)
        for k, px in (("ladder", 125), ("label", 46), ("caption", 58))
    }
    words = [
        word("ladder", "AI PRODUCTS", "ladder", 0.08, 0.405, [19, 78], "big"),
        word("counter-label", "BIGGER", "label", 0.42, 0.417, [25, 73], "stat"),
        word("cap", "AI", "caption", 0.27, 0.676, [38, 46], "cap"),
    ]
    scene = film(words, dur=78, voices=voices)

    def boxes(f):
        return [
            item_box(it, voices, 1080, 1920, f)
            for it in words
            if it["f"][0] <= f < it["f"][1]
        ]

    def clash(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    assert clash(*boxes(42)[:2])
    resolve(scene)
    for f in range(25, 73):
        b = boxes(f)
        assert all(not clash(x, y) for n, x in enumerate(b) for y in b[n + 1 :])
    assert len(scene["items"]) == 3


def test_wang_01_cutout_rim_and_live_letters_stay_registered():
    voice = {"px": 82, "font": "ArchivoBlack-Regular.ttf"}
    hole = dict(
        word("hole", "year", "v", 0.12, 0.44, [42, 44], "big"), text_group="lift"
    )
    rim = dict(hole, id="rim", x=0.115, y=0.437)
    mask = dict(
        id="live",
        k="join",
        mode="glyph",
        v="v",
        t="year",
        x0=0.114,
        y0=0.438,
        f=[42, 44],
        text_group="lift",
        s=1,
    )
    caption = word("caption", "for about a year", "v", 0.12, 0.44, [42, 44], "cap")
    scene = dict(
        frame=[1080, 1920], voices={"v": voice}, items=[hole, rim, mask, caption]
    )
    resolve(scene)
    assert (hole["y"], rim["y"], mask["y0"]) == (0.44, 0.437, 0.438)
    h = item_box(hole, scene["voices"], 1080, 1920, 42)
    c = item_box(caption, scene["voices"], 1080, 1920, 42)
    assert h[3] <= c[1] or c[3] <= h[1]


def test_scrim_follows_visible_ink_on_bright_ground():
    w = word("cap", "META", "cap", 0.30, 0.64, [1, 2], "cap")
    voice = dict(
        cap=dict(px=42, font="ArchivoBlack-Regular.ttf", shadow=dict(a=0.9, blur=0.008))
    )

    def frame(bg, index, items):
        return one(
            dict(
                film([plate(3, bg)] + items, dur=3, frame=(360, 640), voices=voice),
                workflow_text_layout=True,
            ),
            index,
        )

    white = frame("#FFFFFF", 1, [w])
    assert white[380:430].min() < 235
    assert white[480:560].min() > 249
    assert np.array_equal(frame("#FFFFFF", 2, [w]), frame("#FFFFFF", 2, []))
    dark = frame("#101010", 1, [w])
    assert dark.min() >= frame("#101010", 1, []).min()


def test_cues_need_both_a_story_decision_and_a_picture_landing(monkeypatch):
    ids = sound.STORY_SOUNDS
    monkeypatch.setattr(
        sound,
        "library",
        lambda: {s: dict(file=s + ".flac", hit_lufs=-14.0) for s in ids.values()},
    )
    master = 0.28 * np.sin(np.arange(sound.SR // 2) * 2 * np.pi * 1000 / sound.SR)
    monkeypatch.setattr(sound, "_full_band", lambda sid: master)
    events = [dict(f=f, kind="picture") for f in range(0, 360, 30)]
    voice = 0.1 * np.sin(np.arange(12 * sound.AR) * 2 * np.pi * 1000 / sound.AR)
    assert not sound.story_accents([], events, voice, 30)[0]
    cues = [
        dict(f=f, role="turn", sound=k, why="new evidence")
        for f, k in [
            (14, "impact"),
            (30, "tick"),
            (60, "sub"),
            (150, "air"),
            (270, "impact"),
        ]
    ]
    placed, report = sound.story_accents(cues, events, voice, 30)
    assert [(s["s"], s["event_f"]) for s in placed] == [
        (ids["sub"], 60),
        (ids["air"], 150),
    ]
    assert all(-25 < s["db"] < -14 for s in placed)  # -23 dBFS voice, -14 dB hit master
    assert all(
        abs((p["voice_db"] - p["peak_db"]) - 10.7) < 0.2 for p in report["placed"]
    )
    assert report["sounds"] == 2
