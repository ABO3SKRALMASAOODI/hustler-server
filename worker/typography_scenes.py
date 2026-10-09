"""Speech-cued, mixed-font compositions built from editable native text.

Layout is measured once from the COMPLETE scene. New words keep their final
slots; earlier words do not re-centre, replay an entrance or change type size.
The editor supplies the meaning, line breaks and real program cue times.

fit='strict' rejects any out-of-range argument or overflowing layout.
fit='auto' repairs geometry instead: numeric styling is clamped to its range,
the box is clamped into the renderer's safe area and font_size is scaled down
until every row fits. Every repair is reported in ``result['fit']['notes']``
so nothing changes silently. Either way, every remaining problem is raised at
once (``LayoutRejected``) so one correction can fix them all.
"""
import math
import re

from type_metrics import FILES, ITALICS, width

# Validator bounds. agent_tools builds the model-visible JSON schema from these
# exact constants so the advertised contract cannot drift from the checks.
MAX_LINES = 6
MAX_RUNS = 32
MAX_RUN_CHARS = 100
FONT_SIZE_RANGE = (.03, .2)        # fraction of the canvas short edge
LEADING_RANGE = (1.05, 1.8)        # rows never overlap (unlike caption leading)
LINE_SIZE_RANGE = (.6, 1.8)
RUN_SCALE_RANGE = (.6, 1.8)
RUN_PX_RANGE = (.03, .3)           # resulting run size, fraction of short edge
SAFE_AREA = (.045, .04, .955, .96)  # renderer edge-safe region [l, t, r, b]
MIN_BOX_SPAN = .1                  # schemas.Frame._picture_rectangle minimum
DEFAULT_BOX = (.08, .28, .92, .66)
FITS = ('auto', 'strict')


class LayoutRejected(ValueError):
    """All violations found in one pass, not only the first."""

    def __init__(self, problems):
        self.problems = list(problems)
        if len(self.problems) == 1:
            message = self.problems[0]
        else:
            message = (f'{len(self.problems)} problems: '
                       + ' '.join(f'({i}) {p}' for i, p in
                                  enumerate(self.problems, 1)))
        super().__init__(message)


def bounded(value, name, rng, fit, problems, notes, where=''):
    """A float inside rng. auto clamps (and notes it); strict records a
    problem. Non-numbers are always problems. Returns None when unusable."""
    low, high = rng
    place = f' ({where})' if where else ''
    try:
        v = float(value)
    except (TypeError, ValueError):
        problems.append(f'{name} must be a number between {low:g} and {high:g}{place}')
        return None
    if not math.isfinite(v):
        problems.append(f'{name} must be between {low:g} and {high:g}{place}')
        return None
    if low <= v <= high:
        return v
    if fit == 'auto':
        clamped = min(max(v, low), high)
        notes.append(f'{name}{place} {v:g}→{clamped:g} (allowed {low:g}–{high:g})')
        return clamped
    problems.append(f'{name} must be between {low:g} and {high:g}{place}')
    return None


def _fmt_box(box):
    return '[' + ','.join(f'{v:g}' for v in box) + ']'


def _span(a, b, low, high):
    a, b = min(max(a, low), high), min(max(b, low), high)
    if b - a < MIN_BOX_SPAN - 1e-9:
        mid = min(max((a + b) / 2, low + MIN_BOX_SPAN / 2), high - MIN_BOX_SPAN / 2)
        a, b = mid - MIN_BOX_SPAN / 2, mid + MIN_BOX_SPAN / 2
    return a, b


