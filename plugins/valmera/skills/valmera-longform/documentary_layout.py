"""Frame original sources on the branded canvas; keep the spoken text on its own rail."""


def fit(plan, span, key, clip, box, **options):
    x, y, right, bottom = box
    aspect = clip['w'] / clip['h'] * 1080 / 1920
    width = min(right - x, (bottom - y) * aspect)
    height = width / aspect
    x += (right - x - width) / 2
    y += (bottom - y - height) / 2
    return plan.card(span, key, [x, y, x + width, y + height], **options)


def document(plan, span, key, clip, label, companion=None):
    plan.page(span, '#182028', grain=.002)
    # The PDF crop already contains its neighbouring lines. A second crop around
    # the underline clipped descenders and sometimes removed the qualification.
    passage = companion or clip
    quote = next((p['quote'] for p in passage['source'].get('primary_context', [])
                  if p.get('quote') and p['quote'] == passage.get('shows')), '')
    wide = clip['w'] / clip['h'] > 2
    box = (.05, .15, .95, .79)
    lines = []
    if quote:
        from design import face_for
        from pathlib import Path
        font = face_for(quote)
        voice = 'excerpt'
        if Path(font).name != plan.voices[voice]['font']:
            voice = 'excerpt-' + Path(font).stem
            plan.voices[voice] = dict(plan.voices['excerpt'],font=font)
        x, width = (.11, .78) if wide else (.43, .51)
        line = ''
        for word in quote.split():
            joined = (line + ' ' + word).strip()
            if line and plan.R.width(voice, joined) > width * 1920:
                lines.append(line)
                line = word
            else:
                line = joined
        if line:
            lines.append(line)
        if len(lines) <= (4 if wide else 8):
            box = (.05, .14, .95, .51) if wide else (.045, .14, .40, .78)
            y = (.54 if wide else .47 - len(lines) * .024)
            for i, text in enumerate(lines):
                # Reveal whole lines on a reading beat. Earlier lines stay for context.
                onset = min(round(i * 8), (span[1] - span[0]) * i // (2 * len(lines)))
                plan.word([span[0] + onset, span[1]], text, voice, x, y + i * .052, note=False)
    fit(plan, span, key, clip, box)
    plan.word(span, label, 'source', .05, .855, note=False, z=3)


def archive(plan, row, key, crop):
    clip = row['clip']
    plan.page(row['f'], '#14191e', grain=0)
    box = (.005,.07,.995,.86) if clip.get('kind')=='diagram' else (.025,.14,.975,.795)
    fit(plan, row['f'], key, clip, box,
        src=row['src'], crop=crop)
