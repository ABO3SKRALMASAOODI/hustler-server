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


def test_source_comparison_labels_keep_their_seat_under_a_device_title():
    voices = {"v": {"px": 94, "font": "ArchivoBlack-Regular.ttf"}}
    title = dict(word("image", "IMAGE", "v", .24, .40, [1060, 1064], "big"), text_group="brackets")
    left = dict(title, id="left", t="[", x=.13)
    native = [.20, .34, .76, .39]
    card = {"k": "card", "id": "source", "f": [1057, 1090], "source_ink": [native]}
    scene = dict(frame=[1080, 1920], voices=voices, items=[card, title, left])
    resolve(scene)
    b = item_box(title, voices, 1080, 1920, 1062)
    assert b[3] <= native[1] or b[1] >= native[3]
    assert title["y"] == left["y"]


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


def test_wang_payoff_hands_the_spoken_word_to_its_live_cutout():
    from scene.alien.diecut import caption_handoff
    cap = word("w0190", "compounds", "v", .23, .615, [1516, 1555], "cap")
    cap["op"] = [1.] * 39
    start, end, changed = caption_handoff([cap], "Compounds.", 1513, 1556, 30)
    assert (start, end) == (1523, 1556)
    assert len(changed) == 1 and changed[0]["f"] == [1516, start]
    assert changed[0]["id"] == cap["id"] and len(changed[0]["op"]) == 7


def test_wang_llama_page_animates_once_and_its_caption_clears_the_source():
    from scene.palette import merge
    from scene.palette.proof import proof_object
    from scene.text_layout import proof_bounds
    voice = {"v": {"font": "ArchivoBlack-Regular.ttf", "px": 58}}
    cap = word("w0084", "Llama", "v", .42, .60, [313, 325], "cap")
    card = dict(k="card", id="cd0012", pic="v07", f=[293, 328], x=.5, y=.399, w=.92, h=.291)
    base = dict(k="shot", id="original", pic="v07", f=[0, 360], src=[0, 12])
    scene = dict(frame=[1080, 1920], dur_f=360, fps=30, workflow_text_layout=True,
                 assets={"v07": {"kind": "video", "w": 1920, "h": 1080}}, voices=voice, items=[base, card, cap])
    ask = dict(dev="proof_object", f=[309, 350], pool=["v07"], box=[.25,.6,.51,.66], desk="blur")
    scene = merge(scene, proof_object(scene, "", ask, None))
    resolve(scene)
    for f in range(313, 325):
        pages = [i for i in scene["items"] if i["k"] == "card" and i["f"][0] <= f < i["f"][1]]
        assert len(pages) == 1
        source = proof_bounds(pages[0], f, 1080, 1920)
        ink = item_box(cap, voice, 1080, 1920, f)
        assert ink[1] >= source[3] or ink[3] <= source[1]
    assert next(i for i in scene["items"] if i["id"] == "cd0012")["f"] == [293, 309]


def test_ghodsi_reveal_has_separate_seats_for_face_tile_word_and_mark():
    from scene.palette.kit import MARK
    from scene.plan import A
    voice = {"v": {"font": "ArchivoBlack-Regular.ttf", "px": 92}}
    headline = word("w0026","TRANSFORM","v",.23,.17,[0,12],"big")
    shot = dict(k="shot",id="s",pic="main",f=[0,12],src=[24.,24.4],crop=[.02,.13,.28,.95])
    tile = dict(k="card",id="pip.in",pic="orbit",inset_seat=True,f=[0,12],w=1.,h=1.,
                x=.5,y=.40,s=[.42]*7+[.50,.62,.75,.88,1.])
    meta = dict(pw=1920,ph=1080,faces=[[.35,.25,.11,.24]],track=[[24.,.35,.25,.46,.49],[24.4,.40,.25,.51,.49]])
    scene = dict(frame=[1080,1920],fps=30,voices=voice,items=[shot,tile,headline])
    resolve(scene, {"main":meta})
    def apart(a,b):
        return a[0]>=b[2] or a[2]<=b[0] or a[3]<=b[1] or a[1]>=b[3]
    for f,(l,t,r,b) in enumerate(shot["crop"]):
        x=.35+.05*f/12
        face=((x-l)/(r-l),(.25-t)/(b-t),(x+.11-l)/(r-l),(.49-t)/(b-t))
        assert min(face)>=0 and max(face)<=1
        y, size = A(tile["y"],f,0),A(tile["s"],f,0)
        card=(.5-size/2,y-size/2,.5+size/2,y+size/2)
        text=item_box(headline,voice,1080,1920,f)
        assert apart(face,card) and apart(face,text) and apart(MARK,text) and apart(card,text)


def test_performed_word_owns_only_its_visible_frames():
    from scene.palette import share_word_time
    base=word("cap","intelligence.","v",.3,.7,[102,134],"cap")
    device=word("behind","INTELLIGENCE","v",.2,.5,[105,129],"graphic")
    kept=word("next","already","v",.5,.7,[108,134],"cap")
    scene=share_word_time(dict(items=[base,device,kept]),dict(items=[device]),{"behind"})
    assert [it["f"] for it in scene["items"] if it["t"]==base["t"]]==[[102,105],[129,134]]
    assert kept in scene["items"] and device in scene["items"]


