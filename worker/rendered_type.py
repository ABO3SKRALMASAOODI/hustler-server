"""Compare browser-measured HTML glyphs with the pixels of the final film.

No OCR or interpretation of the words. The exposure is only an ink mask;
colour, backing and contrast are all measured on the delivered picture.
"""
import base64
from io import BytesIO
import os
import subprocess
import time

import numpy as np
from PIL import Image

import qa_metrics


def contrast(frame, exposure):
    """APCA on actual picture pixels under and around the measured glyphs."""
    frame = np.asarray(frame.convert('RGB') if isinstance(frame, Image.Image) else frame)
    mask = Image.open(BytesIO(base64.b64decode(exposure))).convert('RGBA').getchannel('A')
    mask = mask.resize((frame.shape[1], frame.shape[0]), Image.Resampling.LANCZOS)
    alpha = np.asarray(mask)
    glyphs = alpha >= 204
    if glyphs.sum() < 20:
        return None
    return qa_metrics.legibility(frame=frame, glyph_mask=glyphs,
                                 exclude=alpha > 8)


def findings(path, measured, plan, deadline):
    """Bounded exact-time reads, one per custom composition's settled sample.

    Source-face frames are unsuitable: their nearest 2fps sample can land
    on a different caption animation state. Decode this exact output time.
    Nothing is written to disk or retained in the public quality report.
    """
    if not (measured or {}).get('items'):
        return []
    custom = {i['id'] for i in (plan or {}).get('items') or []
              if i.get('template') == 'html' and i.get('layer') != 'behind_subject'}
    out = []
    for gid, m in ((measured or {}).get('items') or {}).items():
        if gid not in custom or not m.get('glyph_mask') or not m.get('type_runs'):
            continue
        if time.monotonic() > deadline - 2:
            break
        t = float(m['t'])
        try:
            r = subprocess.run([
                'ffmpeg', '-v', 'error', '-nostdin', '-ss', f'{t:.3f}',
                '-i', os.fspath(path), '-frames:v', '1', '-an', '-sn',
                '-vf', 'scale=720:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'],
                capture_output=True, check=True,
                timeout=max(1, min(8, deadline - time.monotonic())))
            result = contrast(Image.open(BytesIO(r.stdout)), m['glyph_mask'])
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if result and result.get('pass') is False:
            out.append(dict(id=gid, t=t, lc=round(result['value'], 1),
                            text=[x['text'] for x in m['type_runs'][:8]]))
    return out
