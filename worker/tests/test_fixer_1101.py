"""Daily fixer 1 Nov 2026: the causes behind tickets #62-#66 and #49's
recurrence, each on the production trait that showed it."""
import os
import shutil
import subprocess

import pytest

import ask_ledger
import config
import first_cut
import renderer


def test_a_long_raw_take_with_no_speech_is_not_a_finished_piece():
    # Ticket #63: source 38212, a 104 s vertical 4K concert take, 2 words
    # heard over a 142 bpm beat. It was "kept whole (it already plays as a
    # finished piece over its song)". Short pieces over a song stay whole.
    concert = {"words": [{"w": "да", "t0": 40.0, "t1": 40.3},
                         {"w": "ну", "t0": 71.0, "t1": 71.2}],
               "perception": {"bpm": 142.2, "beats": [0.4, 0.8]}}
    assert not first_cut._speech(concert)
    assert not first_cut._music_piece(concert, 103.556)
    assert not first_cut._music_piece(concert, 103.556, robust=True)
    assert first_cut._music_piece(concert, 32.0)
    assert first_cut._music_piece(concert, first_cut.FINISHED_PIECE_MAX_S)


def test_an_app_end_card_run_is_measured_from_the_edge():
    # Ticket #65: CapCut's end card on source 38233 measured >= 0.99 dark from
    # 13.2 s to the end of a 14.92 s clip (10 fps samples).
    footage = [0.26, 0.31, 0.40, 0.44, 0.58, 0.83]
    card = [0.97, 1.0, 1.0] + [0.99] * 17          # 2.0 s at 10 fps
    assert first_cut.card_run(footage + card, 10.0, from_end=True) == 2.0
    assert first_cut.card_run(card + footage, 10.0, from_end=False) == 2.0
    # a blink of black is not a card; a long black stretch is footage
    assert first_cut.card_run(footage + [1.0] * 3, 10.0, from_end=True) == 0.0
    assert first_cut.card_run(footage + [1.0] * 80, 10.0, from_end=True) == 0.0
    assert first_cut.card_run(footage, 10.0, from_end=True) == 0.0


def test_keeps_on_the_card_are_cut_off():
    # the First cut's montage kept [13.14, 14.74], the card itself
    keep = [[2.08, 3.38], [5.28, 6.58], [7.41, 8.71], [10.6, 11.9],
            [13.14, 14.74]]
    assert first_cut.clip_keeps(keep, 0.0, 12.92) == keep[:4]
    assert first_cut.clip_keeps([[0.0, 14.92]], 0.0, 12.92) == [[0.0, 12.92]]
    assert first_cut.clip_keeps([[0.0, 14.92]], 1.5, 14.92) == [[1.5, 14.92]]


class _Proxy:
    def __init__(self, path):
        self.path = path

    def proxy_path(self):
        return self.path


def _ffmpeg():
    return shutil.which("ffmpeg") and shutil.which("ffprobe")


@pytest.mark.skipif(not _ffmpeg(), reason="needs ffmpeg")
def test_the_card_is_found_on_real_pixels(tmp_path):
    # 6 s of moving footage, then a 2 s black card with a small white logo
    src = str(tmp_path / "capcut.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=310x540:r=30:d=6",
         "-f", "lavfi", "-i", "color=c=black:s=310x540:r=30:d=2",
         "-filter_complex",
         "[1:v]drawbox=x=135:y=250:w=40:h=20:color=white:t=fill[card];"
         "[0:v][card]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-pix_fmt", "yuv420p", src], check=True)
    start, end = first_cut.app_card_edges(_Proxy(src), 8.0)
    assert start == 0.0 and 5.8 <= end <= 6.1
    plain = str(tmp_path / "plain.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=310x540:r=30:d=8", "-pix_fmt", "yuv420p",
                    plain], check=True)
    assert first_cut.app_card_edges(_Proxy(plain), 8.0) == (0.0, 8.0)


