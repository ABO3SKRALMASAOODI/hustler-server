"""E2: the render process. ``python -m scene.run`` draws one plan and exits; a crash here cannot
take a worker down, and the parent (``scene.render``) watches its progress file.

    python -m scene.run compile --l1 l1.json --work DIR --plan PLAN_DIR
    python -m scene.run render  --plan PLAN_DIR --work DIR --out film.mp4 [--tier final|preview|master]
    python -m scene.run render  --l1 l1.json   --work DIR --out film.mp4 [--frames 0-90] [--no-audio]
    python -m scene.run still   --l1 l1.json   --work DIR --frames 33,58 --dir stills/
    python -m scene.run check   --l1 l1.json   --work DIR --out film.mp4

A whole film is drawn as segments (DESIGN 4.10): each is encoded on its own with a closed GOP,
checked, kept by hash, and the film is their concat with stream copy. A stopped render leaves its
finished segments, and the next run starts from them. ffmpeg decodes, encodes and carries the sound.
"""
import argparse
import collections
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import numpy as np

from . import audio as AU
from . import compile as C
from . import reader as R
from . import segments as S
from .faults import SceneFault
from .plan import AR, Plan
from .runtime import Runtime

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
FP = os.environ.get("FFPROBE_BIN", "ffprobe")
# DESIGN 4.5. master is the slice's own setting, for a workflow final rendered on the Mac.
TIERS = {"preview": dict(preset="superfast", crf=23, maxrate="3M", bufsize="6M"),
         "final": dict(preset="veryfast", crf=18, maxrate="12M", bufsize="24M"),
         "master": dict(preset="medium", crf=18, maxrate="12M", bufsize="24M")}


class Engine:
    def __init__(self, plan, drop=()):
        self.plan = plan
        self.rt = Runtime(plan)
        self.rt.drop = set(drop)
        self.active = []
        self._at = None

    def advance(self, f):
        """Active items on frame f. Cheap when frames are visited in rising order."""
        p = self.plan
        if f == self._at:
            return self.active
        if self._at is not None and f == self._at + 1:
            self.active = [i for i in self.active if i["f"][1] > f] + p.starts.get(f, [])
        else:
            self.active = p.active(f)
        self._at = f
        return self.active

    def start(self, frames):
        """Begin reading ahead for these frames, in this order."""
        frames = list(frames)
        d = self.plan.demand(frames)
        self.rt.reader = R.Reader(self.plan.assets, d, order=frames)
        self.rt.reader.start()

    def close(self):
        if self.rt.reader:
            self.rt.reader.stop()
        self.rt.release()

    def stills(self, frames):
        """Render chosen frames to arrays, in order, with no encoder."""
        frames = sorted(frames)
        self.start(frames)
        out = {}
        try:
            for f in frames:
                out[f] = self.rt.frame_array(f, self.advance(f)).copy()
                self.rt.reader.release(f)
        finally:
            self.rt.reader.stop()
        return out


def enc_tag(tier):
    t = TIERS[tier]
    return f"x264-{t['preset']}-crf{t['crf']}-{t['maxrate']}"


def encoder(out, W, H, fps, tier="final"):
    """Colour is explicit at the way out: full-range RGB to BT.709 limited, tagged."""
    t = TIERS[tier]
    vf = ("scale=in_range=pc:out_range=tv:out_color_matrix=bt709:out_h_chr_pos=0:out_v_chr_pos=128:"
          "flags=lanczos+accurate_rnd+full_chroma_int+full_chroma_inp,format=yuv420p")
    cmd = [FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-", "-vf", vf,
           "-c:v", "libx264", "-preset", t["preset"], "-crf", str(t["crf"]), "-maxrate", t["maxrate"], "-bufsize", t["bufsize"],
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart", "-an", out]
    try:
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise SceneFault("encode", f"cannot start ffmpeg: {e}")


class Progress:
    """What the watchdog reads: frames drawn and frames decoded. Written whole, at most 4 times a second."""

    def __init__(self, path):
        self.path, self.last, self.frames, self.stage = path, 0.0, 0, "start"

    def say(self, stage=None, frames=None, reader=None, force=False):
        if stage:
            self.stage = stage
        if frames is not None:
            self.frames = frames
        now = time.monotonic()
        if not self.path or (not force and now - self.last < 0.25):
            return
        self.last = now
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"stage": self.stage, "frames": self.frames, "decoded": reader.decoded if reader else 0, "t": time.time()}, f)
        os.replace(tmp, self.path)


