"""MCP delivery facts, not a quality score or an intent classifier.

Audio is measured on the encoded file. Motion samples the actual program at
4 fps in bounded windows, excluding the automatic end card. Authored type counts are explicitly
EDL facts: source-burned type needs frame review. Unknown never means pass.
"""
import json
import math
import subprocess


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
              'motion_sample_fps': 4, 'motion_pairs': 0,
              'motion_basis': 'sampled 0.75-second windows, at most 16; not an exhaustive motion audit',
              'source_text_review': 'inspect rendered frames', 'warnings': []}
    # The automatic end card cannot make an otherwise silent film pass.
    if not audio or audio.get('i') is None or float(duration_s or duration) > duration + .1:
        try:
            import media
            facts = media.probe(path)
            if facts.get('has_audio') is False:
                audio = {'i': -120.0}
                report['audio_track_present'] = False
            else:
                proc = subprocess.run(
                    ['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-t', str(duration),
                     '-map', '0:a:0', '-af', 'loudnorm=I=-14:TP=-2:LRA=11:print_format=json',
                     '-f', 'null', '-'], capture_output=True, timeout=180, check=True)
                loud, _ = audio_qc._parse(proc.stderr.decode('utf-8', 'replace'))
                audio = {'i': float(loud['input_i'])} if loud else None
                report['audio_track_present'] = True
        except Exception as exc:
            audio = None
            report['audio_error'] = type(exc).__name__
    level = (audio or {}).get('i')
    if isinstance(level, (int, float)):
        report['integrated_lufs'] = level if math.isfinite(level) else -120.0
        report['silent'] = level < -70
    # Seek before decoding. A 35-minute narration must not incur a second
    # full video decode merely to report sampled motion.
    diffs, sampled, failures = [], [], []
    count = max(1, min(16, int(math.ceil(duration / 2))))
    for start in np.linspace(0, max(0, duration - .75), count):
        cmd = ['ffmpeg', '-v', 'error', '-ss', str(float(start)), '-i', path,
               '-t', str(min(.75, duration - start)), '-an',
               '-vf', 'fps=4,scale=96:54,format=gray', '-f', 'rawvideo', '-']
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=12, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            failures.append(type(exc).__name__)
            continue
        size = 96 * 54
        frames = np.frombuffer(proc.stdout[:len(proc.stdout) // size * size], dtype=np.uint8)
        frames = frames.reshape(-1, size).astype(np.int16)
        if len(frames) > 1:
            diffs.extend(np.abs(np.diff(frames, axis=0)).mean(axis=1).tolist())
            sampled.append(round(float(start), 3))
    report['motion_sample_starts'] = sampled
    if diffs:
        report.update(motion_pairs=len(diffs),
                      motion_delta_p90=round(float(np.percentile(diffs, 90)), 3),
                      sampled_static_fraction=round(sum(v < .5 for v in diffs) / len(diffs), 4),
                      no_motion_detected=all(v < .5 for v in diffs) if not failures else None)
    if failures or not diffs:
        report['motion_error'] = failures or ['no decoded frame pairs']
    a = report['authored']
    report['no_authored_text'] = not (a['text_items'] or a['captions_enabled'] or a['caption_items'] or a['motion_graphics'])
    if report['silent']:
        report['warnings'].append('SILENT: encoded mix is below -70 LUFS.')
    if report['no_authored_text']:
        report['warnings'].append('NO AUTHORED TEXT: inspect frames for source-burned type; add appropriate captions or designed type.')
    if report['no_motion_detected']:
        report['warnings'].append('NO MOTION DETECTED in sampled program windows; inspect the complete film.')
    report['next_step'] = ('Inspect the complete film and repair unintended gaps using Valmera editing tools, '
                           'then export_final for that version. These facts are not a quality rating.')
    return report


def summary(report):
    if not report:
        return 'Craft measurements unavailable; inspect the rendered film before delivery.'
    return 'CRAFT REPORT: ' + json.dumps(report, ensure_ascii=False, allow_nan=False)
