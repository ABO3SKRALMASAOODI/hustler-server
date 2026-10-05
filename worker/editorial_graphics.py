"""Measured editorial compositions that compile to ordinary editable layers.

No generated media, network, synthetic statistics, or baked text. The caller
provides the exact claim and its timeline; this module owns coherent geometry,
hierarchy and a restrained common motion language.
"""
import math
import re

KINDS = ("statement", "comparison", "metric", "quote", "chapter", "label")
PALETTES = {
    "ink": ("#101012", "#F4F2EE", "#A3A3A7", "#B9AB91"),
    "paper": ("#F0EEE8", "#171719", "#626166", "#605644"),
    "slate": ("#172127", "#F1F4F5", "#A7B1B7", "#AFC2C9"),
}


def compose(*, id, kind, text, start, end, secondary=None, eyebrow=None,
            palette="ink", box=None, motion="settle", W=1080, H=1920,
            motion_motif=None, treatment="panel", mute_captions=False):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", str(id)):
        raise ValueError("id must be 1–48 letters, numbers, underscores or hyphens")
    if kind not in KINDS or palette not in PALETTES or motion not in ("settle", "none") or treatment not in ("panel", "type"):
        raise ValueError("Choose a listed composition, palette and motion")
    start, end = float(start), float(end)
    if not all(math.isfinite(v) for v in (start,end)) or start < 0 or end <= start:
        raise ValueError("start/end must be a finite program-time window")
    text = str(text or "").strip()
    secondary = str(secondary or "").strip()
    eyebrow = str(eyebrow or "").strip()
    if not text or len(text)>100 or len(secondary)>100 or len(eyebrow)>32:
        raise ValueError("Keep the main/supporting text to 100 characters each and the eyebrow to 32")
    if kind == "comparison" and not secondary:
        raise ValueError("A comparison requires both exact terms")
    words = len((text+" "+secondary+" "+eyebrow).split())
    minimum = max(1.5, words/3.2 + (.45 if motion=="settle" else .15))
    if end-start < minimum:
        raise ValueError(f"Allow at least {minimum:.2f}s to read this composition, or shorten the copy")
    from schemas import Frame
    box = Frame._picture_rectangle(box or ([.08,.15,.92,.31] if kind=="label" else [.07,.22,.93,.73]))
    x0,y0,x1,y1 = box
    width,height = x1-x0,y1-y0
    cx,cy=(x0+x1)/2,(y0+y1)/2
    bg,fg,muted,accent=PALETTES[palette]
    prefix=f"eg_{id}__"
    texts,vectors=[],[]
    duration=end-start
    edge=min(.38,duration*.16)

    def movement(y):
        if motion=="none":
            return None
        # All elements share one easing and direction; hierarchy is expressed
        # by type scale and spacing, never four competing entrance presets.
        return {"y":[{"t":0,"v":y+.012},{"t":edge,"v":y,"ease":"out"}],
                "opacity":[{"t":0,"v":0},{"t":edge,"v":1,"ease":"out"},
                           {"t":duration-.18,"v":1},{"t":duration,"v":0}]}

    def line(key, copy, y, size, color=fg, serif=False):
        if not copy:
            return
        # Explicit type size/column width makes a metric and its label, or
        # opposing terms, predictable at portrait, square and wide delivery.
        # Fit from the actual compiler's block bounds, not only character count.
        row=dict(id=prefix+key,text=copy,start=start,end=end,template="title",
                 x=cx,y=y,font="Instrument Serif" if serif else "Inter Display Bold",
                 font_size=size,max_width=width*.84,uppercase=False,box=False,
                 color=color,outline_width=0,shadow=0,entrance="none",exit="none",
                 mute_captions=bool(mute_captions),motion=movement(y),motion_motif=motion_motif)
        from graphics import _compile_item
        if kind == "label":
            height_share = .27 if secondary else .72
        elif kind == "comparison" and key in ("main", "other"):
            height_share = .25
        elif key == "main":
            # Empty supporting rows must not force a sparse two-line headline
            # into the tiny central slot of a three-row card. Reserve space
            # only for content that will actually be rendered.
            height_share = (.36 if eyebrow and secondary else
                            .48 if secondary else .56 if eyebrow else .78)
        else:
            height_share = .13
        max_height = height*H*height_share
        for _ in range(25):
            measured=_compile_item(row,end,(W,H))
            if measured["height"]<=max_height and measured["right"]-measured["left"]<=width*W*.87:
                break
            row["font_size"]*=.94
        if row["font_size"] < .025:
            raise ValueError("Copy is too dense for this card; shorten it or use a larger box")
        texts.append(row)

    if treatment == "panel":
        vectors.append(dict(id=prefix+"panel",kind="rectangle",start=start,end=end,
                        x=cx,y=cy,width=width,height=height,color=bg,
                        rounding=.035 if kind=="label" else .012,
                        stroke_color=muted,stroke_width=.0007,
                        motion=movement(cy),motion_motif=motion_motif))
    if kind=="comparison":
        line("eyebrow",eyebrow,y0+height*.12,.029,accent)
        line("main",text,y0+height*.32,.084)
        line("other",secondary,y0+height*.72,.084)
        vectors.append(dict(id=prefix+"rule",kind="line",start=start,end=end,
                            x=cx,y=cy+height*.02,width=width*.72,height=.001,
                            color=muted,stroke_width=.001,motion=movement(cy+height*.02),
                            motion_motif=motion_motif))
    elif kind=="label":
        line("main",text,y0+height*(.39 if secondary else .5),.047)
        line("detail",secondary,y0+height*.73,.029,muted)
    else:
        line("eyebrow",eyebrow,y0+height*.16,.029,accent)
        main_y = (.48 if eyebrow and secondary else
                  .38 if secondary else .59 if eyebrow else .5)
        line("main",text,y0+height*main_y,.145 if kind=="metric" else .092,
             serif=kind=="quote")
        line("detail",secondary,y0+height*.80,.036,muted)
    return {"texts":texts,"vectors":vectors,"prefix":prefix,"minimum_hold_s":round(minimum,2)}
