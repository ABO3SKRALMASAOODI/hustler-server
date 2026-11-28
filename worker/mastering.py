"""Verify social loudness on encoded bytes, including short and reused renders."""
import math
import os
import subprocess
import audio_qc
import true_peak

TARGET = -14.0
TOLERANCE = .75
MAX_PASSES = 5


def measure(path):
    data, _ = audio_qc._parse(audio_qc._run_ffmpeg(path) or '')
    if not data:
        return None
    return {k: float(data[v]) for k, v in (('i', 'input_i'), ('tp', 'input_tp'))}


def _audio_fingerprint(path):
    """Hash the actual packets, codec parameters, durations and timestamps, without decoding."""
    import hashlib
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-copyts", "-i", path,
                           "-map", "0:a:0", "-c", "copy", "-f", "framehash", "-"],
                          capture_output=True, check=True, timeout=90)
    return hashlib.sha256(proc.stdout).hexdigest()


def cached_guard(path, workdir, bitrate):
    """Reuse a measured AAC master across picture edits. Keep the normal path on a cache miss."""
    import json
    import tempfile
    import media_cache
    try:
        key = f"social-master/v1/{TARGET}/{TOLERANCE}/{bitrate}/{_audio_fingerprint(path)}"
    except (OSError, subprocess.SubprocessError):
        return guard(path, workdir, bitrate)
    with tempfile.TemporaryDirectory(dir=workdir, prefix="verified_audio_") as folder:
        audio = os.path.join(folder, "master.m4a")
        note = os.path.join(folder, "measurements.json")
        try:
            if media_cache.resident(key+".json", folder, "measurements.json"):
                with open(note) as record:
                    saved = json.load(record)
                report = saved["report"]
                if not report["passes"]:
                    return dict(report, reused=True)
                if media_cache.resident(key+".m4a", folder, "master.m4a"):
                    mux = os.path.join(folder, "picture.mp4")
                    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-copyts", "-i", path,
                                    "-i", audio, "-map", "0:v?", "-map", "1:a:0", "-c", "copy",
                                    "-movflags", "+faststart", mux], check=True, capture_output=True, timeout=180)
                    if _audio_fingerprint(mux) == saved["audio"]:
                        os.replace(mux, path)
                        return dict(report, reused=True)
        except (OSError, KeyError, ValueError, subprocess.SubprocessError):
            pass
        report = guard(path, workdir, bitrate)
        if report and report.get("held"):
            # Use fresh paths: a failed read can leave a hardlinked cache lease above.
            try:
                saved = {"report": report}
                if report["passes"]:
                    audio = os.path.join(folder, "new.m4a")
                    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-copyts", "-i", path,
                                    "-map", "0:a:0", "-c", "copy", audio],
                                   check=True, capture_output=True, timeout=180)
                    saved["audio"] = _audio_fingerprint(path)
                    media_cache.remember_file(key+".m4a", audio)
                note = os.path.join(folder, "new.json")
                with open(note, "w") as record:
                    json.dump(saved, record)
                media_cache.remember_file(key+".json", note)
            except (OSError, subprocess.SubprocessError):
                pass
        return report


def guard(path, workdir, bitrate='192k'):
    first = current = measure(path)
    if current is None:
        return None  # no audio stream
    if not math.isfinite(current['i']):
        return {'silent': True, 'passes': 0, 'held': True}
    report = {'i_in': current['i'], 'passes': 0}
    limit_dbfs = -2.0
    gain_response = 1.0
    # AAC may establish a lower safe ceiling on more than one pass. Leave
    # bounded room to use the measured gain response after that settles.
    # The same p5574 approval mix needs three passes with FFmpeg 9, four
    # with FFmpeg 8; three stopped at -15.01 LUFS despite useful progress.
    for n in range(MAX_PASSES):
        if abs(current['i']-TARGET) <= TOLERANCE and current['tp'] <= -1.0:
            return dict(report, **current, held=True)
        tmp = os.path.join(workdir, f'master_guard_{n}.mp4')
        try:
            # A measured constant gain corrects loudnorm's short-programme
            # error. A limiter catches only peaks; picture packets are copied.
            error = TARGET-current['i']
            gain = min(12.0, error / gain_response) if error > 0 else error
            previous_limit = limit_dbfs
            chain = f'volume={gain:.5f}dB,' + true_peak.limiter_chain(limit_dbfs)
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-nostdin', '-i', path,
                '-map', '0:v?', '-map', '0:a:0', '-c:v', 'copy', '-af', chain,
                '-c:a', 'aac', '-b:a', bitrate, '-movflags', '+faststart', tmp],
                capture_output=True, check=True, timeout=1800)
            # loudnorm reports hundredths, while the default ebur128 meter
            # rounds to tenths. A -0.96 dBTP file can pass that guard as
            # -1.0 and then fail this verifier. Correct with this same meter;
            # do not relax the delivery ceiling or retry an unchanged mix.
            peak_report = true_peak.guard(tmp, bitrate=bitrate, workdir=workdir,
                                          meter=lambda p: (measure(p) or {}).get('tp'))
            # Keep the codec headroom the encoded bytes actually required.
            # Resetting to -2 dBFS on each loudness pass recreates the AAC
            # overshoot; the peak guard then removes the gain just added.
            # p5574 oscillated between -14 LUFS/+3 dBTP and -15.6/-2.
            if peak_report and peak_report.get('limit_dbfs') is not None:
                limit_dbfs = min(limit_dbfs, float(peak_report['limit_dbfs']))
                report['limit_dbfs'] = limit_dbfs
            checked = measure(tmp)
            if checked is None or not math.isfinite(checked['i']):
                raise ValueError('mastered sound could not be measured')
            if checked['tp'] > -1.0:
                raise ValueError(f"mastered sound exceeds the true-peak ceiling: "
                                 f"{checked['tp']} dBTP; correction={peak_report}")
            # Once the codec ceiling holds, limiting can absorb much of a
            # requested boost. Use its measured response for the remaining
            # error instead of asymptotically adding ever smaller gains.
            # Re-estimate only at the same ceiling; lowering it changes the
            # transfer curve. The next pass still verifies both constraints.
            achieved = checked['i'] - current['i']
            if gain > .1 and achieved > .1 and limit_dbfs == previous_limit:
                gain_response = max(.25, min(1.0, achieved / gain))
                report['gain_response'] = gain_response
            os.replace(tmp, path)
            current = checked
            report['passes'] += 1
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if abs(current['i']-TARGET) > TOLERANCE:
        raise ValueError(f"social master did not hold {TARGET} LUFS: {current['i']}")
    return dict(report, **current, held=True)
