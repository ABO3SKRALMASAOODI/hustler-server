"""worker/scene: the engine. Turns a scene (the Score's L1) into frames on the GPU, and draws what
today's renderer cannot. It reads no model: the import test fails the build if it ever does.

    import scene
    ok, why = scene.eligible(l1)                      # False for anything unknown, before any work
    log = scene.render(l1, work, out_mp4, tier="final", audio=audio_m4a_or_True_or_False, strict=True)
    arrays = scene.stills(l1, work, [0, 33, 58])      # {frame: HxWx4 uint8}, in this process
    plan = scene.compile_scene(l1, work); plan.save(dir)   # plan.json, curves.bin, atlas.png

``render`` checks eligibility here, then compiles and draws in a child process (``python -m scene.run``) under a watchdog:
no frame and no reader progress for ``stall_s`` kills it. Every failure is one exception,
``scene.SceneFault`` with a ``code``; the caller renders the same EDL on today's renderer and files a
ticket. Nothing in this package switches itself on.

The GL context comes from ``VALMERA_SCENE_GL`` (auto: EGL on Linux, the default context elsewhere).

Tiers (``scene/run.py`` TIERS): ``preview``, ``final``, ``master``, and ``inter`` for a film that another
encode follows (the product's brand finish re-encodes every engine film): nearer the drawn frames
than ``final`` for a third of the encoder's CPU, in a file nobody keeps.

On a software rasteriser (the box: llvmpipe on 4 vCPU) the same frames are drawn by fewer and smaller
passes, and nothing waits on anything else: see ``runtime.Runtime.frame``, ``settle`` and
``soft_sprite``, ``reader.Reader.decoder_for``, and ``run.Writer`` and ``run.Sound``.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from .faults import SceneFault
from .plan import eligible, scene_of

VERSION = "scene-2"
WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

__all__ = ["VERSION", "SceneFault", "eligible", "scene_of", "compile_scene", "render", "stills"]


def compile_scene(doc, work, **kw):
    """EDL plus scene to a Plan (scene/compile.py). No GL, no model."""
    from . import compile as _c
    return _c.compile(doc, work, **kw)


def stills(doc, work, frames, **kw):
    """Chosen frames as arrays, drawn in this process. For checks, contact sheets and tests."""
    from . import run as _r
    return _r.stills(compile_scene(doc, work, segment=kw.pop("segment", False), **kw), frames)


def render(doc, work, out, tier="final", audio=True, strict=False, budget_s=None, cost_tier=None, stall_s=20.0,
           timeout_s=None, cache=None, use_cache=True, python=None, env=None, frames_log=False, size=None):
    """Compile, then draw in a child process. Returns the render log. Raises SceneFault.

    audio: True mixes the plan's own spine and sounds, a path muxes that file, False leaves it silent.
    strict: refuse to draw around a missing picture, font or glyph (what a customer film wants).
    budget_s: degrade the plan until its estimated draw time fits; never a failure.
    size: (W, H) to draw the same scene at another size of the same shape; everything in the plan
          that is in pixels follows, so a caller never rescales a plan by hand.
    """
    ok, why = eligible(doc)
    if not ok:
        raise SceneFault("ineligible", "; ".join(why[:6]), reasons=why)
    tmp = tempfile.mkdtemp(prefix="scene_job_")
    prog = os.path.join(tmp, "progress.json")
    errf = None
    try:
        with open(os.path.join(tmp, "l1.json"), "w", encoding="utf-8") as f:
            json.dump(scene_of(doc), f)
        cmd = [python or sys.executable, "-m", "scene.run", "render", "--l1", os.path.join(tmp, "l1.json"), "--work", work, "--out", out,
               "--tier", tier, "--progress", prog]
        if strict:
            cmd.append("--strict")
        if budget_s is not None:
            cmd += ["--budget", str(float(budget_s))]
        if cost_tier:
            cmd += ["--cost-tier", cost_tier]
        if size:
            cmd += ["--size", f"{int(size[0])}x{int(size[1])}"]
        if isinstance(audio, str):
            cmd += ["--audio", audio]
        elif not audio:
            cmd.append("--no-audio")
        if cache:
            cmd += ["--cache", cache]
        if not use_cache:
            cmd.append("--no-cache")
        if frames_log:
            cmd.append("--frames-log")
        e = dict(os.environ if env is None else env)
        e["PYTHONPATH"] = WORKER + os.pathsep + e.get("PYTHONPATH", "")
        errf = open(os.path.join(tmp, "stderr.txt"), "w+")
        child = subprocess.Popen(cmd, cwd=WORKER, env=e, stdout=subprocess.DEVNULL, stderr=errf)
        last, moved, t0 = None, time.monotonic(), time.monotonic()
        while child.poll() is None:
            time.sleep(0.25)
            try:
                now = open(prog).read()
            except OSError:
                now = None
            if now != last:
                last, moved = now, time.monotonic()
            stalled = time.monotonic() - moved > stall_s
            late = timeout_s is not None and time.monotonic() - t0 > timeout_s
            if stalled or late:
                child.kill()
                child.wait()
                raise SceneFault("stall", f"no progress for {stall_s:.0f} s" if stalled else f"over {timeout_s:.0f} s", progress=last)
        if child.returncode:
            try:
                f = json.load(open(prog + ".fault"))
                raise SceneFault(f.get("code", "crash"), f.get("detail", ""))
            except (OSError, ValueError):
                errf.seek(0)
                raise SceneFault("crash", f"exit {child.returncode}: {errf.read()[-600:]}")
        return json.load(open(out + ".log.json"))
    finally:
        try:
            errf.close()
        except Exception:      # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)
