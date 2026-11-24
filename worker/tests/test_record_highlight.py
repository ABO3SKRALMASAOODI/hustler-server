from scene.palette import type as typography


def test_return_underlines_the_cited_line_at_the_new_cue():
    voice = dict(font="InterDisplay-Bold.ttf",px=55,track=0)
    row = dict(k="word",v="body",t="How did it go?",x=.1,y=.6,f=[50,90],ink="#FFFFFF")
    scene = dict(w=1080,h=1920,fps=30,dur_f=100,voices={"body":voice},
                 said=[["How",53,54,0],["did",54,55,0],["it",55,56,0],["go?",56,58,0]],
                 items=[dict(row,id="caption",role="cap"),dict(row,id="record",role="graphic")])
    result = typography.highlight(scene,None,dict(id="return",dev="highlight",t=row["t"],f=[60,85],
                                             style="under",target_id="record",c="#FFD080"))
    assert result["log"]["hit"] == 60
    assert result["items"][0]["underline_for"] == "record"
    assert result["items"][0]["f"] == [60,85]
