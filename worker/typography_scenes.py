"""Speech-cued, mixed-font compositions built from editable native text.

Layout is measured once from the COMPLETE scene. New words keep their final
slots; earlier words do not re-centre, replay an entrance or change type size.
The editor supplies the meaning, line breaks and real program cue times.

fit='strict' rejects any out-of-range argument or overflowing layout.
fit='auto' repairs geometry instead: numeric styling is clamped to its range,
the box is clamped into the renderer's safe area, a cue before the scene start
snaps to the start, and font_size is scaled down until every row fits, by at
most AUTO_MAX_SHRINK and never below AUTO_MIN_FONT_SIZE (type stays a
deliberate choice, not a silent casualty of long copy). Every repair is
reported in ``result['fit']['notes']`` so nothing changes silently. Either
way, every remaining problem is raised at once (``LayoutRejected``) so one
correction can fix them all.
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
# fit='auto' may shrink the authored font_size by at most this fraction, and
# never below AUTO_MIN_FONT_SIZE (an authored size already below it is not
# shrunk). Beyond that the call is rejected with the largest size that fits.
AUTO_MAX_SHRINK = .4
AUTO_MIN_FONT_SIZE = .05


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


def bounded(value, name, rng, fit, problems, notes, where='', unit=''):
    """A float inside rng. auto clamps (and notes it); strict records a
    problem. Non-numbers are always problems. Returns None when unusable."""
    low, high = rng
    place = f' ({where})' if where else ''
    try:
        v = float(value)
    except (TypeError, ValueError):
        problems.append(f'{name} must be a number between {low:g} and {high:g}{unit}{place}')
        return None
    if not math.isfinite(v):
        problems.append(f'{name} must be between {low:g} and {high:g}{unit}{place}')
        return None
    if low <= v <= high:
        return v
    if fit == 'auto':
        clamped = min(max(v, low), high)
        notes.append(f'{name}{place} {v:g}→{clamped:g} (allowed {low:g}–{high:g})')
        return clamped
    problems.append(f'{name} must be between {low:g} and {high:g}{unit}{place}')
    return None


def _fmt_box(box):
    return '[' + ','.join(f'{v:g}' for v in box) + ']'


def _span(a, b, low, high, minimum=MIN_BOX_SPAN):
    a, b = min(max(a, low), high), min(max(b, low), high)
    if b - a < minimum - 1e-9:
        mid = min(max((a + b) / 2, low + minimum / 2), high - minimum / 2)
        a, b = mid - minimum / 2, mid + minimum / 2
    return a, b


def fit_box(box, default, fit, problems, notes, safe=True, min_width=MIN_BOX_SPAN,
            min_height=MIN_BOX_SPAN, label='box'):
    """[left, top, right, bottom] frame fractions for a layout region.

    strict: the box must already be a valid picture rectangle (and inside the
    safe area when ``safe``) at least min_width × min_height. auto: reversed
    edges are swapped, edges are clamped into the safe area (or 0..1), and a
    region thinner than its minimum grows around its centre. Pixel-looking
    values are never guessed at."""
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
        for span, minimum, axis in ((x1 - x0, min_width, 'wide'),
                                    (y1 - y0, min_height, 'tall')):
            if span < minimum - 1e-9:
                problems.append(f'{label} must be at least {minimum:g} {axis} '
                                f'(got {span:.3g}); enlarge it')
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
    x0, x1 = _span(x0, x1, left, right, min_width)
    y0, y1 = _span(y0, y1, top, bottom, min_height)
    if all(abs(a - b) <= 1e-9 for a, b in zip((x0, y0, x1, y1), values)):
        return authored
    fixed = [round(v, 4) for v in (x0, y0, x1, y1)]
    where = ('the safe area' if safe else 'the frame')
    least = (f'{min_width:g} per axis' if min_width == min_height else
             f'{min_width:g} wide and {min_height:g} tall')
    notes.append(f'{label} {_fmt_box(values)}→{_fmt_box(fixed)} '
                 f'(kept inside {where}, at least {least})')
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
            # li/ri are the AUTHORED positions: skipped invalid lines or runs
            # must not shift the numbering of later messages.
            made.append(dict(text=text, at=at, font=family, italic=italic,
                             scale=1. if scale is None else scale, color=colour,
                             ri=ri))
        parsed.append(dict(size=1. if size is None else size, runs=made, li=li))
    if count > MAX_RUNS:
        problems.append(f'Use at most {MAX_RUNS} runs per scene (got {count})')
    return parsed, count


def _run_in_range(relative, size, short):
    """A run is readable when EITHER its authored size (font_size × line size
    × run scale) or its rounded pixel size is inside RUN_PX_RANGE. Authored:
    the advertised minimums stay valid despite pixel rounding. Rounded: every
    size the pixel check always accepted stays accepted."""
    low, high = RUN_PX_RANGE
    return (low - 1e-9 <= relative <= high + 1e-9
            or low - 1e-9 <= size / short <= high + 1e-9)


def _measure(parsed, font_size, leading, short, fit, problems=None):
    """Rows at one font_size: (rows, runs held at the readable size bounds)."""
    rows, held = [], []
    low_px, high_px = RUN_PX_RANGE[0] * short, RUN_PX_RANGE[1] * short
    for line in parsed:
        li = line['li']
        px = round(font_size * short * line['size'])
        made = []
        for run in line['runs']:
            ri = run['ri']
            size = round(px * run['scale'])
            relative = font_size * line['size'] * run['scale']
            if not _run_in_range(relative, size, short):
                if fit == 'auto':
                    size = min(max(size, math.ceil(low_px)), math.floor(high_px))
                    held.append(f'line {li} run {ri}')
                elif problems is not None:
                    problems.append(f'Run is too small or too large; change its '
                                    f'line size (line {li} run {ri} is '
                                    f'{relative:.3g} of the short edge; '
                                    f'allowed {RUN_PX_RANGE[0]:g}–{RUN_PX_RANGE[1]:g})')
            # An authored size at the top of the range can round one pixel
            # past it; the emitted layer may never exceed the text maximum.
            size = min(size, math.floor(high_px + 1e-9))
            made.append(dict(run, px=size, width=width(run['text'], run['font'], size,
                                                        italic=run['italic'])))
        gap = width(' ', 'Inter Display Bold', px)
        row_width = sum(r['width'] for r in made) + gap * (len(made) - 1)
        rows.append(dict(runs=made, width=row_width, gap=gap, li=li,
                         height=max([r['px'] for r in made] or [px]) * leading))
    return rows, held


def _overflow(rows, room_w, room_h):
    wide = [row for row in rows if row['width'] > room_w]
    tall = sum(r['height'] for r in rows) > room_h
    return wide, tall


def _shrink(parsed, font_size, floor, leading, short, room_w, room_h):
    """Scale the whole scene uniformly (the authored hierarchy between lines
    and runs survives) until every row fits or ``floor`` is reached.
    Returns (font_size, rows, held, wide, tall)."""
    rows, held = _measure(parsed, font_size, leading, short, 'auto')
    wide, tall = _overflow(rows, room_w, room_h)
    for _ in range(40):
        if not (wide or tall):
            break
        widest = max(r['width'] for r in rows)
        total = sum(r['height'] for r in rows)
        ratio = min(room_w / widest if widest > 0 else 1,
                    room_h / total if total > 0 else 1)
        smaller = max(floor, math.floor(font_size * min(ratio, .985) * 1e4) / 1e4)
        if smaller >= font_size:
            break
        font_size = smaller
        rows, held = _measure(parsed, font_size, leading, short, 'auto')
        wide, tall = _overflow(rows, room_w, room_h)
    return font_size, rows, held, wide, tall


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
    font_size = bounded(font_size, 'font_size', FONT_SIZE_RANGE, fit, problems, notes,
                        unit=' of the canvas short edge')
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
    floor, fits_at = '', None
    if (wide or tall) and fit == 'auto':
        # Small type is a design failure, so auto only trims: at most
        # AUTO_MAX_SHRINK, never below AUTO_MIN_FONT_SIZE. Copy that needs
        # more is rejected with the size that would fit, so the editor
        # consciously shortens it, enlarges the box or accepts that size.
        before = font_size
        limit = max(FONT_SIZE_RANGE[0], before * (1 - AUTO_MAX_SHRINK),
                    min(before, AUTO_MIN_FONT_SIZE))
        font_size, rows, held, wide, tall = _shrink(
            parsed, before, limit, leading, short, room_w, room_h)
        shrink = 1 - font_size / before
        if wide or tall:
            needed, _, _, still_wide, still_tall = _shrink(
                parsed, font_size, FONT_SIZE_RANGE[0], leading, short,
                room_w, room_h)
            if still_wide or still_tall:
                floor = ' even at the minimum font_size'
            else:
                fits_at = needed
                floor = (f' at font_size {font_size:g}; '
                         + (f'fit=auto shrinks at most {AUTO_MAX_SHRINK:.0%} '
                            f'and never below {AUTO_MIN_FONT_SIZE:g}'
                            if limit < before else
                            'fit=auto does not shrink type authored at '
                            f'{AUTO_MIN_FONT_SIZE:g} or smaller'))
        elif shrink > 0:
            notes.append(f'font_size {before:g}→{font_size:g} ({shrink:.0%} '
                         'smaller) so every row fits the box'
                         + ('; consider fewer words or a larger box'
                            if shrink > .25 else ''))
    if held:
        notes.append('run size held at the readable '
                     f'{RUN_PX_RANGE[0]:g}–{RUN_PX_RANGE[1]:g} short-edge range: '
                     + ', '.join(held))
    if wide:
        # One entry for every overflowing row, so the numbered list stays
        # about distinct fixes rather than repeating the same rule per line.
        needs = ', '.join(f'line {row["li"]} needs {row["width"]:.0f}px'
                          for row in wide)
        problems.append('Line exceeds its box; shorten it, break it, or reduce '
                        f'its explicit size ({needs}; the box allows '
                        f'{room_w:.0f}px{floor})')
    if tall:
        problems.append('Lines exceed the scene height; use fewer lines or a '
                        f'taller box (needs {sum(r["height"] for r in rows):.0f}px, '
                        f'the box allows {room_h:.0f}px{floor})')
    if fits_at is not None:
        problems.append(f'As written it fits only at font_size {fits_at:g} '
                        f'({1 - fits_at / before:.0%} below {before:g}): shorten '
                        'the copy, enlarge the box, or pass '
                        f'font_size={fits_at:g} to choose that smaller type')
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
