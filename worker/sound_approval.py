"""The sound desk: how a recording joins the library (ROADMAP 4F; the 0C queue; owner decision 13 Nov 2026).

Two doors, and neither is a tool: no agent, MCP client or department verb can reach this module (tests pin that
no registered tool imports or wraps it).

  1. The reference match (owner, 13 Nov 2026: "use the references as a judge ... same type of SFX as the
     references", no owner audition). A recording joins when its signal matches a class of sound the reference
     films place: its shape on the ruler of score/sound_signal (length, attack, tail, the seven-band spectrum,
     noise against tone, sweep) lies within the measured limit of that class's reference moments
     (sound_library/reference_moments.json: 1,699 placed sounds of the 132 reference films), it can be set at the
     level the references give that class against the voice, it is a lossless recording that never clips, and its
     licence allows use inside rendered films. The manifest row records "approved by reference match, owner
     decision 13 Nov 2026" with the reference moments it matched (film and second). Nothing is synthesised and
     nothing here reads a title or a tag.

  2. The owner's ear, for a sound that matches no reference class (a customer asked for something the library
     lacks: sound_requests). Approving takes the owner's own typed attestation that they listened. No code path
     writes, simulates or defaults that line.

    python -m sound_approval stage <request id> <file> --source <url> --license <licence> --author <name> [--title ...]
    python -m sound_approval match <file>                    (the reference match of a file, printed; joins nothing)
    python -m sound_approval approve-by-reference <request id> <candidate> --role <role> --use "<when to use it>"
    python -m sound_approval approve <request id> <candidate> --role <role> --use "..."   (asks the owner to type the line)
    python -m sound_approval approve-many <approved.json>   (an audition page's list; one typed attestation)
    python -m sound_approval decline <request id> [--note ...]

``stage`` copies a candidate into the audition folder (outside the library), converts it to FLAC 48 kHz and
measures it; either door then moves it into worker/sound_library/ with a manifest row and its licence trail.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

AUDITION_DIR = os.environ.get(
    "VALMERA_SOUND_AUDITION",
    os.path.expanduser("~/Documents/Valmera/.valmera/sound_audition"))
LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sound_library")
# The library's licence policy: licences that allow a recording inside a rendered film, commercially, with no
# attribution owed on screen. The text of each is kept beside the library (sound_library/licences/).
LICENCES = {
    "CC0 1.0": {"text": None, "url": "https://creativecommons.org/publicdomain/zero/1.0/"},
    "Sonniss GameAudioGDC": {"text": "licences/sonniss_gameaudiogdc_bundle_licence.txt", "url": "https://sonniss.com/gdc-bundle-license/"},
}
LOSSLESS = ("flac", "alac", "wavpack", "ape", "tta")          # and any pcm_* codec
MIN_RATE = 44100
REFS_PATH = os.path.join(LIB_DIR, "reference_moments.json")
REFERENCE_APPROVAL = "approved by reference match, owner decision 13 Nov 2026"
MATCH_MOMENTS = 5               # the reference moments a row records (one per film, nearest first)
VOICE_LUFS = -20.0              # the voice every placed sound is set against (dialogue_level.TARGET_LUFS)
GAIN_MIN, GAIN_MAX = -36.0, -2.0  # score/sound.py's gain range: a recording must reach its class's level inside it
CLIP_RUN = 8                    # this many full-scale samples in a row is a clipped recording
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN") or (FFMPEG[:-6] + "ffprobe" if FFMPEG.endswith("ffmpeg") else "ffprobe")
_ID = re.compile(r"^[a-z0-9_]{2,40}$")


class ApprovalError(Exception):
    pass


def attestation(rid):
    """The exact line the owner types: approval is a person's act."""
    return f"I listened to {rid} and approve it"