def fit_box(box, default, fit, problems, notes, safe=True):
    """[left, top, right, bottom] frame fractions for a layout region.

    strict: the box must already be a valid picture rectangle (and inside the
    safe area when ``safe``). auto: reversed edges are swapped, edges are
    clamped into the safe area (or 0..1), and a region thinner than 10% grows
    around its centre. Pixel-looking values are never guessed at."""
    from schemas import Frame
    raw = box or default
    try:
        values = [float(v) for v in raw]
    except (TypeError, ValueError):
        values = []
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        problems.append('box must be [left, top, right, bottom] as frame '
                        'fractions 0..1')
        return list(default)
    left, top, right, bottom = SAFE_AREA if safe else (0., 0., 1., 1.)
    # Valid authored numbers pass through unchanged (byte-identical layers).
    authored = (list(raw) if all(isinstance(v, (int, float)) and not
                                 isinstance(v, bool) for v in raw) else values)
    if fit != 'auto':
        try:
            values = Frame._picture_rectangle(authored)
        except ValueError as exc:
            problems.append(str(exc))
            return list(default)
        x0, y0, x1, y1 = values
        if safe and (x0 < left or x1 > right or y0 < top or y1 > bottom):
            # Match the renderer's edge-safe region rather than silently
            # moving a deliberately authored left/right composition.
            problems.append('Keep the scene inside the frame safe area '
                            f'(box within {left:g}–{right:g} × {top:g}–{bottom:g})')
        return values
    if max(abs(v) for v in values) > 1.5:
        problems.append(f'box {_fmt_box(values)} looks like pixels; use frame '
                        'fractions 0..1, e.g. [.08,.28,.92,.66]')
        return list(default)
    x0, y0, x1, y1 = values
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    x0, x1 = _span(x0, x1, left, right)
    y0, y1 = _span(y0, y1, top, bottom)
    if all(abs(a - b) <= 1e-9 for a, b in zip((x0, y0, x1, y1), values)):
        return authored
    fixed = [round(v, 4) for v in (x0, y0, x1, y1)]
    where = ('the safe area' if safe else 'the frame')
    notes.append(f'box {_fmt_box(values)}→{_fmt_box(fixed)} '
                 f'(kept inside {where}, at least {MIN_BOX_SPAN:g} per axis)')
    return fixed


def _color(value):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(value)):
        raise ValueError('Colors must be #RRGGBB')
    return value.upper()


def _parse_lines(lines, start, end, motion, reveal, fit, problems, notes):
    """Validate line/run arguments; measurement happens later at one size."""
    parsed = []
    count = 0
    timed = start is not None and end is not None
    for li, line in enumerate(lines, 1):
        if not isinstance(line, dict) or set(line) - {'runs', 'size'}:
            problems.append('Each line has runs and an optional size '
                            f'multiplier (line {li})')
            continue
        runs = line.get('runs')
        if not isinstance(runs, list) or not runs:
            problems.append(f'Each line needs at least one run (line {li})')
            continue
        raw_size = line.get('size')
        size = bounded(1 if raw_size is None else raw_size, 'line size',
                       LINE_SIZE_RANGE, fit, problems, notes, f'line {li}')
        made = []
        for ri, run in enumerate(runs, 1):
            where = f'line {li} run {ri}'
            if not isinstance(run, dict) or set(run) - {'text', 'at', 'font', 'italic', 'scale', 'color'}:
                problems.append('A run accepts text, at, font, italic, scale '
                                f'and color ({where})')
                continue
            count += 1
            text = str(run.get('text') or '').strip()
            if not text or len(text) > MAX_RUN_CHARS or '\n' in text:
                problems.append('Use a short literal run; put new lines in '
                                f'separate lines ({where}: 1–{MAX_RUN_CHARS} '
                                'characters)')
                text = text.split('\n')[0][:MAX_RUN_CHARS] or '?'
            family = run.get('font') or 'Inter Display Bold'
            italic = run.get('italic', False)
            if family not in FILES or not isinstance(italic, bool) or (italic and family not in ITALICS):
                problems.append('Choose a bundled font; italic needs a bundled '
                                f'italic face ({where})')
                family, italic = 'Inter Display Bold', False
            raw_scale = run.get('scale')
            scale = bounded(1 if raw_scale is None else raw_scale,
                            'run scale', RUN_SCALE_RANGE, fit, problems,
                            notes, where)
            colour = None
            if run.get('color'):
                try:
                    colour = _color(run['color'])
                except ValueError:
                    problems.append(f'Colors must be #RRGGBB ({where})')
            at = None
            if timed:
                raw_at = run.get('at')
                if raw_at is None:
                    raw_at = start
                try:
                    at = float(raw_at)
                except (TypeError, ValueError):
                    at = float('nan')
                latest = end - .3
                if not math.isfinite(at):
                    problems.append(f'at must be between {start:g} and '
                                    f'{latest:g} ({where})')
                    at = None
                elif at < start and fit == 'auto':
                    notes.append(f'{where} at {at:g}→{start:g} (cues cannot '
                                 'precede the scene start)')
                    at = start
                elif not start <= at <= latest:
                    problems.append(f'at must be between {start:g} and '
                                    f'{latest:g} ({where} "{text[:30]}")')
                    at = None
                if at is not None:
                    at = round(at, 2)
                    # Cue times remain speech-led; reject text that can only flash.
                    minimum = max(.3, len(text.split()) / 5
                                  + (.12 if motion == 'settle' and reveal == 'build' else 0))
                    if end - at + 1e-6 < minimum:
                        problems.append(
                            f'Allow {minimum:.2f}s after the cue for '
                            f'"{text[:30]}" (cue at {at:g}s: end ≥ '
                            f'{at + minimum:.2f} or at ≤ {end - minimum:.2f})')
            made.append(dict(text=text, at=at, font=family, italic=italic,
                             scale=1. if scale is None else scale, color=colour))
        parsed.append(dict(size=1. if size is None else size, runs=made))
    if count > MAX_RUNS:
        problems.append(f'Use at most {MAX_RUNS} runs per scene (got {count})')
    return parsed, count


