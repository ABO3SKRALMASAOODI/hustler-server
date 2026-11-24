from scene import continuity


def test_a_late_split_camera_match_preserves_the_selected_faces(tmp_path,monkeypatch):
    path = tmp_path / "take.mp4"
    path.touch()
    views = [dict(first=53,shot=[0,17.2],box=[.35,.13,.668,.696]),
             dict(first=53,shot=[17.2,19.4],box=[.562,.148,.947,.833])]
    # Two frames still from the old camera can match perfectly at the nominal cut.
    monkeypatch.setattr(continuity,"_match",lambda *args: (1.,0.,0.))
    left,right = continuity.closing_crops(str(path),views,(1920,1080),30)
    assert left[0] < .42 < .60 < left[2]
    assert right[0] < .65 < .89 < right[2]


def test_archive_detail_shows_the_dialog_title_and_activity(tmp_path):
    import cv2
    import numpy as np
    frame = np.full((480,640,3), (125,130,0), np.uint8)
    cv2.rectangle(frame,(188,142),(451,220),(185,185,185),-1)
    cv2.rectangle(frame,(190,145),(448,160),(140,0,0),-1)
    cv2.putText(frame,"Dialing...",(231,186),cv2.FONT_HERSHEY_SIMPLEX,.4,(15,15,15),1)
    path = tmp_path / "dialer.png"
    assert cv2.imwrite(str(path),frame)
    box = continuity.enclosing_window(str(path),0,[.308,.35,.482,.424])
    assert box and .17 < box[0] < 188/640 and 451/640 < box[2] < .83
    assert box[1] < 142/480 and box[3] > 220/480


def test_credit_starting_one_frame_after_the_ask_carries_the_note(tmp_path):
    from scene.palette import hud
    group = "archive-credit-236"
    words = ["Andrew Lear · Windows 95", "dial-up recreation"]
    layers = [dict(id=f"credit-{i}", k="word", f=[236,313], t=t, text_group=group)
              for i,t in enumerate(words)]
    doc = dict(w=1080,h=1920,fps=30,dur_f=581,items=layers,assets={},voices={})
    result = hud.fineprint(doc,str(tmp_path),dict(id="note",f=[235,269],lines=["Dial-up recreation"]))
    assert result["drop"] == ["credit-0","credit-1"]
    assert [(it["t"],it["f"]) for it in result["items"]] == [(line,[236,313]) for line in words]
