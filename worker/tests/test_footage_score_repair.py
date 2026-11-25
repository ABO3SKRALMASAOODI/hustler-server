import shutil
import subprocess

from footage import shots, run


def test_reopened_original_reuses_exact_frame_without_redecoding(tmp_path, monkeypatch):
    media = tmp_path/"one.mp4"
    subprocess.run([shots.fetch.ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10",
                    "-t", "2", "-c:v", "libx264", str(media)], check=True)
    copy = tmp_path/"same.mp4"
    shutil.copyfile(media, copy)
    monkeypatch.setenv("FOOTAGE_KEEP_SOURCES", str(tmp_path/"sources"))
    decode = subprocess.run
    calls = []
    def count(cmd, **kw):
        calls.append(cmd)
        return decode(cmd, **kw)
    monkeypatch.setattr(subprocess, "run", count)
    first = shots.frames(str(media), [.5], tmp_path/"pass1", w=320)
    again = shots.frames(str(copy), [.5, 1.], tmp_path/"pass2", w=320)
    assert len(calls) == 2 and open(first[.5], "rb").read() == open(again[.5], "rb").read()
    shots.frames(str(copy), [.5], tmp_path/"pass3", w=768)
    assert len(calls) == 3


def test_gap_scout_rereads_a_documented_window_for_the_actual_detail():
    src = dict(url="archive", shots=[(i,i+1) for i in range(40)])
    target = dict(i=7, repair_windows=[[1032,1065]], dur=1.1)
    hints = [dict(url="archive", subject="TPS-L2", frame_at=34.5, grade=5),
             dict(url="archive", time_range=[34,35], frame_at=34.5, motion_p90=3),
             dict(url="archive", subject="TPS-L2", frame_at=23.5, grade=4)]
    ts = run.repair_times(src, [], hints, {"TPS-L2"}, [target])
    assert 23.5 in ts and 34.5 not in ts
    assert 1 < len(ts) <= 8