def _measure(parsed, font_size, leading, short, fit, problems=None):
    """Rows at one font_size: (rows, runs held at the readable size bounds)."""
    rows, held = [], []
    low_px, high_px = RUN_PX_RANGE[0] * short, RUN_PX_RANGE[1] * short
    for li, line in enumerate(parsed, 1):
        px = round(font_size * short * line['size'])
        made = []
        for ri, run in enumerate(line['runs'], 1):
            size = round(px * run['scale'])
            # Judge the authored size, not its pixel rounding: the advertised
            # minimum font_size (and line size/run scale) must stay valid.
            relative = font_size * line['size'] * run['scale']
            if not RUN_PX_RANGE[0] - 1e-9 <= relative <= RUN_PX_RANGE[1] + 1e-9:
                if fit == 'auto':
                    size = min(max(size, math.ceil(low_px)), math.floor(high_px))
                    held.append(f'line {li} run {ri}')
                elif problems is not None:
                    problems.append(f'Run is too small or too large; change its '
                                    f'line size (line {li} run {ri} is '
                                    f'{relative:.3g} of the short edge; '
                                    f'allowed {RUN_PX_RANGE[0]:g}–{RUN_PX_RANGE[1]:g})')
            made.append(dict(run, px=size, width=width(run['text'], run['font'], size,
                                                        italic=run['italic'])))
        gap = width(' ', 'Inter Display Bold', px)
        row_width = sum(r['width'] for r in made) + gap * (len(made) - 1)
        rows.append(dict(runs=made, width=row_width, gap=gap,
                         height=max([r['px'] for r in made] or [px]) * leading))
    return rows, held


def _overflow(rows, room_w, room_h):
    wide = [i for i, row in enumerate(rows, 1) if row['width'] > room_w]
    tall = sum(r['height'] for r in rows) > room_h
    return wide, tall