def test_replaced_middle_of_source_changes_segment_dependency(tmp_path):
    from scene.plan import fingerprint
    source=tmp_path/"clip"
    source.write_bytes(b"a"*600000)
    before=fingerprint(source)
    with source.open("r+b") as stream:
        stream.seek(300000)
        stream.write(b"b")
    assert fingerprint(source)!=before


def test_podcast_opening_yields_to_active_voice_and_keeps_musical_clock(tmp_path):
    from score import music
    import numpy as np
    song=dict(bars=[{"t0":17.}])
    plan=dict(tempo=1.1,end_hit_s=39.5,end_hit_bar=0,segments=[dict(tempo=1.2)])
    voice=np.ones((3*music.SR,2),np.float32)*.1
    bed=np.full_like(voice,.12)
    measured={}
    music.podcast_bed(bed,voice,dict(words=[(.05,.9)],dur_s=40.333),song,plan,tmp_path/"music.mp3",measured)
    assert np.mean(bed[:music.SR]**2) <= np.mean(voice[:music.SR]**2)*.1001
    assert np.allclose(bed[-music.SR:],.12)
    assert abs(measured["card_bed"]["at_s"]-(17.+.833*1.2)) < .0001


def test_orbital_reveal_mattes_only_the_visible_take_interval(tmp_path,monkeypatch):
    from scene.palette import matte, sense, behind
    clip=tmp_path/"take.mp4"
    clip.write_bytes(b"test source")
    shot=dict(k="shot",id="speaker",pic="main",f=[0,1210],src=[0.,1210/30])
    doc=dict(frame=[1080,1920],fps=30,dur_f=1210,assets={"main":{"kind":"video","path":str(clip)}},
             speaker_layout={"main":{"faces":[[.3,.3,.2,.2]]}},items=[shot])
    seen=[]
    def infer(path,ranges,total,fps,out,size):
        seen.extend(ranges)
        from pathlib import Path
        Path(out).parent.mkdir(exist_ok=True)
        Path(out).write_bytes(b"mask")
    monkeypatch.setattr(behind.asense,"probe",lambda _:dict(fps=30.,dur=40.34,w=1920,h=1080))
    monkeypatch.setattr(behind,"_rvm",infer)
    sensor=sense.Sense(doc,str(tmp_path))
    wins,patch=matte._ready(doc,sensor,dict(f=[1132,1154]))
    assert len(wins)==1 and "matte" in patch["assets"]
    assert seen==[(1130,1157)]
    matte._ready(doc,sensor,dict(f=[1132,1154]))
    assert len(seen)==1
    clip.write_bytes(b"new source!")
    matte._ready(doc,sensor,dict(f=[1132,1154]))
    assert len(seen)==2


def test_ghodsi_caption_tokens_move_as_one_line_across_the_picture_cut():
    from scene.plan import A
    voices = {"v": dict(font="ArchivoBlack-Regular.ttf", px=58)}
    first = word("first", "you", "v", .27, .615, [0,24], "cap")
    second = word("second", "know", "v", .51, .615, [10,24], "cap")
    card = dict(k="card", id="evidence", f=[0,12], source_ink=[[.24,.50,.41,.75]])
    scene = dict(frame=[1080,1920], fps=30, voices=voices, items=[card,first,second])
    resolve(scene)
    for f in range(10,24):
        assert abs(A(first["y"], f, 0)-A(second["y"], f, 10)) < .0001


def test_archival_credit_survives_a_transparent_projector_finish():
    from scene.palette.kit import picture_spans
    layers = [dict(k="card", pic="archive", f=[4,24]),
              dict(k="card", pic="palette_gate", f=[8,20], z=40)]
    assert picture_spans(dict(items=layers), "archive", 4,24) == [[4,24]]


def test_full_width_headline_has_margins_on_a_tracked_closeup():
    from scene.palette.kit import MARK, place
    voice = dict(font="ArchivoBlack-Regular.ttf", px=138)
    x, y = place(voice, "INTELLIGENCE", .5, .35, 1080, 1920)
    title = word("headline", "INTELLIGENCE", "v", x, y, [0,2], "big")
    title["s"] = [1.18,1.0]
    shot = dict(k="shot",id="take",pic="main",f=[0,2],crop=[0,0,1,1])
    face = dict(pw=1080,ph=1920,faces=[[.09,.22,.82,.53]])
    scene = dict(frame=[1080,1920],fps=30,voices={"v":voice},items=[shot,title])
    resolve(scene, {"main":face})
    for frame in range(2):
        left, top, right, _ = item_box(title, scene["voices"],1080,1920,frame)
        assert left >= .065 and right <= .935 and top >= MARK[3]+.018


