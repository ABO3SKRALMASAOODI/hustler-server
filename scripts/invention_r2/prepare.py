"""Cut a study's word-aligned reel from the original recording.

Usage: python prepare.py DEVICE SOURCE TAKE.mov [--apple-av1]
The optional Apple hardware decoder is for the AV1 Altman recording.
"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--apple-av1", action="store_true")
    args = parser.parse_args()
    cue = json.loads(Path(__file__).with_name("cues.json").read_text())[args.device]
    ff = [os.environ.get("FFMPEG_BIN", "ffmpeg"), "-nostdin", "-v", "error", "-y"]
    decode = ["-hwaccel", "videotoolbox", "-c:v", "av1"] if args.apple_av1 else ["-threads", "2"]
    encode = (
        ["-c:v", "h264_videotoolbox", "-b:v", "28M"]
        if args.apple_av1
        else ["-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-crf", "14"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as tmp:
        folder = Path(tmp)
        listing = []
        for i, (start, dur) in enumerate(cue["cuts"]):
            part = folder / f"{i}.mov"
            subprocess.run(
                [
                    *ff,
                    *decode,
                    "-ss",
                    str(start),
                    "-i",
                    str(args.source),
                    "-frames:v",
                    str(round(24 * dur)),
                    "-t",
                    str(dur),
                    "-vf",
                    "fps=24,scale=1280:720:flags=lanczos,setsar=1",
                    "-af",
                    "aresample=48000,apad",
                    *encode,
                    "-c:a",
                    "pcm_s16le",
                    str(part),
                ],
                check=True,
            )
            listing.append(f"file '{part.name}'\nduration {dur}\n")
        manifest = folder / "cuts.txt"
        manifest.write_text("".join(listing))
        result = folder / "result.mov"
        subprocess.run(
            [*ff, "-f", "concat", "-safe", "1", "-i", str(manifest), "-c", "copy", str(result)], check=True
        )
        result.replace(args.output)


if __name__ == "__main__":
    main()
