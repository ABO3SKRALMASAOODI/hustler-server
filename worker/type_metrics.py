"""Bundled-font advances in libass font-size units, cached across scenes."""
from functools import lru_cache
from pathlib import Path

FILES = {
    'Inter Display Black': 'InterDisplay-Black.ttf',
    'Inter Display ExtraBold': 'InterDisplay-ExtraBold.ttf',
    'Inter Display Bold': 'InterDisplay-Bold.ttf',
    'Anton': 'Anton-Regular.ttf', 'Bebas Neue': 'BebasNeue-Regular.ttf',
    'Archivo Black': 'ArchivoBlack-Regular.ttf', 'Poppins Black': 'Poppins-Black.ttf',
    'Syne ExtraBold': 'Syne-ExtraBold.ttf',
    'Playfair Display Black': 'PlayfairDisplay-Black.ttf',
    'Instrument Serif': 'InstrumentSerif-Regular.ttf',
    'DM Serif Display': 'DMSerifDisplay-Regular.ttf', 'Montserrat': 'Montserrat-Bold.ttf',
    'Plus Jakarta Sans ExtraBold': 'PlusJakartaSans-ExtraBold.ttf',
}
ITALICS = {
    'Inter Display Bold': 'InterDisplay-BoldItalic.ttf',
    'Playfair Display Black': 'PlayfairDisplay-BlackItalic.ttf',
    'Instrument Serif': 'InstrumentSerif-Italic.ttf',
    'DM Serif Display': 'DMSerifDisplay-Italic.ttf',
}


@lru_cache(maxsize=32)
def _font(family, italic=False):
    from PIL import ImageFont
    name = (ITALICS if italic else {}).get(family) or FILES[family]
    return ImageFont.truetype(str(Path(__file__).parent/'fonts'/name), 2048)


def width(text, family, px, spacing=0, italic=False):
    # libass normalizes requested size against ascent+descent, not the em.
    # A large measuring font avoids integer rounding at mobile preview sizes.
    font = _font(family, italic)
    ascent, descent = font.getmetrics()
    advance = font.getlength(text)*px/(ascent+descent)
    return advance + max(0, len(text)-1)*spacing


def wrap(text, family, px, usable, spacing=0, italic=False):
    result = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = (line+' '+word).strip()
            if line and width(candidate,family,px,spacing,italic)>usable:
                result.append(line); line = word
            else:
                line = candidate
        result.append(line)
    return result
