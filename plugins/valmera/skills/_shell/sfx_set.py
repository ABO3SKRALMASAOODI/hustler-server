"""The workflow films' sound-effect set: build it, measure it, describe it (lane sfx, 20 Nov 2026).

The owner, 20 Nov, on the night's films: the SFX were "weird sounds ... not related at all", they "need to be high
quality". The product library had filled its generic classes with slices of things that are not editing effects (a
clock-tick loop, a waterphone, a cassette case, a rope whip, cloth), so a whoosh or a hit slot fell through to them.
This tool builds a separate local set of purpose-made editing effects for the workflow films:

    python sfx_set.py build [--out DIR] [--cache DIR]

- sfx_set_sources.json names every row: where it comes from (a Mixkit sound effect, free licence, or one of the
  product library's own recordings), the class it answers (whoosh, hit, impact, riser, click), the family of an object
  recording (paper, shutter, type, glitch: it answers only a picture that shows the object) and what it is. A model
  read each source title once to write that; nothing here reads a title.
- each Mixkit file is cut to its one gesture the way an editor trims it (leading silence off; a whoosh to its audible
  tail; a hit or an impact to a short ring that fades by itself; a riser to its climb, cut dead at the top), faded,
  brought to a -3 dBFS peak and written as 48 kHz FLAC. The library's own files are copied unchanged (their ids, shas
  and files stay, so a workflow that names one still plays it).
- every row is measured by signal on the written file: length, attack, audible tail, end level, noise floor, clipped
  tops, the top of its spectrum and the steepness there (an encoder wall), centroid, low share, tonality (the share of
  energy in its five strongest partials, and the pitch of the strongest), its 2-8 kHz level and the product's own
  timing and level fields (peak_s, hit_s, max_s, hit_lufs, the shape on the sound engine's ruler).
- a row whose measures show a defect a phone would play (a clipped source, an encoder wall under 16 kHz, a noise floor
  under a short sound, a tail that ends abruptly after the cut) is left out of the set and listed with the reason in
  set.json, so no film ever chooses it.

The set is written to .valmera/models/sfx_ref (manifest.json, the FLAC files, set.json, LICENSES.md), this Mac only:
audio never goes into the repository. chain.use_sound_set() points the workflow runs at it.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brand  # noqa: E402

V = "/Users/masaoodi/Documents/Valmera"
OUT = f"{V}/.valmera/models/sfx_ref"
SOURCES = os.path.join(HERE, "sfx_set_sources.json")
SR = 48000
PEAK_DBFS = -3.0
MIXKIT_LICENCE = "Mixkit Sound Effects Free License"
MIXKIT_LICENCE_URL = "https://mixkit.co/license/#sfxFree"

# how an editor trims each class (seconds)
CUT = {
    "whoosh": dict(pre=0.004, max_len=1.25, tail_db=-42.0, fade=0.04),
    "hit": dict(pre=0.003, ring=0.45, fade=0.12),
    "impact": dict(pre=0.003, lead=0.6, ring=1.4, fade_from=0.35),
    "riser": dict(climb=2.6, after=0.03, fade_in=0.35),
    "click": dict(pre=0.002, ring=0.22, fade=0.03),
}
WALL_HZ = 16000.0          # an encoder wall under this is audible on a phone
WALL_DB = 25.0             # a drop this steep over 1.2 kHz is a wall, not a natural roll-off
FLOOR_DB = -40.0           # a short sound's quietest tenth this near its peak: hiss or room under it
END_DB = -38.0             # after the cut, the last 10 ms this near the peak: a tail chopped off


def ff():
    return brand.FF


def pcm(path, ch=2):
    r = subprocess.run([ff(), "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0", "-ac", str(ch), "-ar", str(SR),
                        "-f", "f32le", "pipe:1"], capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"cannot read {os.path.basename(path)}: {r.stderr.decode()[-200:]}")
    return np.frombuffer(r.stdout, np.float32).reshape(-1, ch).copy()


def write_flac(x, out):
    subprocess.run([ff(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac",
                    str(x.shape[1]), "-i", "pipe:0", "-c:a", "flac", "-sample_fmt", "s16", out],
                   input=x.astype(np.float32).tobytes(), check=True, timeout=300)


def env_db(x, win=0.010, hop=0.005):
    """10 ms RMS level every 5 ms, dB (mono)."""
    w, h = int(win * SR), int(hop * SR)
    if len(x) <= w:
        return np.array([20 * np.log10(np.sqrt((x.astype(np.float64) ** 2).mean()) + 1e-12)])
    c = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    k = np.arange(0, len(x) - w, h)
    return 10 * np.log10((c[k + w] - c[k]) / w + 1e-18)


def _fade(n, kind="lin"):
    t = np.linspace(0.0, 1.0, max(n, 1), dtype=np.float32)
    return t ** 2 if kind == "sq" else t


def cut(x, cls):
    """The one gesture of a source file, trimmed the way an editor trims it. x: stereo float."""
    m = x.mean(axis=1)
    e = env_db(m)
    top = float(e.max())
    ipk = int(np.argmax(e))
    above = np.flatnonzero(e >= top - 40)
    on = int(above[0])
    t_on, t_pk = on * 0.005, ipk * 0.005
    c = CUT[cls]
    if cls == "riser":
        a = max(t_on, t_pk - c["climb"])
        b = t_pk + c["after"]
        y = x[int(a * SR):int(b * SR)].copy()
        fi = int(c["fade_in"] * SR) if a > t_on + 0.05 else int(0.004 * SR)
        y[:fi] *= _fade(fi, "sq")[:, None]
        fo = int(0.008 * SR)
        y[-fo:] *= _fade(fo)[::-1][:, None]
        return y, dict(cut_s=[round(a, 3), round(b, 3)])
    if cls == "impact":
        a = max(t_on - c["pre"], t_pk - c["lead"], 0.0)
        quiet = np.flatnonzero(e[ipk:] < top - 42)
        b = min(t_pk + c["ring"], (ipk + int(quiet[0])) * 0.005 if len(quiet) else len(m) / SR, len(m) / SR)
        y = x[int(a * SR):int(b * SR)].copy()
        if a > t_on + 0.02:                               # cut into the whoosh-in: eased in
            fi = int(0.06 * SR)
            y[:fi] *= _fade(fi, "sq")[:, None]
        else:
            fi = int(0.002 * SR)
            y[:fi] *= _fade(fi)[:, None]
        ring = len(y) - int((t_pk - a) * SR)
        f0 = int((t_pk - a) * SR) + min(int(c["fade_from"] * SR), int(0.6 * ring))
        if f0 < len(y):                                   # the ring falls away by itself, as a trimmed hit does
            n = len(y) - f0
            y[f0:] *= (np.exp(-np.linspace(0.0, 5.0, n)) * np.linspace(1.0, 0.0, n) ** 0.5).astype(np.float32)[:, None]
        return y, dict(cut_s=[round(a, 3), round(b, 3)])
    if cls in ("hit", "click"):
        a = max(t_on - c["pre"], 0.0)
        quiet = np.flatnonzero(e[ipk:] < top - 42)
        b = min(t_pk + c["ring"], (ipk + int(quiet[0])) * 0.005 + 0.01 if len(quiet) else len(m) / SR, len(m) / SR)
        y = x[int(a * SR):int(b * SR)].copy()
        fi = int(0.002 * SR)
        y[:fi] *= _fade(fi)[:, None]
        ring = len(y) - int((t_pk - a) * SR)
        fo = min(max(int(c["fade"] * SR), int(0.4 * ring)), len(y) // 2)
        y[-fo:] *= _fade(fo, "sq")[::-1][:, None]
        return y, dict(cut_s=[round(a, 3), round(b, 3)])
    # whoosh: onset to where the tail sinks under tail_db, at most max_len; a fade over the last part
    a = max(t_on - c["pre"], 0.0)
    quiet = np.flatnonzero(e[ipk:] < top + c["tail_db"])
    b = (ipk + int(quiet[0])) * 0.005 + 0.02 if len(quiet) else len(m) / SR
    b = min(b, a + c["max_len"], len(m) / SR)
    y = x[int(a * SR):int(b * SR)].copy()
    fi = int(0.003 * SR)
    y[:fi] *= _fade(fi)[:, None]
    tail = len(y) - int((t_pk - a) * SR)
    fo = max(int(c["fade"] * SR), int(0.35 * tail)) if b >= a + c["max_len"] - 1e-3 else int(c["fade"] * SR)
    fo = min(fo, len(y) // 2)
    y[-fo:] *= _fade(fo, "sq")[::-1][:, None]
    return y, dict(cut_s=[round(a, 3), round(b, 3)])


def normalise(y):
    pk = float(np.abs(y).max())
    if pk < 1e-5:
        raise RuntimeError("no sound")
    return (y * (10 ** (PEAK_DBFS / 20) / pk)).astype(np.float32)


def band_level(m, lo, hi, win=0.1):
    """The loudest `win` seconds of the lo-hi band, RMS dBFS (mono)."""
    X = np.fft.rfft(m.astype(np.float64))
    hz = np.fft.rfftfreq(len(m), 1.0 / SR)
    X[(hz < lo) | (hz >= hi)] = 0
    b = np.fft.irfft(X, len(m))
    w = int(win * SR)
    if len(b) <= w:
        return round(20 * np.log10(np.sqrt((b ** 2).mean()) + 1e-12), 1)
    c = np.concatenate([[0.0], np.cumsum(b ** 2)])
    return round(10 * np.log10(float(((c[w:] - c[:-w]) / w).max()) + 1e-18), 1)


def signal(path, src_path=None):
    """The signal measures of one recording (and of its source file, when given)."""
    st = pcm(path, 2)
    m = st.mean(axis=1)
    e = env_db(m)
    top = float(e.max())
    ipk = int(np.argmax(e))
    above = np.flatnonzero(e >= top - 40)
    on, last = int(above[0]), int(above[-1])
    n = 8192
    mm = np.pad(m, (0, max(0, n - len(m))))
    frames, lv = [], []
    win = np.hanning(n)
    for i in range(0, len(mm) - n + 1, n // 4):
        f = mm[i:i + n]
        lv.append(10 * np.log10((f ** 2).mean() + 1e-18))
        frames.append(np.abs(np.fft.rfft(f * win)) ** 2)
    lv = np.array(lv)
    P = np.mean(np.array(frames)[lv >= lv.max() - 30], axis=0)
    hz = np.fft.rfftfreq(n, 1.0 / SR)
    Pdb = 10 * np.log10(P + 1e-18)
    ref = Pdb[(hz >= 200) & (hz <= 6000)].max()
    over = np.flatnonzero(Pdb >= ref - 60)
    top_hz = float(hz[over[-1]]) if len(over) else 0.0
    i0, i1 = np.searchsorted(hz, max(0.0, top_hz - 600)), np.searchsorted(hz, min(hz[-1], top_hz + 600))
    wall = float(Pdb[i0:i0 + 20].mean() - Pdb[max(i1 - 20, i0 + 1):i1].mean()) if i1 - i0 > 40 else 0.0
    tot = float(P[hz >= 30].sum()) or 1e-18
    Q = P.copy()
    share, pitch = 0.0, None
    for _ in range(5):
        j = int(np.argmax(Q[3:])) + 3
        pitch = float(hz[j]) if pitch is None else pitch
        share += float(Q[max(0, j - 2):j + 3].sum())
        Q[max(0, j - 2):j + 3] = 0
    out = dict(len_s=round(len(m) / SR, 3), attack_s=round((ipk - on) * 0.005, 3), tail_s=round((last - ipk) * 0.005, 3),
               end_db=round(float(e[-1] - top), 1), top_hz=round(top_hz), wall_db=round(wall, 1),
               centroid_hz=round(float((hz * P).sum() / max(P.sum(), 1e-18))), low_share=round(float(P[(hz >= 20) & (hz < 150)].sum()) / tot, 3),
               tonal=round(share / max(float(P[3:].sum()), 1e-18), 3), pitch_hz=round(pitch or 0.0, 1),
               hf_db=band_level(m, 2000.0, 8000.0), full_db=band_level(m, 20.0, 20000.0))
    if src_path:
        s = pcm(src_path, 2)
        pk = float(np.abs(s).max())
        a = np.abs(s).max(axis=1) >= 0.999 * max(pk, 1e-9)
        runs = 0
        if pk >= 0.99:
            d = np.diff(np.concatenate([[0], a.astype(int), [0]]))
            runs = int(((np.flatnonzero(d == -1) - np.flatnonzero(d == 1)) >= 3).sum())
        es = env_db(s.mean(axis=1))
        out.update(src_clip_runs=runs, src_floor_db=round(float(np.percentile(es, 10) - es.max()), 1))
    return out


def defects(row, sig):
    """Why a recording is left out of the set ([] when it is in). Signals only."""
    why = []
    if sig.get("src_clip_runs"):
        why.append(f"its source clips ({sig['src_clip_runs']} clipped tops)")
    if sig["top_hz"] < WALL_HZ and sig["wall_db"] >= WALL_DB:
        why.append(f"an encoder wall at {sig['top_hz']} Hz ({sig['wall_db']} dB)")
    if row["src"].startswith("mixkit:") and row["class"] in ("hit", "click", "impact") and sig.get("src_floor_db") is not None \
            and sig["src_floor_db"] > FLOOR_DB:              # a source file padded with room: its quietest tenth is the noise
        why.append(f"a noise floor {sig['src_floor_db']} dB under its peak")
    if row["class"] != "riser" and sig["end_db"] > END_DB and not row["src"].startswith("lib:"):
        why.append(f"its tail is cut off ({sig['end_db']} dB at the end)")
    return why


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def _fetch(url, dest):
    tmp = dest + ".part"
    r = subprocess.run(["curl", "-sfL", "-A", "Mozilla/5.0", "-o", tmp, url], timeout=120)
    if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
        raise RuntimeError(f"cannot fetch {url}")
    os.replace(tmp, dest)


def build(out=OUT, cache=None, sources=SOURCES, worker=None):
    """Write the set to `out`. Returns the set report."""
    worker = worker or brand.worker_dir()
    if worker not in sys.path:
        sys.path.insert(0, worker)
    os.environ.setdefault("FFMPEG_BIN", ff())
    import sound_approval as SA
    import sfx_mix
    SA.FFMPEG = ff()
    spec = json.load(open(sources))
    lib_dir = os.path.join(worker, "sound_library")
    lib = {r["id"]: r for r in json.load(open(os.path.join(lib_dir, "manifest.json")))}
    own_cache = cache is None
    cache = cache or tempfile.mkdtemp(prefix="sfx_src_")
    stage = out + ".part"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    rows, dropped = [], []
    try:
        for r in spec["rows"]:
            kind, _, ref = r["src"].partition(":")
            dst = os.path.join(stage, f"{r['id']}.flac")
            if kind == "mixkit":
                src = os.path.join(cache, f"{ref}.wav")
                url = spec["mixkit"].format(n=ref)
                if not os.path.exists(src):
                    _fetch(url, src)
                y, how = cut(pcm(src, 2), r["class"])
                write_flac(normalise(y), dst)
                trail = dict(title=r["what"], author="Mixkit", source_url=url, license=MIXKIT_LICENCE,
                             license_url=MIXKIT_LICENCE_URL, source_sha256=_sha(src), **how)
            else:
                x = lib[ref]
                src = os.path.join(lib_dir, x["file"])
                shutil.copy(src, dst)
                trail = {k: x.get(k) for k in ("title", "author", "source_url", "license") if x.get(k)}
                how = {}
            sig = signal(dst, src)
            why = defects(r, sig)
            if why:
                dropped.append(dict(id=r["id"], src=r["src"], why=why, signal=sig))
                os.remove(dst)
                continue
            peak, hit_s, max_s, dur = SA._tail_s(dst, sweep=r["class"] in ("whoosh", "riser"))
            hit, hp = sfx_mix.measure_recording(dst, ffmpeg=ff())
            desc = SA.describe_recording(dst)
            row = dict(id=r["id"], role=r.get("role") or (r.get("family") or r["class"]), file=os.path.basename(dst),
                       sha=_sha(dst), use=r["what"], family=r.get("family"),
                       reference=dict(**{"class": r["class"]}, by="model read of the source title, sfx_set_sources.json"),
                       shape={k: desc[k] for k in ("dur_s", "attack_s", "decay_s", "bands", "flat", "low", "sweep", "centroid_hz")} if desc else None,
                       peak_s=peak, hit_s=hit_s, max_s=max_s, duration_s=dur, hit_lufs=hit, hit_lufs_hp150=hp,
                       gain_db=round(-20.0 - 6.0 - hit, 1), screen=sig, **trail)
            if kind == "lib":
                row["role"] = lib[ref].get("role") or row["role"]     # a workflow that names it keeps its role
            if kind == "lib" and lib[ref].get("align"):
                row["align"] = lib[ref]["align"]
            if kind == "lib" and lib[ref].get("gain_db") is not None:
                row["gain_db"] = lib[ref]["gain_db"]
            rows.append({k: v for k, v in row.items() if v is not None})
        report = dict(about=spec["about"], built_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), rows=len(rows),
                      by_class={c: sum(1 for x in rows if x["reference"]["class"] == c and not x.get("family")) for c in CUT},
                      families={f: sum(1 for x in rows if x.get("family") == f) for f in ("paper", "shutter", "type", "glitch")},
                      dropped=dropped, left_out=spec.get("left_out"))
        with open(os.path.join(stage, "manifest.json"), "w") as f:
            json.dump(rows, f, indent=1)
            f.write("\n")
        with open(os.path.join(stage, "set.json"), "w") as f:
            json.dump(report, f, indent=1)
        with open(os.path.join(stage, "LICENSES.md"), "w") as f:
            f.write("# Workflow SFX set: licences\n\nMixkit rows (`mx_*`): " + MIXKIT_LICENCE + " (" + MIXKIT_LICENCE_URL +
                    "): free for commercial use in a film, no attribution owed. Every other row is a copy of the product "
                    "library's own recording under the licence its row names (CC0 1.0 or the Sonniss GameAudioGDC bundle "
                    "licence, worker/sound_library/LICENSES.md).\n\n| id | licence | source |\n|---|---|---|\n")
            for x in rows:
                f.write(f"| {x['id']} | {x.get('license', '')} | {x.get('source_url', '')} |\n")
        old = out + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(out):
            os.replace(out, old)
        os.replace(stage, out)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if own_cache:
            shutil.rmtree(cache, ignore_errors=True)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sfx_set")
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--cache", help="a folder of already-fetched Mixkit WAVs (<n>.wav); kept")
    ap.add_argument("--worker")
    a = ap.parse_args(argv)
    rep = build(a.out, a.cache, worker=a.worker)
    print(json.dumps({k: rep[k] for k in ("rows", "by_class", "families")}, indent=1))
    for d in rep["dropped"]:
        print("left out:", d["id"], "; ".join(d["why"]))


if __name__ == "__main__":
    main()
