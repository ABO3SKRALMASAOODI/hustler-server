"""Music/voice stem separation (round 97, #7).

"Remove the music but keep the talking" was an honest 'impossible' for as
long as the original footage's audio was one mixed track — and two of the
last seventeen engaged users asked for exactly that in one 36-hour window.
Demucs (Meta's open-source separator, htdemucs) splits any audio into
vocals + everything-else well enough that muting one side sounds deliberate,
not broken.

Where it runs: on the EXECUTOR, synchronously inside an agent turn — the
round-61 capture shape (no job row). Separation is model compute over the
whole track, exactly the class of work the dispatcher must never do beside
agent turns. The dependency (torch + demucs, CPU) and the model weights are
baked into the image; `available()` is what /health's `features` list and
the local fallback both consult, so a build without the dependency simply
never advertises the capability — honest-off, the round-53 rule.

What it produces: two AAC files in storage, cached FOREVER per source audio
(the keys embed the source sha, so asking twice never separates twice). The
EDL's `stem_mix` node then names those keys plus a gain for each side, and
the renderer swaps the premixed pair in wherever the graph would have read
the original's audio track. Removing the node restores the untouched
original — separation artifacts are never in the signal path unless the
user asked for the split.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

import config
import media
import storage


def available():
    """Can THIS process run a separation? True only where the demucs package
    is importable (the executor image bakes it; the dispatcher and dev boxes
    usually do not). find_spec, not import: torch takes seconds to load and
    /health calls this on every probe."""
    return importlib.util.find_spec("demucs") is not None


def _run_demucs(wav_path, outdir):
    """One two-stem separation. CLI, not the python API: the CLI is the
    documented stable surface, and a crash inside torch takes the subprocess
    with it instead of this worker."""
    cmd = [sys.executable, "-m", "demucs.separate",
           "--two-stems", "vocals",
           "-n", config.STEMS_MODEL,
           "-d", "cpu",
           "-o", outdir,
           wav_path]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=config.STEMS_TIMEOUT_S)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-400:]
        raise media.MediaError(f"stem separation failed: {tail}")
    base = os.path.splitext(os.path.basename(wav_path))[0]
    stem_dir = os.path.join(outdir, config.STEMS_MODEL, base)
    vocals = os.path.join(stem_dir, "vocals.wav")
    accomp = os.path.join(stem_dir, "no_vocals.wav")
    if not (os.path.exists(vocals) and os.path.exists(accomp)):
        raise media.MediaError(
            f"stem separation produced no stems in {stem_dir}")
    return vocals, accomp


def run_stems_job(job):
    """Separate a stored source's audio into vocals/accompaniment and upload
    both. payload: {src_key, vocals_key, accomp_key}. Returns
    {ok, vocals_key, accomp_key, seconds}.

    Idempotent by construction: the destination keys embed the source sha
    (the caller builds them), so a re-run overwrites identical content and
    a cache hit never reaches this function at all."""
    payload = job.get("payload") or {}
    src_key = payload["src_key"]
    vocals_key = payload["vocals_key"]
    accomp_key = payload["accomp_key"]
    if not available():
        raise media.MediaError(
            "stem separation is not available on this build")
    workdir = tempfile.mkdtemp(prefix="stems_",
                               dir=getattr(config, "TMP_DIR", None) or None)
    try:
        src_local = os.path.join(
            workdir, "src" + os.path.splitext(src_key)[1].lower())
        storage.download_to(src_key, src_local)
        seconds = media.probe_audio_duration(src_local)
        if seconds > config.STEMS_MAX_SOURCE_S:
            raise media.MediaError(
                f"the audio is {seconds / 60:.0f} minutes long; music "
                f"separation currently supports up to "
                f"{config.STEMS_MAX_SOURCE_S / 60:.0f} minutes")
        wav = os.path.join(workdir, "audio.wav")
        media.run(["ffmpeg", "-y", "-v", "error", "-i", src_local,
                   "-vn", "-ac", "2", "-ar", "44100", wav])
        vocals_wav, accomp_wav = _run_demucs(wav, workdir)
        vocals_m4a = os.path.join(workdir, "vocals.m4a")
        accomp_m4a = os.path.join(workdir, "accomp.m4a")
        for src, dst in ((vocals_wav, vocals_m4a), (accomp_wav, accomp_m4a)):
            media.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                       "-c:a", "aac", "-b:a", "192k",
                       "-movflags", "+faststart", dst])
        storage.upload_file(vocals_m4a, vocals_key, "audio/mp4")
        storage.upload_file(accomp_m4a, accomp_key, "audio/mp4")
        return {"ok": True, "vocals_key": vocals_key,
                "accomp_key": accomp_key, "seconds": seconds}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
