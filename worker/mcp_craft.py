"""MCP delivery facts, not a quality score or an intent classifier.

Audio is measured on the encoded file. Motion samples the actual program at
2 fps, excluding the automatic end card. Authored type counts are explicitly
EDL facts: source-burned type needs frame review. Unknown never means pass.
"""
import json
import math
import subprocess
import threading


def authored(edl):
    captions = edl.get('captions') or {}
    return {
        'text_items': len(edl.get('texts') or []),
        'caption_items': len(captions.get('items') or []),
        'captions_enabled': captions.get('enabled', False),
        'motion_graphics': len(edl.get('motion') or []),
        'camera_moves': len((edl.get('effects') or {}).get('zooms') or []),
        'basis': 'timeline declarations; source-burned text and in-shot motion are not counted',
    }


def measure(path, edl, audio=None, duration_s=None):
    import numpy as np
    import audio_qc
    from schemas import program_duration
    duration = min(float(duration_s or program_duration(edl)), float(program_duration(edl)))
    report = {'schema': 1, 'program_s': duration, 'authored': authored(edl),
              'silent': None, 'no_motion_detected': None,
              'motion_sample_fps': 2, 'motion_pairs': 0,
              'source_text_review': 'inspect rendered frames', 'warnings': []}
    if not audio or audio.get('i') is None:
        try:
            audio = audio_qc.measure(path, duration_s=duration)
        except Exception as exc:
            report['audio_error'] = type(exc).__name__
    level = (audio or {}).get('i')
    if isinstance(level, (int, float)):
        report['integrated_lufs'] = level if math.isfinite(level) else -120.0
        report['silent'] = level < -70
    # Fixed-resolution decoded pixels, streaming, with a bounded process.
    cmd = ["ffmpeg", '-v', 'error', '-i', path, '-t', str(duration),
           '-an', '-vf', 'fps=2,scale=96:54,format=gray', '-f', 'rawvideo', '-']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(120, proc.kill)
    timer.start()
    diffs = []
    previous = None
    try:
        while True:
            raw = proc.stdout.read(96 * 54)
            if len(raw) != 96 * 54:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
            if previous is not None:
                diffs.append(float(np.abs(frame - previous).mean()))
            previous = frame
        code = proc.wait(timeout=30)
        if code == 0 and diffs:
            report.update(motion_pairs=len(diffs),
                          motion_delta_p90=round(float(np.percentile(diffs, 90)), 3),
                          sampled_static_fraction=round(sum(v < .5 for v in diffs) / len(diffs), 4),
                          no_motion_detected=all(v < .5 for v in diffs))
        else:
            report['motion_error'] = f'decode exit {code}; {len(diffs)} frame pairs'
    finally:
        timer.cancel()
        timer.join()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    a = report['authored']
    report['no_authored_text'] = not (a['text_items'] or a['captions_enabled'] or a['caption_items'] or a['motion_graphics'])
    if report['silent']:
        report['warnings'].append('SILENT: encoded mix is below -70 LUFS.')
    if report['no_authored_text']:
        report['warnings'].append('NO AUTHORED TEXT: inspect frames for source-burned type; add appropriate captions or designed type.')
    if report['no_motion_detected']:
        report['warnings'].append('NO MOTION DETECTED: all sampled program frame pairs are static.')
    report['next_step'] = ('Inspect the complete film and repair unintended gaps using Valmera editing tools, '
                           'then export_final for that version. These facts are not a quality rating.')
    return report


def summary(report):
    if not report:
        return 'Craft measurements unavailable; inspect the rendered film before delivery.'
    return 'CRAFT REPORT: ' + json.dumps(report, ensure_ascii=False, allow_nan=False)