def _draw(e, frames, video, tier, prog, flog=None, done0=0):
    """Draw these frames, in this order, into one encoded file. Returns the mean level of what was
    drawn, 0-255, so a black segment can be told from a dark plan."""
    plan, rt = e.plan, e.rt
    enc = encoder(video, plan.W, plan.H, plan.fps, tier)
    lum, n = 0.0, 0
    try:
        for f in frames:
            act = e.advance(f)
            rt.frame(f, act)
            buf = rt.read()
            ts = time.perf_counter()
            try:
                enc.stdin.write(buf)
            except (BrokenPipeError, OSError):
                raise SceneFault("encode", (enc.stderr.read() or b"").decode("utf-8", "replace")[-400:])
            rt.tick("encode_wait", ts)
            rt.reader.release(f)
            lum += float(np.frombuffer(buf, np.uint8)[::1009].mean())
            n += 1
            prog.say("draw", done0 + n, rt.reader)
            if flog:
                shot = next((i for i in act if i["k"] == "shot"), None)
                flog.write(json.dumps({"f": f, "shot": shot and shot["id"], "card": [i["id"] for i in act if i["k"] == "card"],
                                       "flash": [i["id"] for i in act if i["k"] == "flash"],
                                       "join": [i["id"] for i in act if i["k"] == "join"],
                                       "word": sum(1 for i in act if i["k"] == "word")}, separators=(",", ":")) + "\n")
    finally:
        try:
            enc.stdin.close()
        except OSError:
            pass
    err = (enc.stderr.read() or b"").decode("utf-8", "replace")
    if enc.wait():
        raise SceneFault("encode", err[-400:])
    return lum / max(n, 1)


def _dark_plan(plan, a, b):
    """True when the plan itself is dark over a..b: only plates, or shots with no source to compare."""
    return all(i.get("pic") == "plate" for i in plan.items if i["k"] == "shot" and i["f"][0] < b and i["f"][1] > a)


