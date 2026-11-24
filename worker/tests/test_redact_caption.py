from scene.palette import type as typography


def test_strike_of_spoken_phrase_emphasizes_whole_caption_on_its_own_clock(tmp_path):
    cue = dict(id="spoken", k="word", role="cap", v="cap", f=[144,221], x=.12, y=.8,
               ink="#FFFFFF", t="That’s like the person didn’t show up.")
    scene = dict(frame=[1080,1920], fps=30, dur_f=656, items=[cue], workflow_text_layout=True,
                 voices=dict(cap=dict(font="InterDisplay-Bold.ttf", px=58, track=0)),
                 said=[["didn't",189,200,1],["show",200,204,1],["up.",204,216,1]])
    ask = dict(id="dr1", dev="redact", at=[204,206], mode="strike", t="didn't show up.", c="#E9D8A6")
    patch = typography.redact(scene, str(tmp_path), ask)
    assert [w["id"] for w in patch["items"]] == [cue["id"]]
    word, = patch["items"]
    assert word["f"] == cue["f"] and word["t"] == cue["t"]
    assert word["stroke"]["c"] != word["ink"]
    assert patch["marks"][0]["f"] == 204
