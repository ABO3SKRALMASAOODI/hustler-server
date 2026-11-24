"""An unnumbered instrument for a speaker's qualitative analogy."""
import math
from pathlib import Path

from PIL import Image, ImageDraw

from . import kit


def artwork(work):
    folder = Path(work) / "palette" / "analogy-dial"
    folder.mkdir(parents=True, exist_ok=True)
    assets = {}
    for layer in ("face", "needle"):
        path = folder / (layer + ".png")
        if not path.exists():
            im = Image.new("RGBA", (640, 640))
            draw = ImageDraw.Draw(im)
            if layer == "face":
                draw.ellipse((20, 20, 620, 620), fill="#E2DCCD", outline="#B8B09F", width=4)
                draw.ellipse((36, 36, 604, 604), outline="#AFA99C", width=2)
                for index in range(33):
                    angle = math.radians(145 + 250 * index / 32)
                    def point(radius):
                        return (320 + radius * math.cos(angle), 320 + radius * math.sin(angle))
                    major = index % 4 == 0
                    draw.line((point(218 if major else 233), point(249)),
                              fill="#AE5B3C" if index >= 23 else "#344046", width=5 if major else 3)
                draw.arc((57, 57, 583, 583), start=329, end=395, fill="#B76747", width=5)
            else:
                draw.polygon(((316, 119), (324, 119), (330, 342), (310, 342)), fill="#A54E31")
                draw.ellipse((302, 302, 338, 338), fill="#333E40")
                draw.ellipse((312, 312, 328, 328), fill="#D2C2A7")
            im.save(path)
        assets["analogy-dial-" + layer] = dict(kind="image", path=str(path))
    return assets


def dial(l1, work, ask, sense=None):
    options = kit.opts(ask)
    start, end, _ = kit.span(l1, ask, need=48)
    width, height, fps, _ = kit.size(l1)
    change = options.get("change")
    change = min(end-1, max(start, change if type(change) is int else (start+end)//2))
    sign = -1 if options.get("trend") == "down" else 1
    travel = max(1, round(.38 * fps))
    turns = []
    for frame in range(start, end):
        t = min(1., max(0., (frame-change+1)/travel))
        turns.append(sign * (-15 + 75 * t*t*(3-2*t)))
    assets = artwork(work)
    base = dict(k="card", f=[start,end], x=.5, y=.64, w=.45, h=.45*width/height, s=1, op=1)
    items = [dict(base, id=ask["id"]+"-face", pic="analogy-dial-face", rot=0, z=5),
             dict(base, id=ask["id"]+"-needle", pic="analogy-dial-needle", rot=turns, z=6)]
    return dict(items=items, assets=assets, log=dict(change=change, trend=options.get("trend"), f=[start,end]))


DEVICES = {"analogy_dial": dict(layer="hud", family="hud", needs=("words",), plan=dial,
    fallback=(), span=True, words=("change",),
    card="analogy_dial at until change(word ID) trend(up or down). An original unnumbered dial appears when "
         "the speaker names an instrument in an analogy. Its needle moves once, on the word changing its reading.")}
