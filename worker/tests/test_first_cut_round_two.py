from types import SimpleNamespace

import numpy as np
import pytest

from score import compiler, lower, picture_cuts


def test_wordless_source_selection_ends_with_the_last_authored_picture():
    idx = {"first_film": {"frame": "9:16"}, "words": [], "video": {
        "duration": 166, "width": 720, "height": 1280, "fps": 30}}
    ctx = compiler.Ctx(idx)
    ctx.film = {}
    beat = {"k": "b", "_line": 1, "id": "payoff", "at": "@124..@154",
            "cut": [30, 42, 38], "pic": ["main", "main", "main"]}
    compiler.read_beats(ctx, [beat])
    compiler.build_spine(ctx)
    assert ctx.dur_f == 110
    assert ctx.spine[-1]["src"][1] == pytest.approx(124 + 110 / 30)


def test_dropped_chapters_renumber_repeated_fragments_but_preserve_clock():
    def chapter(n, second):
        return {"t": f"{n:02} MOVE 16", "f": [second, second + 1],
                "chapter": [f"b{n}.counter", "{n:02d} MOVE 16", n]}
    rows = [chapter(3, 1), chapter(3, 2), chapter(5, 4),
            {"t": "16:00", "f": [0, 1]}]
    compiler.number_chapters(rows)
    assert [r["t"] for r in rows] == ["01 MOVE 16", "01 MOVE 16", "02 MOVE 16", "16:00"]


def test_clock_ticks_hold_across_layout_drops(monkeypatch):
    values = [(0, 9, "704"), (18, 32, "1698")]
    words = [dict(k="word", role="big", t=v, id=f"b02.counter.{n}.0",
                  f=[a, b], x=.4, y=.3) for n, (a, b, v) in enumerate(values)]
    def text_item(l1, it, a, b, rep):
        return dict(id="tx." + it["id"], text=it["t"], start=a/30, end=b/30,
                    font_size=.05, x=.4, y=.3)
    monkeypatch.setattr(lower, "text_item", text_item)
    texts = lower.lower_texts(dict(fps=30, dur_f=32, items=words), {})
    assert texts[0]["end"] == texts[1]["start"] == .6


def test_soft_source_intervals_identify_a_baked_dissolve_but_leave_a_hard_cut():
    rng = np.random.default_rng(42)
    a, b = rng.integers(0, 255, size=(2, 40, 70, 3), dtype=np.uint8)
    dissolve = [((1-u)*a + u*b).astype(np.uint8) for u in np.linspace(0, 1, 16)]
    ranges = picture_cuts.soft_transitions([a]*20 + dissolve + [b]*20, 15)
    assert any(start < 1.85 < end for start, end in ranges)
    assert not picture_cuts.soft_transitions([a]*25 + [b]*25, 15)


def test_reply_names_only_titles_measured_on_delivered_frames():
    from reply_truth import first_cut_summary
    reply = first_cut_summary(dict(duration_s=28, width=720, height=1280,
        picture_qc={"visible_titles":["UNDER SIEGE", "sixteen minutes, one base"]},
        shots=42, sfx=9), 166)
    assert "UNDER SIEGE" in reply and "sixteen minutes, one base" in reply
    assert "166 seconds" in reply and "42" not in reply and "sound" not in reply


def test_short_unframed_spine_seam_holds_the_speaker_until_the_page():
    from score import lower
    main = dict(k="shot", pic="main", crop=[.34, 0, .66, 1])
    fitted = [-.1, -1.08, 1.1, 2.08]
    l1 = dict(fps=30, frame=[720,1280], assets={"main":dict(kind="video",
        w=1920,h=1080,path="source.mp4")}, spine=[{"f":[0,60],"src":[0,2]}],
        items=[dict(main,id="a",f=[0,24],src=[0,.8]),
               dict(main,id="seam",f=[24,30],src=[.8,1],crop=fitted),
               dict(k="shot",id="page",pic="plate",f=[30,60],plate={})])
    _, zooms, *_ = lower.lower_pictures(l1, "", SimpleNamespace(locals={},plate=lambda *_:"page"), {})
    assert zooms[-1]["end"] == 1.0 and zooms[-1]["path"][0]["s"] == zooms[0]["path"][-1]["s"]


def test_fitted_cutaway_keeps_blurred_bands_and_follows_reading_camera(tmp_path):
    import subprocess
    import renderer
    def make(path, vf):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", vf, "-an",
                        "-c:v", "libx264", "-threads", "1", str(path)], check=True)
    src, desk, out = (tmp_path / n for n in ("base.mp4", "desk.mp4", "out.mp4"))
    make(src, "color=black:s=320x180:r=30:d=1.5")
    make(desk, "color=blue:s=320x180:r=30:d=1.5,"
         "drawbox=x=60:y=20:w=200:h=140:c=red:t=fill,"
         "drawbox=x=120:y=50:w=80:h=80:c=white:t=fill")
    pts = [dict(f=f, s=.8, cx=.5, cy=.5) for f in (0., 1.)]
    edl = dict(keep=[[0., 1.5]], canvas=dict(width=180,height=320,fps=30),
        frame=dict(ratio="9:16",mode="pad_blur"),
        overlays=[dict(id="desk",asset_key="other",kind="video",fit="source_blur",
                       start=.2,duration_s=1.1,source_start_s=.1)],
        effects=dict(zooms=[dict(id="read",start=.75,end=1.3,mode="path",
                                 strength=.8,path=pts,ease="linear")]))
    renderer.render_edl(edl, dict(words=[],sentences=[],silences=[],video=dict(duration=1.5)),
                        str(src), str(out), str(tmp_path), False,
                        suppress_outro=True,asset_locals={"other":str(desk)})
    frames = np.frombuffer(subprocess.check_output(["ffmpeg","-v","error","-i",str(out),
        "-vf","scale=90:160","-pix_fmt","rgb24","-f","rawvideo","-"]), np.uint8).reshape(-1,160,90,3)
    wide, close = frames[14], frames[31]
    assert wide[:35].std(axis=(0, 1)).mean() > 4  # bands retain the source background
    assert (close.min(axis=2)>210).sum() > 2*(wide.min(axis=2)>210).sum()
