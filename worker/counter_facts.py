"""Keep calendar ranges from acquiring an invented duration unit (#97)."""
import re
import unicodedata


def _plain(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text or '').casefold())
                   if not unicodedata.combining(c))


def candidates(before, after):
    old = {m.get('id'): m for m in (before or {}).get('motion') or []}
    found = []
    for item in (after or {}).get('motion') or []:
        p = item.get('params') or {}
        if item.get('template') != 'counter':
            continue
        span = re.fullmatch(r'\s*(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*', str(p.get('value') or ''))
        if not span or not all(1 <= int(n) <= 31 for n in span.groups()):
            continue
        if not re.search(r'\b(?:days?|dias?|jours?|tage?)\b', _plain(p.get('label'))):
            continue
        previous = old.get(item.get('id')) or {}
        pp = previous.get('params') or {}
        if previous.get('template') == 'counter' and all(
                pp.get(k) == p.get(k) for k in ('value', 'label')) \
                and all(previous.get(k) == item.get(k) for k in ('start', 'end')):
            continue
        found.append((item, span.groups()))
    return found


def check_write(before, after, words, source_to_output, user_text=''):
    for item, (a, b) in candidates(before, after):
        # An explicit customer-supplied duration is their content. This guard
        # prevents the editor from inventing it from spoken calendar dates.
        duration = rf'\b{a}\s*(?:[-–—]|to|a|ate|bis)\s*{b}\s+(?:days?|dias?|jours?|tage?)\b'
        if re.search(duration, _plain(user_text)):
            continue
        heard = []
        for word in words or []:
            try:
                at = source_to_output(float(word.get('t0', word.get('s'))))
            except (TypeError, ValueError):
                continue
            if at is not None and float(item['start']) - 8 <= at <= float(item['end']) + 3:
                heard.append(str(word.get('w') or ''))
        text = _plain(' '.join(heard))
        day = r'(?:dia|day|date|jour|tag)'
        dates = rf'\b{day}\s+{a}\b[^.!?]{{0,35}}\b{day}\s+{b}\b'
        ordinals = rf'\b{a}(?:st|nd|rd|th)\b[^.!?]{{0,35}}\b{b}(?:st|nd|rd|th)\b'
        if re.search(dates, text) or re.search(ordinals, text):
            return (f'The speech gives calendar dates {a}–{b}, not a duration of '
                    f'{a}–{b} days. Keep the range with a neutral label quoting the '
                    'promotion, or use a date text card. Do not add an unspoken month or duration unit.')
    return None
