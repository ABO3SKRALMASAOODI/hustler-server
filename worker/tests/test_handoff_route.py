from scene.palette import type as typography


def test_handoff_retains_each_station_once_and_moves_on_spoken_cues():
    steps = [(30,"Product"),(55,"Engineering"),(75,"Marketing"),(97,"Sales")]
    out = typography.handoff_route("route", (20,120), steps, width=1080,height=1920,
                                   voice=dict(font="InterDisplay-Bold.ttf",px=48,track=0),
                                   ink="#FFFFFF",accent="#FFCC00")
    labels = [it for it in out["items"] if it["k"]=="word"]
    assert [it["t"] for it in labels] == [label for _,label in steps]
    assert all(it["f"] == [20,120] for it in labels)
    assert len({(it["x"],it["y"]) for it in labels}) == 4
    assert [it["f"][0] for it in out["items"] if it.get("handoff_marker")] == [30,55,75,97]