def render_plan(plan, out, tier="final", frames=None, audio=True, cache=None, progress=None, frames_log=False, use_cache=True):
    """Draw a plan to a file. Returns the log, also written to out + '.log.json'.

    frames: draw only these (a partial render: one file, no sound, no segments).
    audio: True mixes the plan's own spine and sounds; a path muxes that file; False leaves the film silent.
    """
    t0 = time.time()
    prog = Progress(progress)
    prog.say("gl", 0, force=True)
    e = Engine(plan)                                     # every shader compiles here, in the first second
    rt = e.rt
    prog.say("ready", 0, force=True)
    partial = frames is not None
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="scene_", dir=os.path.dirname(os.path.abspath(out)))
    segs_log, rep = [], {}
    flog = open(out + ".frames.jsonl", "w") if frames_log else None
    try:
        video = os.path.join(tmp, "v.mp4")
        if partial or not plan.segments:
            frames = list(range(plan.dur)) if frames is None else list(frames)
            e.start(frames)
            try:
                _draw(e, frames, video, tier, prog, flog)
            finally:
                rt.reader.stop()
            n_frames = len(frames)
        else:
            cache = cache or S.cache_dir()
            tag = enc_tag(tier)
            todo, paths = [], {}
            for s in plan.segments:
                p = S.seg_path(cache, s["sha"], tag)
                paths[s["i"]] = p
                if not (use_cache and S.have(p)):
                    todo.append(s)
            order = S.order(todo)
            e.start([f for s in order for f in range(*s["f"])])
            done = 0
            try:
                for s in order:
                    a, b = s["f"]
                    part = os.path.join(tmp, f"seg{s['i']:04d}.mp4")
                    rt.src_sum, rt.src_n = 0.0, 0
                    ts = time.time()
                    mean = _draw(e, range(a, b), part, tier, prog, flog, done)
                    done += b - a
                    S.verify(part, b - a, plan.fps, mean, (rt.src_sum / rt.src_n) if rt.src_n else None, _dark_plan(plan, a, b))
                    S.keep(part, paths[s["i"]])
                    segs_log.append({"i": s["i"], "f": [a, b], "s": round(time.time() - ts, 3), "sha": s["sha"]})
            finally:
                rt.reader.stop()
            prog.say("join", done, rt.reader, force=True)
            S.concat([paths[s["i"]] for s in plan.segments], video)
            n_frames, _ = S.count(video)
            if n_frames != plan.dur:
                raise SceneFault("segment", f"joined film has {n_frames} frames, planned {plan.dur}")
            S.sweep(cache)
        t_pic = time.time() - t0
        if audio and not partial:
            prog.say("audio", force=True)
            if isinstance(audio, str):
                AU.mux(video, audio, out)
                rep = {"from": os.path.basename(audio)}
            else:
                a = os.path.join(tmp, "a.m4a")
                rep = AU.write_audio(plan, a, tmp)
                AU.mux(video, a, out)
        else:
            shutil.move(video, out)
    finally:
        if flog:
            flog.close()
        shutil.rmtree(tmp, ignore_errors=True)
    rd = rt.reader
    log = dict(frames=n_frames, fps=plan.fps, size=[plan.W, plan.H], gl=rt.gl, gl_version=rt.gl_version, engine=C.ENGINE_VERSION,
               shaders=plan.doc.get("shaders"), plan=plan.sha, tier=tier, seconds=round(time.time() - t0, 2), picture_seconds=round(t_pic, 2),
               stage_ms={k: round(1000 * v / max(rt.n[k], 1), 3) for k, v in rt.t.items()}, stage_frames=dict(rt.n),
               decoded=rd.decoded if rd else 0, seeks=rd.seeks if rd else 0, sprites=len(plan.sprites),
               segments=len(plan.segments), segments_drawn=len(segs_log), segment_log=segs_log, audio=rep,
               degraded=plan.doc.get("degraded") or [], cost_s=plan.doc.get("cost_s"), warnings=plan.warn)
    with open(out + ".log.json", "w") as f:
        json.dump(log, f, indent=1)
    prog.say("done", n_frames, force=True)
    e.close()
    return log


def stills(plan, frames, drop=()):
    e = Engine(plan, drop=drop)
    try:
        return e.stills(frames)
    finally:
        e.close()


