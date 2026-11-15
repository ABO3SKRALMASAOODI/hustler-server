"""Verify social loudness on encoded bytes, including short and reused renders."""
import math
import os
import subprocess
import audio_qc
import true_peak

TARGET = -14.0
TOLERANCE = .75


def measure(path):
    data, _ = audio_qc._parse(audio_qc._run_ffmpeg(path) or '')
    if not data:
        return None
    return {k: float(data[v]) for k, v in (('i', 'input_i'), ('tp', 'input_tp'))}


def guard(path, workdir, bitrate='192k'):
    first = current = measure(path)
    if current is None:
        return None  # no audio stream
    if not math.isfinite(current['i']):
        return {'silent': True, 'passes': 0, 'held': True}
    report = {'i_in': current['i'], 'passes': 0}
    for n in range(3):
        if abs(current['i']-TARGET) <= TOLERANCE and current['tp'] <= -1.0:
            return dict(report, **current, held=True)
        tmp = os.path.join(workdir, f'master_guard_{n}.mp4')
        try:
            # A measured constant gain corrects loudnorm's short-programme
            # error. A limiter catches only peaks; picture packets are copied.
            gain = TARGET-current['i']
            chain = f'volume={gain:.5f}dB,' + true_peak.limiter_chain(-2.0)
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
            checked = measure(tmp)
            if checked is None or not math.isfinite(checked['i']):
                raise ValueError('mastered sound could not be measured')
            if checked['tp'] > -1.0:
                raise ValueError(f"mastered sound exceeds the true-peak ceiling: "
                                 f"{checked['tp']} dBTP; correction={peak_report}")
            os.replace(tmp, path)
            current = checked
            report['passes'] += 1
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if abs(current['i']-TARGET) > TOLERANCE:
        raise ValueError(f"social master did not hold {TARGET} LUFS: {current['i']}")
    return dict(report, **current, held=True)
