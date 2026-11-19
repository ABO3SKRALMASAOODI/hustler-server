"""Round1 sound-events-missing (20 Nov 2026): a marketing film played its recorded export inside clip shots, and the
export's own picture landings (the trash can, the black hole) never became sound events: 0.96 sounds per 10 s.

An asset may name the landings its own edit makes (``landings``: source seconds). Each one a played window shows is a
landing on the film's clock: a picture is a card (motivated, it covers the frame), a title a display landing. A plain
switch between slices of the same file at that frame gives way to it. Assets without ``landings`` (every customer
film) are unchanged. Signal only: no voice, no model."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from score import sound as SD  # noqa: E402


def _l1(landings=None):
    body = {"kind": "video", "path": "/x/export_body.mp4"}
    if landings is not None:
        body["landings"] = landings
    items = [dict(id="a", k="shot", pic="v00", f=[0, 60], src=[0.0, 2.0]),
             dict(id="b", k="shot", pic="v01", f=[60, 120], src=[10.0, 12.0]),      # plays export 10-12 s
             dict(id="c", k="shot", pic="v01", f=[120, 180], src=[12.0, 14.0]),     # a plain switch to the next slice
             dict(id="d", k="shot", pic="v00", f=[180, 300], src=[4.0, 8.0])]
    return dict(fps=30, dur_f=300, assets={"v00": {"kind": "video", "path": "/x/up.mp4"}, "v01": body}, items=items)


def test_assets_without_landings_are_unchanged():
    assert SD.l1_events(_l1()) == SD.l1_events(_l1([]))
    assert not SD.landing_events(_l1())


def test_export_landings_inside_played_windows_become_motivated_events():
    ev = SD.l1_events(_l1([{"t": 11.0, "kind": "picture"}, {"t": 12.5, "kind": "title"}, {"t": 20.0, "kind": "picture"}]))
    pic = next(e for e in ev if e["f"] == 90)                    # 11.0 s inside b's 10-12 window: frame 60 + 30
    title = next(e for e in ev if e["f"] == 135)                 # 12.5 s inside c's window: frame 120 + 15
    assert pic["kind"] == "card" and pic.get("landing") and SD.motivated(pic)
    assert title["kind"] == "display" and SD.motivated(title)
    assert not any(e.get("landing") and e["f"] > 180 for e in ev)          # 20 s is never shown: no event


def test_a_landing_on_a_switch_between_export_files_replaces_the_plain_picture_event():
    def l1(landings):
        l = _l1()
        l["assets"]["v02"] = {"kind": "video", "path": "/x/export_1.mp4", **({"landings": landings} if landings else {})}
        l["items"][2]["pic"], l["items"][2]["src"] = "v02", [3.0, 5.0]       # the next window is another cut of the export
        return l
    plain = [e for e in SD.l1_events(l1(None)) if e["f"] == 120]
    assert plain and plain[0]["kind"] == "picture" and not SD.motivated(plain[0])
    at = [e for e in SD.l1_events(l1([{"t": 3.0, "kind": "picture"}])) if abs(e["f"] - 120) <= 2]
    assert len(at) == 1 and at[0]["kind"] == "card" and SD.motivated(at[0])


def test_landings_raise_the_motivated_allowance():
    fps, dur = 30, 30.0
    plain = [{"f": int(t * fps), "kind": "picture", "id": str(t)} for t in (2.0, 6.0, 10.0, 14.0, 18.0, 22.0, 26.0)]
    lands = [{"f": int(t * fps), "kind": "card", "photo": True, "area": 1.0, "landing": True, "id": f"l{t}"}
             for t in (4.0, 8.3, 12.5, 15.4, 19.3, 22.7)]
    ctx = {"fps": fps, "dur_f": int(dur * fps), "existing": [], "speech": None, "voice": None, "words": [(0.2, dur - 2.0)],
           "bed": True, "np10": 4.0, "seed": SD.film_seed("landings")}
    _s0, r0 = SD.plan_sounds(plain, ctx)
    s1, r1 = SD.plan_sounds(sorted(plain + lands, key=lambda e: e["f"]), ctx)
    assert r1["allow"] > r0["allow"] and len(s1) > len(_s0)
