from scene.palette import rhythm


def test_human_blockers_snap_on_frame_121_changes_the_live_picture(tmp_path):
    # The real Director input at “blockers”: a fitted 1920x1080 interview over a plate.
    crop = [.187755, 0., .784655, 1.]
    speaker = dict(id="cd000000.000", k="card", f=[0, 214], pic="main", source_frame=True,
                   x=.5, y=.45, w=1., h=.53008, z=1, src=[0., 7.033333333333333], crop=crop)
    plate = dict(id="pg000000.000", k="shot", pic="plate", f=[0, 214])
    scene = dict(frame=[1080, 1920], fps=30, dur_f=214, items=[plate, speaker],
                 assets={"main": dict(kind="video", w=1920, h=1080, dur=44.667, path="source.mp4")})
    ask = dict(id="dr0", dev="snap", beat=0, f=121, size=1.2)
    result = rhythm.snap(scene, tmp_path, ask)
    assert "skip" not in result, result
    assert result["log"]["shot"] == speaker["id"] and result["events"][0]["f"] == 121
    camera, = result["items"]
    assert camera["f"] == speaker["f"] and camera["src"] == speaker["src"]
    assert camera["crop"][120] == crop
    widths = [c[2]-c[0] for c in camera["crop"]]
    assert widths[121] < widths[126] < widths[120]
    # At a picture cut the half-open span belongs to the incoming panel, even
    # with the outgoing item later in the list and a transparent card above it.
    incoming = dict(speaker, id="incoming", f=[121, 214])
    hidden = dict(incoming, id="hidden", z=4, op=0.)
    scene["items"] = [plate, incoming, hidden, dict(speaker, f=[0, 121])]
    assert rhythm.snap(scene, tmp_path, ask)["log"]["shot"] == "incoming"


def test_snap_fits_the_moving_arms_and_glasses_in_the_live_panel(tmp_path):
    crop = [0., .015, 1., 1.]
    panel = dict(id="live", k="card", pic="main", f=[131,295], z=1, x=.5, y=.4,
                 w=.82, h=.26, s=1, crop=crop, src=[4.27,9.73], source_frame=True,
                 fit_motion=True)
    scene = dict(frame=[1080,1920], fps=30, dur_f=295, items=[panel],
                 assets={"main":dict(kind="video", w=1920,h=1080,dur=24.2,path="source.mp4")})
    result = rhythm.snap(scene, tmp_path, dict(f=162, size=1.2))
    camera, = result["items"]
    assert camera["crop"] == crop
    assert camera["src"] == panel["src"]
    assert camera["s"][30] == 1 < camera["s"][31]
    assert camera["s"][31] > camera["s"][36] and max(camera["s"])*panel["w"] <= 1.00001


def test_snap_at_full_width_keeps_the_source_and_has_a_visible_landing(tmp_path):
    panel = dict(id="live", k="card", pic="main", f=[0,20], x=.5, y=.5, w=1., h=.32,
                 crop=[0.,0.,1.,1.], source_frame=True, fit_motion=True)
    scene = dict(frame=[1080,1920], fps=30, dur_f=20, items=[panel], assets={})
    for cue in (0,10):
        camera, = rhythm.snap(scene,tmp_path,dict(f=cue,size=1.2))["items"]
        scales = camera["s"]
        assert max(scales) == 1 and min(scales) < .9
        assert camera["crop"] == panel["crop"] and scales[cue+4] == 1
