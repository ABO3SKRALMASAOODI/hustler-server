#!/usr/bin/env python3
"""Render a motion template (or authored HTML) over footage for review.

  python3 worker/tools/motion_preview.py --template hook_title \
      --params '{"text":"..."}' --duration 3 --bg clip.mp4 --bg-start 4 \
      --out /tmp/review/hook   [--size 1080x1920] [--fps 30] [--html file.html]

Writes <out>.mp4 (composited, with the template's sound cues mixed in when
--sfx is given) and <out>_sheet.jpg (frames at --sheet-times, default ten
evenly spaced). Without --bg the background is a neutral dark gradient.
Used by template authors and reviewers; not part of the render path.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import motion_engine  # noqa: E402
import motion_templates  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="html")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--html", help="file with an authored composition (template=html)")
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--bg", help="background video")
    ap.add_argument("--bg-start", type=float, default=0.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheet-times", default=None, help="comma list of seconds")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--box", default=None, help="x0,y0,x1,y1 fractions")
    ap.add_argument("--layer-only", action="store_true", help="skip compositing")
    a = ap.parse_args()
    W, H = [int(v) for v in a.size.lower().split("x")]
    params = json.loads(a.params)
    html = open(a.html).read() if a.html else None
    spec = motion_templates.spec(a.template)
    params = motion_templates.check_params(a.template, params, html=html)
    dur = a.duration or float(spec.get("duration") or 3.0)
    item = {"id": "preview", "template": a.template, "start": 0.0, "end": dur,
            "params": params, "html": html,
            "box": [float(v) for v in a.box.split(",")] if a.box else None}
    job = motion_templates.build_job(item, W, H, a.fps)
    out_dir = os.path.dirname(os.path.abspath(a.out)) or "."
    os.makedirs(out_dir, exist_ok=True)
    clip = motion_engine.render_jobs([job], out_dir, pages=1)[0]
    print(json.dumps({"clip": clip.path, "box": [clip.x, clip.y, clip.w, clip.h],
                      "frames": clip.frames, "captured": clip.captured,
                      "seconds": round(clip.seconds, 2), "empty": clip.empty}))
    if clip.empty or a.layer_only:
        return
    ff = motion_engine._ffmpeg()
    if a.bg:
        bg = ["-ss", f"{a.bg_start:.3f}", "-t", f"{dur:.3f}", "-i", a.bg]
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0E0E10,setsar=1,fps={a.fps}[b]")
    else:
        bg = ["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
              f"gradients=s={W}x{H}:c0=0x1b2330:c1=0x07080a:x0=0:y0=0:x1={W}:y1={H}:d={dur}:r={a.fps}"]
        base = "[0:v]setsar=1[b]"
    graph = (f"{base};[1:v]setpts=PTS-STARTPTS,format=rgba[m];"
             f"[b][m]overlay={clip.x}:{clip.y}:format=auto:eof_action=pass,format=yuv420p[v]")
    mp4 = a.out + ".mp4"
    subprocess.run([ff, "-v", "error", "-y", *bg, "-i", clip.path, "-filter_complex", graph,
                    "-map", "[v]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "20", "-an", mp4], check=True)
    if a.sheet_times:
        times = [float(t) for t in a.sheet_times.split(",")]
    else:
        n = 10
        times = [round(dur * (i + 0.5) / n, 3) for i in range(n)]
    sel = "+".join(f"eq(n\\,{int(round(t * a.fps))})" for t in times)
    rows = (len(times) + a.cols - 1) // a.cols
    tw = 360 if W <= H else 480
    subprocess.run([ff, "-v", "error", "-y", "-i", mp4, "-vf",
                    f"select='{sel}',scale={tw}:-2,"
                    f"drawtext=text='%{{pts\\:hms}}':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.5,"
                    f"tile={a.cols}x{rows}:padding=4:color=0x333333",
                    "-frames:v", "1", "-fps_mode", "passthrough", a.out + "_sheet.jpg"], check=False)
    print(json.dumps({"mp4": mp4, "sheet": a.out + "_sheet.jpg"}))


if __name__ == "__main__":
    main()