def check_film(plan_of, film, short=3):
    """The gate, by frame diff on the encoded file. frame(f) is pure, so the same film drawn without
    its flashes is the baseline: each short flash must differ from it inside its rect on exactly its
    planned frames, and must not on the frames either side. ``plan_of(items=None)`` compiles the
    film, or the film with a different item list."""
    plan = plan_of()
    W, H = plan.W, plan.H
    want = [i for i in plan.items if i["k"] == "flash" and i["f"][1] - i["f"][0] <= short and i.get("mode") != "pool"]
    salvo = [i for i in plan.items if i["k"] == "shot" and i["f"][1] - i["f"][0] <= short and i["f"][0] > 0 and i["f"][1] < plan.dur]
    frames = set()
    for it in want + salvo:
        frames.update(x for x in range(it["f"][0] - 1, it["f"][1] + 1) if 0 <= x < plan.dur)
    ref = stills(plan, sorted(frames), drop=("flash",))
    drawn = stills(plan_of(), sorted(frames))
    info = R.probe(film)
    a = dict(info, abs=film, rate=plan.fps)
    d = R.Decoder(a, 0)
    got, n = {}, 0
    while True:
        b = d.next()
        if b is None:
            break
        if n in frames:
            got[n] = np.frombuffer(b, np.uint8).reshape(info["h"], info["w"], 4)
        n += 1
    d.close()
    cover = collections.defaultdict(list)
    for it in plan.items:
        if it["k"] == "flash":
            for x in range(it["f"][0], it["f"][1]):
                cover[x].append(it)

    def diff(f, r, base_=None):
        base_ = ref if base_ is None else base_
        x0, y0, x1, y1 = int(r[0] * W), int(r[1] * H), int(math.ceil(r[2] * W)), int(math.ceil(r[3] * H))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return float(np.abs(got[f][y0:y1, x0:x1, :3].astype(np.int16) - base_[f][y0:y1, x0:x1, :3].astype(np.int16)).mean())

    def overlaps(a_, b_):
        return a_[0] < b_[2] and b_[0] < a_[2] and a_[1] < b_[3] and b_[1] < a_[3]

    def rect_of(i, x):
        return AR(i.get("rect"), x, i["f"][0]) or (0, 0, 1, 1)

    def alone(it):
        """The film without this one flash: a flash on top of another can only be judged against this."""
        return stills(plan_of(drop_id=it["id"]), list(range(it["f"][0], it["f"][1])))
    rows, misses, stacked = [], [], 0
    for it in want:
        a0, b0 = it["f"]
        r0 = rect_of(it, a0)
        under = [o for x in range(a0, b0) for o in cover.get(x, []) if o is not it and overlaps(rect_of(o, x), rect_of(it, x))]
        base_ = alone(it) if under else None
        stacked += bool(under)
        ins = [round(diff(x, rect_of(it, x), base_), 2) for x in range(a0, b0)]
        outs = []
        for x in (a0 - 1, b0):
            if x in got and not any(overlaps(rect_of(o, x), r0) for o in cover.get(x, [])):
                outs.append(round(diff(x, r0), 2))
        floor = max(outs) if outs else 2.5
        ok = min(ins) >= max(3 * floor, 3.0)
        if not ok:
            misses.append(dict(id=it["id"], f=it["f"], mode=it.get("mode"), inside=ins, outside=outs))
        rows.append(ok)
    shots_ok, shot_misses, too_alike = 0, [], 0
    for it in salvo:
        a0, b0 = it["f"]
        good = True
        for x in range(a0, b0):
            own = float(np.abs(got[x][..., :3].astype(np.int16) - drawn[x][..., :3].astype(np.int16)).mean())
            cross = [float(np.abs(got[x][..., :3].astype(np.int16) - drawn[y][..., :3].astype(np.int16)).mean()) for y in (a0 - 1, b0)]
            if min(cross) < 3.0:          # a dark locked-off take: the neighbours are the same picture
                too_alike += 1
                continue
            if own * 2 >= min(cross):
                good = False
                shot_misses.append(dict(id=it["id"], f=x, own=round(own, 2), cross=[round(c, 2) for c in cross]))
        shots_ok += good
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-v", "error", "-i", film, "-f", "null", "-"], capture_output=True, text=True, timeout=900)
    q = subprocess.run([FP, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", film], capture_output=True, text=True, timeout=60)
    dur = {x["codec_type"]: float(x.get("duration") or 0) for x in json.loads(q.stdout or "{}").get("streams", [])}
    return dict(frames_in_file=n, frames_planned=plan.dur, short_flashes=len(want), stacked=stacked, on_exact_frames=sum(rows), misses=misses[:20],
                short_shots=len(salvo), short_shots_on_exact_frames=shots_ok, short_frames_too_alike_to_tell=too_alike, shot_misses=shot_misses[:20],
                decode_errors=r.stderr.strip()[:300], video_s=dur.get("video"), audio_s=dur.get("audio"))


def _frames(spec):
    if not spec:
        return None
    fs = []
    for part in spec.split(","):
        if "-" in part:
            x, y = part.split("-")
            fs += list(range(int(x), int(y)))
        else:
            fs.append(int(part))
    return fs


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scene.run")
    ap.add_argument("cmd", choices=["compile", "render", "still", "check"])
    ap.add_argument("--l1")
    ap.add_argument("--plan")
    ap.add_argument("--work", required=True)
    ap.add_argument("--out")
    ap.add_argument("--dir")
    ap.add_argument("--frames")
    ap.add_argument("--tier", default="final", choices=sorted(TIERS))
    ap.add_argument("--audio")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-segments", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--cost-tier", default="llvmpipe" if sys.platform.startswith("linux") else "mac")
    ap.add_argument("--cache")
    ap.add_argument("--progress")
    ap.add_argument("--frames-log", action="store_true")
    a = ap.parse_args(argv)
    if sys.platform.startswith("linux") and not os.environ.get("MESA_SHADER_CACHE_DIR"):
        try:                                   # llvmpipe compiles each shader once per cache, not once per render
            os.environ["MESA_SHADER_CACHE_DIR"] = os.path.join(a.cache or S.cache_dir(), "mesa")
            os.makedirs(os.environ["MESA_SHADER_CACHE_DIR"], exist_ok=True)
        except OSError:
            pass
    fault_path = (a.progress + ".fault") if a.progress else None
    prog0 = Progress(a.progress)
    try:
        doc = json.load(open(a.l1, encoding="utf-8")) if a.l1 else None

        def plan_of(drop_id=None, segment=True):
            if doc is None:
                return Plan.load(a.plan, a.work)
            d = doc if drop_id is None else dict(doc, items=[i for i in doc["items"] if i.get("id") != drop_id])
            prog0.say("compile", force=True)
            return C.compile(d, a.work, segment=segment and not a.no_segments, strict=a.strict, budget_s=a.budget, tier=a.cost_tier,
                             tick=lambda: prog0.say("compile", prog0.frames - 1))
        if a.cmd == "compile":
            p = plan_of()
            p.save(a.plan)
            print(json.dumps(dict(plan=p.sha, dir=a.plan, segments=len(p.segments), cost_s=p.doc.get("cost_s"), warnings=p.warn)))
        elif a.cmd == "render":
            fs = _frames(a.frames)
            log = render_plan(plan_of(), a.out, tier=a.tier, frames=fs, audio=(a.audio or (not a.no_audio)), cache=a.cache,
                              progress=a.progress, frames_log=a.frames_log, use_cache=not a.no_cache)
            print(json.dumps(log))
        elif a.cmd == "check":
            print(json.dumps(check_film(lambda drop_id=None: plan_of(drop_id, segment=True), a.out)))
        else:
            from PIL import Image
            os.makedirs(a.dir, exist_ok=True)
            p = plan_of()
            for f, arr in stills(p, _frames(a.frames)).items():
                Image.fromarray(arr[..., :3]).save(os.path.join(a.dir, f"f{f:05d}.png"))
            print(json.dumps(dict(warnings=p.warn)))
        return 0
    except SceneFault as e:
        if fault_path:
            with open(fault_path, "w") as f:
                json.dump(e.as_dict(), f)
        print(json.dumps({"fault": e.as_dict()}), file=sys.stderr)
        return 3
    except Exception as e:      # noqa: BLE001 - the parent turns any other death into SceneFault('crash')
        traceback.print_exc()
        if fault_path:
            with open(fault_path, "w") as f:
                json.dump({"code": "crash", "detail": f"{type(e).__name__}: {e}"[:600]}, f)
        return 1


if __name__ == "__main__":
    sys.exit(main())