def compose(*, id, start, end, lines, box=None, align='center',
            reveal='build', motion='settle', color='#F4F2EE',
            font_size=.075, leading=1.16,
            mute_captions=True, W=1080, H=1920, fit='strict'):
    problems, notes = [], []
    if fit not in FITS:
        problems.append("fit must be 'auto' or 'strict'")
        fit = 'strict'
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', str(id)):
        problems.append('id must be 1–48 letters, numbers, underscores or hyphens')
    start = bounded(start, 'start', (0, 86400), 'strict', problems, notes)
    if start is not None:
        end = bounded(end, 'end', (start + .3, 86400), 'strict', problems, notes)
    else:
        end = None
    if align not in ('left', 'center', 'right') or reveal not in ('build', 'still') or motion not in ('settle', 'none'):
        problems.append('Choose a listed alignment, reveal and motion')
    if not isinstance(mute_captions, bool):
        problems.append('mute_captions must be true or false')
    try:
        color = _color(color)
    except ValueError as exc:
        problems.append(str(exc))
        color = '#F4F2EE'
    font_size = bounded(font_size, 'font_size', FONT_SIZE_RANGE, fit, problems, notes)
    leading = bounded(leading, 'leading', LEADING_RANGE, fit, problems, notes)
    x0, y0, x1, y1 = fit_box(box, DEFAULT_BOX, fit, problems, notes, safe=True)
    if not isinstance(lines, list) or not lines:
        problems.append(f'Use 1–{MAX_LINES} deliberate lines')
        raise LayoutRejected(problems)
    if len(lines) > MAX_LINES:
        problems.append(f'Use 1–{MAX_LINES} deliberate lines (got {len(lines)})')
    parsed, count = _parse_lines(lines, start, end, motion, reveal, fit,
                                 problems, notes)
    short = min(W, H)
    room_w, room_h = (x1 - x0) * W - 12, (y1 - y0) * H
    if font_size is None or leading is None:
        raise LayoutRejected(problems)
    rows, held = _measure(parsed, font_size, leading, short, fit,
                          problems if fit != 'auto' else None)
    wide, tall = _overflow(rows, room_w, room_h)
    if (wide or tall) and fit == 'auto':
        # Scale the whole scene uniformly so the authored hierarchy between
        # lines and runs survives; stop at the validated minimum size.
        before = font_size
        for _ in range(40):
            widest = max(r['width'] for r in rows)
            total = sum(r['height'] for r in rows)
            ratio = min(room_w / widest if widest > 0 else 1,
                        room_h / total if total > 0 else 1)
            smaller = max(FONT_SIZE_RANGE[0],
                          math.floor(font_size * min(ratio, .985) * 1e4) / 1e4)
            if smaller >= font_size:
                break
            font_size = smaller
            rows, held = _measure(parsed, font_size, leading, short, fit)
            wide, tall = _overflow(rows, room_w, room_h)
            if not (wide or tall):
                break
        shrink = 1 - font_size / before
        if shrink > 0:
            notes.append(f'font_size {before:g}→{font_size:g} ({shrink:.0%} '
                         'smaller) so every row fits the box'
                         + ('; consider fewer words or a larger box'
                            if shrink > .35 else ''))
    if held:
        notes.append('run size held at the readable '
                     f'{RUN_PX_RANGE[0]:g}–{RUN_PX_RANGE[1]:g} short-edge range: '
                     + ', '.join(held))
    floor = ' even at the minimum font_size' if fit == 'auto' else ''
    for li in wide:
        problems.append('Line exceeds its box; shorten it, break it, or reduce '
                        f'its explicit size (line {li} needs '
                        f'{rows[li - 1]["width"]:.0f}px, the box allows '
                        f'{room_w:.0f}px{floor})')
    if tall:
        problems.append('Lines exceed the scene height; use fewer lines or a '
                        f'taller box (needs {sum(r["height"] for r in rows):.0f}px, '
                        f'the box allows {room_h:.0f}px{floor})')
    if problems:
        raise LayoutRejected(problems)

    prefix = f'ts_{id}__'; items = []; height = sum(r['height'] for r in rows)
    y = (y0 + y1) * H / 2 - height / 2
    for li, row in enumerate(rows):
        cy = (y + row['height'] / 2) / H
        left = (x0 * W if align == 'left' else x1 * W - row['width'] if align == 'right'
                else (x0 + x1) * W / 2 - row['width'] / 2)
        for ri, run in enumerate(row['runs']):
            cx = (left + run['width'] / 2) / W
            item = dict(id=f'{prefix}{li}_{ri}', text=run['text'], start=run['at'], end=end,
                template='title', font=run['font'], italic=run['italic'], text_align='center',
                tracking=0, font_size=run['px'] / short, max_width=min(.96, max(.1, run['width'] / W + .015)),
                x=cx, y=cy, color=run['color'] or color, uppercase=False, box=False,
                outline_width=0, shadow=0, entrance='none', exit='none', mute_captions=mute_captions)
            if reveal == 'still':
                item['start'] = start
            elif motion == 'settle':
                edge = min(.18, (end - run['at']) * .22)
                item['motion'] = {'y': [{'t': 0, 'v': cy + .006}, {'t': edge, 'v': cy, 'ease': 'out'}],
                                  'opacity': [{'t': 0, 'v': 0}, {'t': min(.07, edge), 'v': 1, 'ease': 'out'}]}
            items.append(item)
            left += run['width'] + row['gap']
        y += row['height']
    return {'texts': items, 'prefix': prefix, 'runs': count, 'height_px': height,
            'fit': {'mode': fit, 'font_size': font_size, 'leading': leading,
                    'box': [x0, y0, x1, y1], 'notes': notes},
            'note': 'Cue times are program seconds. Inspect face clearance and the full moving phrase.'}