def _canon(licence):
    t = str(licence or "").strip().lower()
    if t in ("cc0", "cc0 1.0", "cc0-1.0", "public domain dedication"):
        return "CC0 1.0"
    if t in ("sonniss gameaudiogdc", "sonniss gdc", "sonniss #gameaudiogdc", "sonniss gdc bundle"):
        return "Sonniss GameAudioGDC"
    return None


def _probe(path):
    """(codec, sample rate, bits) of a file's first audio stream, or (None, 0, 0)."""
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=codec_name,sample_rate,bits_per_raw_sample,bits_per_sample", "-of", "json", path],
                           capture_output=True, text=True, timeout=60)
        st = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        bits = int(st.get("bits_per_raw_sample") or st.get("bits_per_sample") or 0)
        return st.get("codec_name"), int(st.get("sample_rate") or 0), bits
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, 0, 0


def lossless(codec):
    return bool(codec) and (str(codec).startswith("pcm_") or codec in LOSSLESS)


def _pcm(path, ch=2, rate=48000):
    import numpy as np
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-vn",
                        "-ac", str(ch), "-ar", str(rate), "-f", "f32le", "pipe:1"], capture_output=True, timeout=600)
    return np.frombuffer(r.stdout, np.float32).reshape(-1, ch).copy()


def _write_flac(x, out, rate=48000):
    subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(rate),
                    "-ac", str(x.shape[1]), "-i", "pipe:0", "-c:a", "flac", "-sample_fmt", "s16", out],
                   input=x.astype("float32").tobytes(), check=True, timeout=600)