def test_a_first_cut_waits_on_its_own_running_render(monkeypatch):
    # Ticket #62: preview 126723 was still rendering when render_preview's
    # 900 s wait ended; the First cut said "I couldn't finish" and the render
    # landed 10 s later.
    calls = []
    answers = ["PREREQUISITE: complete preview for EDL v14 is still running "
               "as job 126723. Call wait_for_job(job_id=126723).",
               "Preview v14 rendered: 31.8s (source 902.4s)."]

    def execute(ctx, name, args):
        calls.append((name, args))
        return answers.pop(0)

    monkeypatch.setattr(first_cut.agent_tools, "execute", execute)
    steps = [{"tool": "render_preview", "ok": True}]
    first = ("PREREQUISITE: complete preview for EDL v14 is still running as "
             "job 126723. Call wait_for_job(job_id=126723).")
    out = first_cut._await_running_render(type("C", (), {})(), steps, first)
    assert out.startswith("Preview v14 rendered")
    assert [a for _n, a in calls] == [{"quality": "approval", "complete": True},
                                      {"quality": "approval", "complete": True}]
    # waiting is not another render
    assert first_cut._renders_done(steps) == 1
    # a finished or failed render is never waited on
    assert first_cut._await_running_render(None, [], "Preview v3 rendered") \
        == "Preview v3 rendered"
    monkeypatch.setattr(config, "FIRST_CUT_RENDER_GRACE_S", 0.0)
    assert first_cut._await_running_render(None, [], first) == first


def test_one_label_does_not_meet_a_kinetic_captions_ask():
    # Tickets #66 / #49: "big kinetic captions" on a silent watch clip was
    # met by a "CITIZEN" label beside the First cut's hook title; the turn
    # ended fulfilled while its reply said no captions were added.
    ask = "Turn this into a punchy 9:16 short with big kinetic captions"
    hook = {"id": "mg1", "template": "hook_title",
            "params": {"text": "Green *glow*"}}
    before = {"captions": None, "texts": [], "motion": [hook]}
    after = {"captions": None, "motion": [hook],
             "texts": [{"id": "tx1", "text": "CITIZEN", "template": "title"}]}
    assert ask_ledger.ledger(ask, before, after) == {"captions": "not_done"}
    assert ask_ledger.unmet(ask_ledger.ledger(ask, before, after)) == ["captions"]
    beats = dict(after, texts=[{"text": t} for t in ("CITIZEN", "ECO-DRIVE",
                                                      "SOLAR")])
    assert ask_ledger.ledger(ask, before, beats) == {"captions": "done"}
    spoken = dict(after, captions={"mode": "from_transcript"})
    assert ask_ledger.ledger(ask, before, spoken) == {"captions": "done"}


def test_the_no_speech_captions_rule_is_in_the_first_requests_skill():
    path = os.path.join(os.path.dirname(first_cut.__file__), "skills",
                        "first-requests.md")
    text = open(path, encoding="utf-8").read()
    assert "never \"nothing to\n   caption\"" in text
    assert "never thinned \"for density\"" in text
    assert "app card" in text


@pytest.mark.skipif(not _ffmpeg(), reason="needs ffmpeg")
def test_a_plain_whole_take_preview_is_scaled_to_the_preview_size(tmp_path):
    # Ticket #64: approval preview 126719 of a 2160x3840 whole-take EDL was
    # encoded at 2160x3840 (448 MB, 412 s); every box approval of a large
    # source was native size. The cheap graph never scaled and the trailing
    # preview scale read the already-capped height.
    import media
    src = str(tmp_path / "src4k.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=2160x3840:r=30:d=1", "-f", "lavfi", "-i",
         "sine=f=440:d=1", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src], check=True)
    index = {"words": [], "video": {"width": 2160, "height": 3840,
                                    "fps": 30.0, "duration": 1.0}}
    for quality, want in (("approval", (720, 1280)), ("draft", (270, 480))):
        token = renderer._PREVIEW_QUALITY.set(quality)
        try:
            out = str(tmp_path / f"{quality}.mp4")
            renderer.render_edl({"version": 1, "keep": [[0.0, 1.0]]}, index,
                                src, out, str(tmp_path), preview=True)
        finally:
            renderer._PREVIEW_QUALITY.reset(token)
        info = media.probe(out)
        assert (info["width"], info["height"]) == want, quality
