"""The brand finish: the product's own watermark and end card on a Mac-rendered programme.

Nothing is redrawn here. The mark's pixels and place come from the worker's renderer
(watermark_image, watermark_geometry) and the end card's join from outro_card_parts, so a
film from a workflow closes exactly as a Valmera export does. The end card and the mark are
untouchable: check() measures both on the delivered file.
"""
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V = "/Users/masaoodi/Documents/Valmera"
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = os.environ.get("SHELL_FFMPEG", BIN + "/ffmpeg"), os.environ.get("SHELL_FFPROBE", BIN + "/ffprobe")


def worker_dir():
    """The worker beside this plugin (repo checkout), else the synced main checkout."""
    if os.environ.get("VALMERA_WORKER"):
        return os.environ["VALMERA_WORKER"]
    d = HERE
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.exists(os.path.join(d, "worker", "renderer.py")):
            return os.path.join(d, "worker")
    return f"{V}/worktrees/skill-main/worker"


def _renderer():
    w = worker_dir()
    if w not in sys.path:
        sys.path.insert(0, w)
    import config  # noqa: F401
    import renderer
    return renderer


def probe(path):
    r = subprocess.run([FP, "-v", "error", "-count_packets", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,nb_read_packets:format=duration",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    n, den = v["r_frame_rate"].split("/")
    return dict(w=int(v["width"]), h=int(v["height"]), fps=float(n) / float(den), frames=int(v.get("nb_read_packets") or 0),
                dur=float(d["format"]["duration"]), audio=any(s["codec_type"] == "audio" for s in d["streams"]))


def finish(programme, out, workdir):
    """programme (the engine's film) -> out, with the mark on every programme frame and the end card joined."""
    R = _renderer()
    import config
    p = probe(programme)
    W, H, fps = p["w"], p["h"], round(p["fps"])
    prog_s = p["frames"] / fps if p["frames"] else p["dur"]
    outro_s = float(config.OUTRO_DURATION_S)
    card = R.endcard_path()
    mark = R.watermark_image(workdir, W, H)
    if not card or not mark:
        raise RuntimeError("brand asset missing: the end card and the mark are not optional here")
    x, y = R.watermark_geometry(W, H)["image"][:2]
    parts, j = R.outro_card_parts(2, W, H, fps, outro_s, "", motion=not card.lower().endswith(R.IMAGE_EXTS), program_s=prog_s)
    n_prog = p["frames"] or math.ceil(prog_s * fps - 1e-6)
    t_join = max(0.0, (n_prog - j - 0.25) / fps)
    fade = float(getattr(config, "OUTRO_AUDIO_TAIL_FADE_S", 0.25))
    g = [f"[0:v]setsar=1,format=yuv420p[pv]",
         f"[pv][1:v]overlay={x}:{y}:format=auto:shortest=1[vm]"] + parts + [
        "[ohead]format=yuva420p[oheadp]",
        f"[oheadp]settb=AVTB,setpts=PTS-STARTPTS+{t_join:.6f}/TB[oheadt]",
        "[vm][oheadt]overlay=0:0:format=auto:eof_action=repeat[vjoin]",
        f"anullsrc=r=48000:cl=stereo:d={outro_s:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo[osil]"]
    if p["audio"]:
        g.append(f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"afade=t=out:st={max(0.0, prog_s - fade):.3f}:d={fade:.3f},asetpts=N/SR/TB[aprogc]")
    else:
        g.append(f"anullsrc=r=48000:cl=stereo:d={prog_s:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo[aprogc]")
    g.append("[vjoin][aprogc][ovid][osil]concat=n=2:v=1:a=1[vout][aout]")
    cmd = [FF, "-v", "error", "-y", "-i", programme, "-loop", "1", "-r", str(fps), "-i", mark]
    cmd += R._endcard_input_args(card, outro_s, fps)
    cmd += ["-filter_complex", ";".join(g), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return dict(programme_s=round(prog_s, 3), outro_s=outro_s, join_frames=j, mark_xy=[x, y], card=os.path.basename(card))


def _frame(path, t, W, H):
    import numpy as np
    r = subprocess.run([FF, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={W}:{H}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.uint8)[: W * H * 3].reshape(H, W, 3).astype(np.float32)


def check(programme, final, workdir):
    """Measured on the files: length, the mark on the programme, the end card at the close, the sound level."""
    import numpy as np
    R = _renderer()
    import config
    p, f = probe(programme), probe(final)
    W, H = f["w"], f["h"]
    prog_s = p["frames"] / round(p["fps"]) if p["frames"] else p["dur"]
    want = prog_s + float(config.OUTRO_DURATION_S)
    out = dict(final_s=round(f["dur"], 3), want_s=round(want, 3), length_ok=abs(f["dur"] - want) <= 0.1)
    ix0, iy0, iw, ih = R.watermark_geometry(W, H)["image"]
    t = min(prog_s * 0.5, max(0.5, prog_s - 1.0))
    a, b = _frame(programme, t, W, H), _frame(final, t, W, H)
    roi = np.abs(a[iy0:iy0 + ih, ix0:ix0 + iw] - b[iy0:iy0 + ih, ix0:ix0 + iw]).mean()
    rest = np.abs(a - b).mean()
    out.update(mark_roi_diff=round(float(roi), 2), outside_diff=round(float(rest), 2), mark_ok=bool(roi > 4 * max(rest, 0.5)))
    card = R.endcard_path()
    c = _frame(card, 2.5, W, H)
    e = _frame(final, f["dur"] - 2.5, W, H)
    cd = float(np.abs(c - e).mean())
    out.update(endcard_diff=round(cd, 2), endcard_ok=cd < 6.0)
    r = subprocess.run([FF, "-v", "info", "-i", final, "-t", f"{prog_s:.3f}", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    lufs = None
    for line in r.stderr.splitlines()[::-1]:
        s = line.strip()
        if s.startswith("I:") and "LUFS" in s:
            lufs = float(s.split()[1])
            break
    out.update(lufs=lufs, loud_ok=lufs is not None and -18.0 <= lufs <= -10.0)
    out["ok"] = all(out[k] for k in ("length_ok", "mark_ok", "endcard_ok", "loud_ok"))
    return out
