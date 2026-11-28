"""Measure ordinary titles against the final picture using their libass ink.

The compiler, fonts, wrapping, stacking and animation are the renderer's.
Only a temporary measurement copy has its ink exposed; the EDL is unchanged.
"""
import copy
from io import BytesIO
import os
import subprocess
import tempfile
import time

import numpy as np
from PIL import Image

import graphics
import qa_metrics


def samples(texts, duration, limit=12):
    rows = []
    for n, item in enumerate(texts):
        a, b = max(0., float(item.get('start') or 0)), min(duration, float(item.get('end') or 0))
        if b-a < .3 or item.get('behind'):
            continue
        # Stay out of the requested entrance/exit fades. The three settled
        # moments catch a moving bright background under otherwise static ink.
        for fraction in (.25, .5, .75):
            rows.append((n, a+(b-a)*fraction))
    if len(rows) > limit:
        # Sample the opening and closing lockups before intermediate ticks.
        first, last = min(t for _, t in rows), max(t for _, t in rows)
        edges = sorted({n for n, t in rows if t < first+.4 or t > last-.4})
        chosen = [next(q for q in rows if q[0] == n) for n in edges[:limit]]
        rest = [q for q in rows if q[0] not in edges]
        slots = min(len(rest), limit-len(chosen))
        rows = chosen + [rest[int(i*len(rest)/slots)] for i in range(slots)]
    return rows


def exposed(texts, selected):
    items = copy.deepcopy(texts)
    for n, item in enumerate(items):
        ink = '#FFFFFF' if n == selected else '#000000'
        item.update(color=ink, accent_color=ink, outline_width=0, shadow=0, box=False)
    return items


def contrast(frame, mask):
    alpha = np.asarray(mask.convert('L'))
    glyphs = alpha >= 204
    if glyphs.sum() < 20:
        return None
    return qa_metrics.legibility(frame=np.asarray(frame.convert('RGB')),
                                 glyph_mask=glyphs, exclude=alpha > 8)


def _filter_path(path):
    return os.fspath(path).replace('\\', '\\\\').replace("'", "'\\''").replace(':', '\\:')


def measure(path, texts, W, H, duration, deadline, visible=None, moments=None):
    """Bounded offline samples; return failures and explicitly skipped reads."""
    failures, skipped = [], []
    w = min(720, int(W))
    h = max(2, round(w*H/W/2)*2)
    deadline = min(deadline, time.monotonic()+8.)
    with tempfile.TemporaryDirectory(prefix='title-ink-') as folder:
        for n, t in samples(texts, duration):
            if deadline-time.monotonic() < .5:
                skipped.append('ordinary title contrast: sample budget spent')
                break
            ass = os.path.join(folder, 'ink.ass')
            graphics.build_gfx_ass({'texts': exposed(texts, n)}, duration, ass, (W, H))
            # Compile at the real canvas, then scale ink and picture equally.
            vf = (f"setpts=PTS+{t:.6f}/TB,ass='{_filter_path(ass)}'"
                  f":fontsdir='{_filter_path(graphics.FONTS_DIR)}',scale={w}:{h}")
            commands = [
                ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '1', '-filter_threads', '1',
                 '-f', 'lavfi', '-i', f'color=black:s={W}x{H}:r=30', '-vf', vf],
                ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '1', '-filter_threads', '1',
                 '-ss', f'{t:.6f}', '-i', os.fspath(path), '-vf', f'scale={w}:{h}'],
            ]
            try:
                images = []
                for command in commands:
                    r = subprocess.run(command+['-frames:v', '1', '-an', '-sn', '-f',
                        'image2pipe', '-vcodec', 'png', '-'], check=True, capture_output=True,
                        timeout=max(.1, deadline-time.monotonic()))
                    images.append(Image.open(BytesIO(r.stdout)).copy())
                result = contrast(images[1], images[0])
                if visible is not None and result and result.get('pass'):
                    text = texts[n].get('text')
                    if moments is not None and text:
                        item = texts[n]
                        group = ".".join(str(item.get("id") or "").split(".")[:3])
                        moments[n] = {"text": text, "start": item["start"], "end": item["end"],
                                      "group": group}
                    if text and text not in visible:
                        visible.append(text)
                if result and result.get('pass') is False:
                    item = texts[n]
                    failures.append(dict(id=item.get('id') or f'text.{n}', t=round(t, 3),
                                         lc=round(result['value'], 1), text=[item.get('text', '')]))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                skipped.append(f'ordinary title contrast: {type(exc).__name__}')
    return failures, skipped