def stage(rid, path, *, source_url, licence, author, title=None,
          audition_dir=None, cut=None, trail=None, fade=None):
    """Copy a candidate recording into the audition folder; returns its candidate id. The licence must be one the
    library's policy names (LICENCES); the file is converted to FLAC 48 kHz like every library recording.

    cut=(t0, t1): the one sound of a longer source file (a library file often holds several takes). The cut is
    faded (2 ms in, 30 ms out; fade=(in_s, out_s) for the last seconds of a long climb, which an editor fades in)
    and brought to a -3 dBFS peak like the rest of the library; nothing else is done to it. trail: extra fields
    for the licence trail (the source file's name and hash, the bundle)."""
    lic = _canon(licence)
    if lic not in LICENCES:
        raise ApprovalError("the library takes recordings under " + " or ".join(sorted(LICENCES)) + " only")
    if not source_url:
        raise ApprovalError("a candidate needs its source page (the trail)")
    d = os.path.join(audition_dir or AUDITION_DIR, rid)
    os.makedirs(d, exist_ok=True)
    n = len([f for f in os.listdir(d) if f.endswith(".flac")]) + 1
    cid = f"c{n}"
    out = os.path.join(d, f"{cid}.flac")
    codec, rate, bits = _probe(path)
    meta = {"request": rid, "candidate": cid, "source_url": source_url,
            "license": lic, "author": author, "title": title,
            "source_codec": codec, "source_rate": rate, "source_bits": bits,
            "staged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if cut is not None:
        import numpy as np
        t0, t1 = float(cut[0]), float(cut[1])
        x = _pcm(path)[int(round(t0 * 48000)):int(round(t1 * 48000))]
        if len(x) < 480:
            raise ApprovalError("the cut holds no sound")
        meta["clipped"] = bool(_clipped(x))
        if not fade:
            # a short sound ends where its audible tail does (a cap that close to the hit cannot be written as max_s)
            pk, end = _audible_end(x.mean(axis=1))
            if end is not None and end <= pk + 0.25 and end < len(x) / 48000 - 0.03:
                x = x[:int(end * 48000)]
                t1 = t0 + len(x) / 48000
        fin, fout = (float(fade[0]), float(fade[1])) if fade else (0.002, 0.03)
        fi, fo = min(int(fin * 48000), len(x) // 2), min(int(fout * 48000), len(x) // 4)
        x[:fi] *= (np.linspace(0.0, 1.0, fi, dtype=np.float32) ** (2 if fin > 0.05 else 1))[:, None]
        x[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)[:, None]
        pk = float(np.abs(x).max())
        if pk < 1e-4:
            raise ApprovalError("the cut holds no sound")
        x *= (10 ** (-3.0 / 20)) / pk
        _write_flac(x, out)
        meta["cut_s"] = [round(t0, 3), round(t1, 3)]
        if fade:
            meta["fade_s"] = [round(fin, 3), round(fout, 3)]
    else:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", path, "-ac", "2",
                        "-ar", "48000", "-c:a", "flac", out], check=True)
        try:
            meta["clipped"] = bool(_clipped(_pcm(out)))
        except Exception:  # noqa: BLE001 - read again at the reference door
            pass
    if trail:
        meta.update({k: v for k, v in trail.items() if k not in meta})
    try:
        import sfx_mix
        hit, hp = sfx_mix.measure_recording(out, ffmpeg=FFMPEG)
        meta.update(hit_lufs=hit, hit_lufs_hp150=hp)
    except Exception:  # noqa: BLE001 — measured again on approval
        pass
    with open(os.path.join(d, f"{cid}.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return cid


def _clipped(x):
    """True when the samples sit at full scale for CLIP_RUN in a row (a recording driven into the ceiling)."""
    import numpy as np
    m = np.abs(x).max(axis=1) >= 0.999 if getattr(x, "ndim", 1) > 1 else np.abs(x) >= 0.999
    if not m.any():
        return False
    edges = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    runs = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
    return bool(len(runs) and runs.max() >= CLIP_RUN)


# ----------------------------------------------------------------- the reference match

_refs_cache = {}


def references(path=None):
    """The reference moments (sound_library/reference_moments.json) as arrays, once per process."""
    import numpy as np
    path = path or REFS_PATH
    if path not in _refs_cache:
        doc = json.load(open(path))
        rows = doc["moments"]
        _refs_cache[path] = {"limits": doc["limits"], "scale": np.asarray(doc["scale"], np.float32), "k": int(doc.get("k", MATCH_MOMENTS)),
                             "bed_rel": doc.get("bed_rel_db") or {},
                             "film": [r[0] for r in rows], "t": [r[1] for r in rows], "cls": [r[2] for r in rows],
                             "vec": np.asarray([r[5] for r in rows], np.float32)}
    return _refs_cache[path]


_bed_cache = {}


def _bed(n):
    """A measuring bed, never a sound in a film: n samples of noise with music's long-term slope (3 dB down an
    octave, 40 Hz to 14 kHz), the same every time, at a K-weighted level of 0 dB."""
    import numpy as np
    from score import sound_signal as SS
    if "x" not in _bed_cache or len(_bed_cache["x"]) < n:
        m = max(n, SS.SR * 14)
        w = np.fft.rfft(np.random.default_rng(1113).standard_normal(m).astype(np.float32))
        hz = np.fft.rfftfreq(m, 1.0 / SS.SR)
        shape = np.where((hz >= 40) & (hz <= 14000), 1.0 / np.sqrt(np.maximum(hz, 40.0)), 0.0)
        x = np.fft.irfft(w * shape, m).astype(np.float32)
        kl = SS.k_level(SS.power(x[:SS.SR * 4]))
        x *= 10 ** (-float(10 * np.log10(np.mean(10 ** (kl / 10)))) / 20)
        _bed_cache["x"] = x
    return _bed_cache["x"][:n]


def describe_recording(path, refs=None):
    """A recording on the ruler of score/sound_signal, read the way the references' sounds were read.

    A reference's sound was read off its film's stem: over the bed that plays under it, the bed's spectrum taken
    off, so its tail ends where it sinks into the music. A dry recording read alone rings on for seconds and would
    match nothing. So the recording is laid over a measuring bed at the level the references carry under its class
    (reference_moments.json bed_rel_db: a click's loudest 200 ms sits 4.5 dB under its film's bed, an impact's 2.4
    over) and read with that bed taken off, exactly as the study read the references. Returns the description as
    heard, with `dry` holding the plain reading (its loudness and extents)."""
    import numpy as np
    from score import sound_signal as SS
    x = path if not isinstance(path, str) else _pcm(path, ch=1, rate=SS.SR)[:, 0]
    if len(x) < 2400:
        return None
    dry = SS.describe(x)
    if not dry:
        return None
    rel = (refs or references())["bed_rel"]
    if dry["kind"] == "riser":
        # a climb is read as it is: the study read the references' risers with no bed taken off, and a steady
        # measuring bed under a slow climb reads as part of the climb
        return dict(dry, dry=dry)
    d, cls = None, dry["kind"]
    for _ in range(2):                                  # the class the bed is set for is the class it reads as over it
        r = rel.get(cls)
        if r is None:
            return dict(dry, dry=dry)
        pre = int(0.75 * SS.SR)
        post = int(1.0 * SS.SR)
        bed = _bed(pre + len(x) + post) * (10 ** ((dry["k200"] - r) / 20))
        mix = bed.copy()
        mix[pre:pre + len(x)] += x
        on = pre + int(dry["onset_s"] * SS.SR)
        a0 = max(0, on - int(0.45 * SS.SR))
        pk = pre + int(dry["peak_s"] * SS.SR)
        a1 = min(len(mix), pk + int(max(0.8, dry["decay_s"] + 0.5) * SS.SR))
        bg = bed[max(0, on - int(0.65 * SS.SR)):max(0, on - int(0.04 * SS.SR))]
        d = SS.describe(mix[a0:a1], SS.SR, bg=bg, at=(pk - a0) / SS.SR, search_s=0.08)
        if not d:
            return None
        if d["kind"] == cls:
            break
        cls = d["kind"]
    return dict(d, dry=dry)


def reference_match(d, refs=None):
    """Does this shape match a class of sound the references place? d: a description (sound_signal.describe).

    The class is the one the shape itself belongs to (sound_signal.kind_of). Its distance is the mean distance, on
    the ruler, to the nearest reference moment of that class in each of the k nearest films: one film's habit
    cannot carry a recording in. The limit is that class's own spread, measured on the references the same way
    (the 75th percentile). Returns {ok, class, distance, limit, moments: [[film, second, distance], ...], why}."""
    import numpy as np
    from score import sound_signal as SS
    refs = refs or references()
    if not d:
        return {"ok": False, "class": None, "why": "nothing to read in the file"}
    cls = d.get("kind") or SS.kind_of(d)
    lim = refs["limits"].get(cls)
    if lim is None:
        return {"ok": False, "class": cls, "why": f"the references place no sound of this shape ({cls})"}
    idx = [i for i, c in enumerate(refs["cls"]) if c == cls]
    v = SS.vector(d)
    dist = np.sqrt(np.mean((SS.WEIGHT * (refs["vec"][idx] - v) / refs["scale"]) ** 2, axis=1))
    best = {}
    for j, i in enumerate(idx):
        f = refs["film"][i]
        if f not in best or dist[j] < best[f][0]:
            best[f] = (float(dist[j]), refs["t"][i])
    near = sorted((dv, f, t) for f, (dv, t) in best.items())[:refs["k"]]
    if len(near) < refs["k"]:
        return {"ok": False, "class": cls, "why": "too few reference films carry this class"}
    mean = float(np.mean([n[0] for n in near]))
    out = {"ok": mean <= lim, "class": cls, "distance": round(mean, 3), "limit": lim,
           "moments": [[f, t, round(dv, 3)] for dv, f, t in near]}
    if not out["ok"]:
        out["why"] = f"{mean:.3f} from the references' {cls}s, past the measured limit {lim}"
    return out


def reach(cls, hit_lufs):
    """The gain that sets this recording at its class's reference level against the voice, and whether the mix can
    give it (score/sound.py's range). Level is part of the match: a recording that cannot be as loud as the
    references' sounds of its class, or that only whispers under them, is not one of them."""
    from score import sound_profiles as SP
    c = SP.CLASSES.get(cls) or {}
    over = c.get("over_voice_spoken_db")
    if over is None or hit_lufs is None:
        return None, True
    g = VOICE_LUFS + float(over) - float(hit_lufs)
    return round(g, 1), GAIN_MIN <= g <= GAIN_MAX


def _env10(x, sr=48000):
    """(times, dB) of the 10 ms RMS envelope every 1 ms: the reading the library's timing fields are pinned on."""
    import numpy as np
    win, hop = int(0.010 * sr), int(0.001 * sr)
    if len(x) <= win:
        return np.asarray([len(x) / 2 / sr]), np.asarray([-180.0])
    c = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    k = np.arange(0, len(x) - win, hop)
    return (k + win / 2) / sr, 10 * np.log10((c[k + win] - c[k]) / win + 1e-18)


def _audible_end(x, sr=48000):
    """(peak_s, end_s | None): the loudest 10 ms, and the end of the audible tail after it (the K-weighted level
    25 dB under its top, 0.12 s on). x: mono at 48 kHz."""
    import numpy as np
    from score import sound_signal as SS
    t, env = _env10(x, sr)
    peak = float(t[int(np.argmax(env))])
    kl = SS.k_level(SS.power(x))
    at = int(round(peak * SS.SR / SS.HOP))
    a, b = max(0, at - 12), min(len(kl), at + 13)               # the K-weighted top that belongs to that peak
    top = a + int(np.argmax(kl[a:b])) if b > a else int(np.argmax(kl))
    under = np.flatnonzero(kl[top:] < kl[top] - 25.0)
    return peak, ((top + int(under[0])) * SS.HOP / SS.SR + 0.12 if len(under) else None)


def _tail_s(path, sweep=False):
    """(peak_s, hit_s, max_s, duration_s), measured as the first library's were (tests/test_sfx_timing.py): the
    loudest 10 ms; where the sound reaches full level when that is clearly earlier (the first 10 ms within 1 dB of
    the top; None for a sweep or a climb, whose PEAK meets the picture); a cap at the end of the audible tail when
    the recording runs on past it; the length."""
    import numpy as np
    from score import sound_signal as SS
    x = _pcm(path, ch=1, rate=SS.SR)[:, 0]
    dur = len(x) / SS.SR
    if len(x) < 960:
        return 0.0, None, None, round(dur, 3)
    t, env = _env10(x)
    peak, end = _audible_end(x)
    hit = float(t[int(np.flatnonzero(env >= env.max() - 1.0)[0])])
    max_s = round(end, 3) if end is not None and peak + 0.25 < end < dur - 0.2 else None
    return round(peak, 3), (round(hit, 3) if (not sweep and peak - hit > 0.05) else None), max_s, round(dur, 3)


def approve_by_reference(rid, cid, *, role, use, family=None, db=None, audition_dir=None, lib_dir=None, refs_path=None):
    """Move a staged candidate into the library because its signal matches the references (door 1 above). No
    attestation is asked for and none is written: the row says how it was approved. Returns the library id.
    Raises ApprovalError, with the measured reason, when the recording does not match."""
    if not _ID.match(str(role or "")):
        raise ApprovalError("role is a short lowercase word (whoosh, impact, ...)")
    if not str(use or "").strip():
        raise ApprovalError("say when to use it (the manifest's 'use')")
    d = os.path.join(audition_dir or AUDITION_DIR, rid)
    src = os.path.join(d, f"{cid}.flac")
    meta_path = os.path.join(d, f"{cid}.json")
    if not (os.path.exists(src) and os.path.exists(meta_path)):
        raise ApprovalError(f"no staged candidate {cid} for {rid}")
    meta = json.load(open(meta_path))
    if _canon(meta.get("license")) not in LICENCES:
        raise ApprovalError("its licence is not one the library's policy names")
    if not lossless(meta.get("source_codec")) or int(meta.get("source_rate") or 0) < MIN_RATE:
        raise ApprovalError("the reference door takes lossless recordings of 44.1 kHz or more "
                            f"(this source is {meta.get('source_codec')} at {meta.get('source_rate')} Hz)")
    if meta.get("clipped") or _clipped(_pcm(src)):
        raise ApprovalError("the recording clips")
    desc = describe_recording(src)
    m = reference_match(desc, references(refs_path) if refs_path else None)
    if not m["ok"]:
        raise ApprovalError("no reference match: " + m["why"])
    try:
        import sfx_mix
        hit, hp = sfx_mix.measure_recording(src, ffmpeg=FFMPEG)
    except Exception as e:  # noqa: BLE001
        raise ApprovalError(f"its level cannot be measured ({e!r})")
    gain, ok = reach(m["class"], hit)
    if not ok:
        raise ApprovalError(f"it cannot sit at the references' level for a {m['class']} (it would need {gain} dB)")
    peak, hit_s, max_s, dur = _tail_s(src, sweep=m["class"] in ("whoosh", "riser"))
    row = _join(src, meta, role, use, rid, lib_dir, extra={
        "approval": REFERENCE_APPROVAL, "family": family or None,
        "reference": {"class": m["class"], "distance": m["distance"], "limit": m["limit"], "moments": m["moments"]},
        "shape": {k: desc[k] for k in ("dur_s", "attack_s", "decay_s", "bands", "flat", "low", "sweep", "centroid_hz")},
        "hit_lufs": hit, "hit_lufs_hp150": hp, "peak_s": peak, "hit_s": hit_s, "max_s": max_s, "duration_s": dur,
        "gain_db": gain})
    if db is not None:
        import sound_requests
        sound_requests.set_status(db, rid, "approved", note=f"joined the library as {row['id']} by reference match")
    return row["id"]


def _join(src, meta, role, use, rid, lib_dir, extra=None):
    """Copy a staged recording into the library and write its manifest row (both doors end here)."""
    lib = lib_dir or LIB_DIR
    man_path = os.path.join(lib, "manifest.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else []
    n = 1 + sum(1 for r in manifest if r.get("role") == role)
    rec_id = f"{role}_{n}"
    while any(r.get("id") == rec_id for r in manifest):
        n += 1
        rec_id = f"{role}_{n}"
    dst = os.path.join(lib, f"{rec_id}.flac")
    shutil.copy(src, dst)
    import hashlib
    with open(dst, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()[:16]
    row = {"id": rec_id, "role": role, "use": str(use).strip()[:300],
           "file": os.path.basename(dst), "sha": sha,
           "title": meta.get("title"), "author": meta.get("author"),
           "source_url": meta.get("source_url"),
           "license": meta.get("license"), "approved_request": rid}
    for k in ("peak_s", "hit_s", "hit_lufs", "hit_lufs_hp150", "duration_s", "max_s", "source_file", "source_sha256",
              "source_codec", "source_rate", "source_bits", "cut_s", "fade_s", "bundle", "bundle_licence_sha256"):
        if meta.get(k) is not None:
            row[k] = meta[k]
    for k, v in (extra or {}).items():
        if v is not None:
            row[k] = v
    manifest.append(row)
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return row


def approve(rid, cid, *, role, use, attest, db=None, audition_dir=None,
            lib_dir=None):
    """Move an auditioned candidate into the library. ``attest`` must be
    exactly ``attestation(rid)`` — typed by the owner at the CLI."""
    if attest != attestation(rid):
        raise ApprovalError("approval needs the owner's typed attestation")
    if not _ID.match(str(role or "")):
        raise ApprovalError("role is a short lowercase word (whoosh, "
                            "impact, ...)")
    if not str(use or "").strip():
        raise ApprovalError("say when to use it (the manifest's 'use')")
    d = os.path.join(audition_dir or AUDITION_DIR, rid)
    src = os.path.join(d, f"{cid}.flac")
    meta_path = os.path.join(d, f"{cid}.json")
    if not (os.path.exists(src) and os.path.exists(meta_path)):
        raise ApprovalError(f"no staged candidate {cid} for {rid}")
    meta = json.load(open(meta_path))
    row = _join(src, meta, role, use, rid, lib_dir, extra={"approval": "approved by the owner's ear"})
    rec_id = row["id"]
    if db is not None:
        import sound_requests
        sound_requests.set_status(db, rid, "approved",
                                  note=f"joined the library as {rec_id}")
    return rec_id


def approve_many(rid, picks, *, attest, db=None, audition_dir=None,
                 lib_dir=None):
    """Approve several auditioned candidates of one request with ONE typed
    attestation (an audition page hands the owner a list). ``picks`` is
    [{"cid", "role", "use"}]. Each still goes through ``approve``; one that
    fails is reported and the others still join. Returns [(cid, id | error)]."""
    if attest != attestation(rid):
        raise ApprovalError("approval needs the owner's typed attestation")
    out = []
    for p in picks:
        try:
            out.append((p.get("cid"), approve(
                rid, p.get("cid"), role=p.get("role"), use=p.get("use"),
                attest=attest, db=None, audition_dir=audition_dir,
                lib_dir=lib_dir)))
        except (ApprovalError, OSError, ValueError) as e:
            out.append((p.get("cid"), f"not approved: {e}"))
    return out


def decline(rid, *, db, note=None):
    import sound_requests
    return sound_requests.set_status(db, rid, "declined", note=note)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="sound_approval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("rid")
    s.add_argument("path")
    s.add_argument("--source", required=True)
    s.add_argument("--license", required=True)
    s.add_argument("--author", required=True)
    s.add_argument("--title")
    a = sub.add_parser("approve")
    a.add_argument("rid")
    a.add_argument("cid")
    a.add_argument("--role", required=True)
    a.add_argument("--use", required=True)
    m = sub.add_parser("approve-many")
    m.add_argument("list", help="the audition page's approved.json")
    r = sub.add_parser("approve-by-reference")
    r.add_argument("rid")
    r.add_argument("cid")
    r.add_argument("--role", required=True)
    r.add_argument("--use", required=True)
    r.add_argument("--family")
    q = sub.add_parser("match")
    q.add_argument("path")
    args = ap.parse_args(argv)
    if args.cmd == "match":
        print(json.dumps(reference_match(describe_recording(args.path)), indent=1))
        return 0
    if args.cmd == "approve-by-reference":
        try:
            print(approve_by_reference(args.rid, args.cid, role=args.role, use=args.use, family=args.family))
        except ApprovalError as e:
            print(f"not approved: {e}", file=sys.stderr)
            return 3
        return 0
    if args.cmd == "approve-many":
        if not sys.stdin.isatty():
            print("approval is typed by the owner at a terminal",
                  file=sys.stderr)
            return 2
        doc = json.load(open(args.list))
        rid, picks = doc["rid"], doc.get("approved") or []
        print(f"{len(picks)} recordings of {rid}:")
        for p in picks:
            print(f"  {p.get('cid')}  {p.get('role')}  {p.get('use')}")
        line = input(f"Type exactly: {attestation(rid)}\n> ")
        for cid, got in approve_many(rid, picks, attest=line):
            print(cid, "->", got)
        return 0
    if args.cmd == "stage":
        print(stage(args.rid, args.path, source_url=args.source,
                    licence=args.license, author=args.author,
                    title=args.title))
        return 0
    if args.cmd == "approve":
        if not sys.stdin.isatty():
            print("approval is typed by the owner at a terminal",
                  file=sys.stderr)
            return 2
        line = input(f"Type exactly: {attestation(args.rid)}\n> ")
        print(approve(args.rid, args.cid, role=args.role, use=args.use,
                      attest=line))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