def test_podcast_bed_yields_to_quiet_midphrase_speech_without_moving_the_tail(tmp_path):
    from score import music
    import numpy as np

    # Ghodsi m04's 37–38 s spot: a nearly level low bed overtakes a softer phrase.
    sr = music.SR
    voice = np.zeros((40*sr, 2), np.float32)
    voice[37*sr:38*sr] = .10
    bed = np.full_like(voice, .106)
    original = bed.copy()
    marks = dict(words=[(37.0,37.26),(37.26,37.50),(37.50,37.76),(37.76,38.)],dur_s=40.)
    plan = dict(tempo=1.,end_hit_s=39.,end_hit_bar=0,segments=[dict(tempo=1.)])
    report = {}
    music.podcast_bed(bed,voice,marks,dict(bars=[dict(t0=25.)]),plan,tmp_path/'score.mp3',report)
    phrase = slice(37*sr,38*sr)
    assert np.mean(bed[phrase]**2) < np.mean(voice[phrase]**2)
    assert np.array_equal(bed[:36*sr],original[:36*sr])
    assert np.array_equal(bed[39*sr:],original[39*sr:])
    assert report['card_bed']['at_s'] == 26.


def test_started_using_is_owned_by_the_live_staircase():
    from scene.palette import share_word_time
    caps = [word(str(i), t, "cap", .2+i*.1, .8, [a, 201], "cap")
            for i,(t,a) in enumerate([("who",167),("started",174),("using",183)])]
    stair = dict(word("r0","STARTED USING","big",.15,.4,[173,191],"big"), op=[0]+[1]*17)
    items = share_word_time(dict(items=caps+[stair]),dict(items=[stair]),{"r0"})["items"]
    for f in range(167,201):
        visible = [i["t"] for i in items if i.get("role")=="cap" and i["f"][0]<=f<i["f"][1]]
        assert visible == [i["t"] for i in caps if i["f"][0]<=f and (i["t"]=="who" or f>=191)]


def test_contrast_plate_follows_moving_revenue_until_its_last_frame():
    from scene.palette.kit import item_box
    voices = {"v": dict(px=32,font="ArchivoBlack-Regular.ttf")}
    w = dict(word("revenue","REVENUE","v",.15,.4,[1,5],"big"),
             y=[.34,.54,.76,.68], op=[.6,1.,1.,0.], s=[.8,1.,.9,1.],
             ink="#101010", backplate="#E5AF35")
    scene = film([plate(6,"#101010"),w],dur=6,frame=(360,640),voices=voices)
    for f in range(6):
        rgb=one(scene,f)[:,:,:3].astype(float)
        ys,xs=np.where((rgb[:,:,0]-rgb[:,:,2]>45) & (rgb[:,:,0]>100))
        if 1<=f<=3:
            l,t,r,b=item_box(w,voices,360,640,f)
            assert len(xs) and xs.min()/360<l<r<xs.max()/360 and ys.min()/640<t<b<ys.max()/640
            assert abs((ys.min()+ys.max())/1280-(t+b)/2)<.012
        else:
            assert not len(xs)


def test_exchange_keeps_one_baseline_through_reframing_and_word_entrances():
    from scene.plan import A
    # The Ghodsi two-shot at 18.3 s. The early mark pass moved only the
    # rightmost tokens of a caption page. The final crop must seat them together.
    voice = {"cap":dict(px=58, font="InterDisplay-Bold.ttf")}
    shot = dict(id="s",k="shot",pic="take",f=[553,630],src=[18.3333,20.9],
                crop=[.13601,0,.45242,1])
    meta = dict(pw=1920,ph=1080,faces=[[.17236,.21036,.25962,.54879]],
                track=[[18.3,.17076,.21473,.3473,.6531],[20.9,.21,.18,.4,.69]])
    caps = [dict(word(t,t,"cap",x,y,[a,b],"cap"),chunk=f"caption-{b}") for t,x,y,a,b in (
        ("we",.29,.18143,569,591),("need",.375,.18143,577,591),("to",.511,.22228,582,591),
        ("move",.573,.22228,585,591),("Token",.296,.17365,597,609),("matching.",.456,.22228,603,609))]
    scene = dict(items=[shot,*caps], fps=30, frame=[1080,1920], voices=voice)
    resolve(scene,{"take":meta})
    seats = {A(i["y"],f,i["f"][0]) for i in caps for f in range(*i["f"])}
    assert len(seats)==1 and .70 < seats.pop() < .94
    assert [i["f"][0] for i in caps]==[569,577,582,585,597,603]


def test_source_chart_keeps_whole_caption_page_below_both_axes():
    from scene.plan import A
    voices = {"v": dict(font="ArchivoBlack-Regular.ttf",px=58)}
    card = dict(k="card",id="plot",f=[6,24],source_ink=[[0,.48,1,.79]],caption_baseline=.87)
    first = word("one","cost","v",.25,.665,[0,20],"cap")
    second = word("two","per","v",.55,.665,[10,20],"cap")
    for token in (first,second):
        token["chunk"] = "same sentence"
    resolve(dict(frame=[1080,1920],fps=30,voices=voices,items=[card,first,second]))
    for layer in (first,second):
        assert all(abs(A(layer["y"],f,layer["f"][0])-.87)<.001 for f in range(*layer["f"]))
