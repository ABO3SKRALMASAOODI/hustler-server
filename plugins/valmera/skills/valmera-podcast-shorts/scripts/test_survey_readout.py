import wf1_source_frame as frames
from test_wf1_take import quiet_engines


def test_composed_row_magnifies_two_spoken_fields(monkeypatch):
    class Drawing:
        def __init__(self):
            self.cards = []
        def page(self, *args, **kwargs):
            pass
        def card(self, span, key, rect, **style):
            self.cards.append((key,rect,style))
    cells = [("Engineering", [.31,.52,.37,.54]), ("56%", [.41,.52,.44,.54]),
             ("Tools & Systems", [.60,.52,.69,.54])]
    monkeypatch.setattr(frames,"visible_lines",lambda *args: cells,raising=False)
    plan = Drawing()
    row = dict(path="source",box=[.30,.51,.79,.73],panel_box=[.30,.24,.79,.73],
               w=1920,h=1080,shot=[0,31],clause="Engineering, they don't like the tools and systems.")
    frames.compose(plan,[204,279],row,"verified","take",[6.8,9.3],1080,1920,None,None)
    details = [(r,s["crop"]) for k,r,s in plan.cards if s.get("cell_detail")]
    assert len(details) == 2
    for (rect,crop), (_,box) in zip(details,(cells[0],cells[2])):
        assert crop[0] <= box[0] < box[2] <= crop[2]
        assert (rect[3]-rect[1])/(crop[3]-crop[1])*(box[3]-box[1])*1920 >= 48


def test_grounded_reaction_uses_bounded_listener_in_a_shared_shot():
    words = [dict(w="say",start=i,end=i+.8) for i in range(7)]
    shot = dict(shot=[0,7],t=3.)
    upper,lower=[.79,0,1,.21],[.79,.213,1,.427]
    result=dict(presenter_tiles=[dict(frame=0,box=upper),dict(frame=0,box=lower)],
                speaker_inserts=[dict(first=0,last=1,speaker=1),dict(first=2,last=3,speaker=0),
                                 dict(first=5,last=6,speaker=0)],reactions=[dict(first=4,last=4)])
    reading = frames.bind(dict(words=words,windows=[]),[shot],result)
    assert any(r["first"]==r["last"]==4 and r["tile"]==lower for r in frames.reaction_portraits(reading))

def test_closing_phrase_holds_together_after_comparison(tmp_path, quiet_engines, monkeypatch):
    import wf1_coverage
    import wf1_film
    from test_wf1_take import _sup, cuts_two_shot
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    pack = _sup(tmp_path, cuts_two_shot())
    words = pack["coverage"]["words"]
    last = len(words)-1
    for word, text in zip(words[-4:], "a piece of art.".split()):
        word["w"] = text
    pack["coverage"]["comparisons"] = [dict(first=9, last=last, reveal=last,
        premise="One designer", consequence="One shared work", steps=[dict(at=12,label="Shared authorship")])]
    script = dict(title="Design",segments=[dict(say="one"), dict(say="shared")])
    scene, beats = wf1_film.build(script,pack,"footage-cut",dict(coverage_final=True,caption_ends=[8,13,last]))
    closing = [word for word in scene["items"] if word.get("role")=="cap" and word["f"][1]==beats["dur_f"]]
    assert [word["t"] for word in closing] == ["a piece of art."]
    assert not any(it.get("t")=="Hypothetical" and it["f"][1] > closing[0]["f"][1]-2 for it in scene["items"])
