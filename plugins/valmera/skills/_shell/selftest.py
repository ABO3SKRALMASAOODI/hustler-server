"""The shell's own test workflow: every stage writes real files, nothing calls a model.

Each stage leaves scratch in the run folder (ctx.tmp, supply/) the way the real chain does, so the
cleanup test sees a run that made a mess and has to clear it. The render is a short lavfi film;
the brand finish and the checks are the real ones.
"""
import json
import os
import subprocess

import brand
import chain


def _junk(path, mb):
    with open(path, "wb") as f:
        f.write(os.urandom(mb * 1024 * 1024))


def ideas(ctx):
    _junk(os.path.join(ctx.tmp, "transcript.bin"), 3)
    return dict(source=dict(path=ctx.source, kind="test"), chosen=None,
                ideas=[dict(id="i1", title="Self test film", t0=0.0, t1=2.0, styles=[s["id"] for s in ctx.styles_allowed()])])


def script(ctx, ideas_out):
    idea = ideas_out["ideas"][0]
    style = ctx.styles_allowed()[0]
    ideas_out["chosen"] = idea
    return dict(title=idea["title"], style=style, window=[idea["t0"], idea["t1"]], source=ctx.source,
                direction="test", idea=idea, spend=[dict(stage="script", provider="none", usd=0.0)])


def supply(ctx, script_out):
    chain.link(ctx)
    _junk(os.path.join(ctx.supply, "source.bin"), 4)
    os.makedirs(os.path.join(ctx.supply, "bin"), exist_ok=True)
    _junk(os.path.join(ctx.supply, "bin", "0.bin"), 2)
    return dict(window=script_out["window"])


def score(ctx, script_out):
    l1 = os.path.join(ctx.supply, "l1.json")
    json.dump(dict(l1=1, sound=[], assets={}), open(l1, "w"))
    return dict(l1=l1, spend=[])


def render(ctx, script_out, sc):
    out = os.path.join(ctx.supply, "film_engine.mp4")
    if not os.path.exists(out):
        subprocess.run([brand.FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=2",
                        "-f", "lavfi", "-i", "anoisesrc=d=2:c=pink:a=0.25", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out + ".part.mp4"], check=True, capture_output=True)
        os.replace(out + ".part.mp4", out)
    _junk(os.path.join(ctx.supply, "engine_scratch.bin"), 2)
    return dict(engine="test", film=out)
