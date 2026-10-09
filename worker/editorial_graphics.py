"""Measured editorial compositions that compile to ordinary editable layers.

No generated media, network, synthetic statistics, or baked text. The caller
provides the exact claim and its timeline; this module owns coherent geometry,
hierarchy and a restrained common motion language.
"""
import math
import re

from typography_scenes import FITS, LayoutRejected, bounded, fit_box

KINDS = ("statement", "comparison", "metric", "quote", "chapter", "label", "headline")
# Validator bounds; agent_tools derives the model-visible schema from these.
MAX_TEXT_CHARS = 100
MAX_EYEBROW_CHARS = 32
MAX_SPEAKER_CHARS = 60
HEADLINE_FONT_RANGE = (.035, .085)   # fraction of the canvas short edge
HEADLINE_FONT_SIZE = .052
HEADLINE_MAX_LINES = 3
HEADLINE_LEADING = 1.2
MIN_CARD_FONT = .025
# Card copy wraps in a column CARD_TEXT_COLUMN of the card width, and an EDL
# text column (TextItem.max_width) must be at least .1 of the frame, so a
# card narrower than .1/.84 would compose to a layer the EDL rejects.
CARD_TEXT_COLUMN = .84
CARD_MIN_WIDTH = .12
DEFAULT_BOXES = {"label": (.08, .15, .92, .31), "headline": (.08, .13, .92, .27)}
DEFAULT_BOX = (.07, .22, .93, .73)
PALETTES = {
    "ink": ("#101012", "#F4F2EE", "#A3A3A7", "#B9AB91"),
    "paper": ("#F0EEE8", "#171719", "#626166", "#605644"),
    "slate": ("#172127", "#F1F4F5", "#A7B1B7", "#AFC2C9"),
}


def compose(*, id, kind, text, start, end, secondary=None, eyebrow=None,
            palette="ink", box=None, motion="settle", W=1080, H=1920,
            motion_motif=None, treatment="panel", mute_captions=False,
            speaker=None, font_size=None, fit="strict"):
    """fit='strict' rejects; fit='auto' repairs the box (and a headline's
    size) and reports it in result['fit']['notes']. Every remaining problem
    is raised together as LayoutRejected."""
    problems, notes = [], []
    if fit not in FITS:
        problems.append("fit must be 'auto' or 'strict'")
        fit = "strict"
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", str(id)):
        problems.append("id must be 1–48 letters, numbers, underscores or hyphens")
    if kind not in KINDS or palette not in PALETTES or motion not in ("settle", "none") or treatment not in ("panel", "type"):
        problems.append("Choose a listed composition, palette and motion")
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        start = end = float("nan")
    timed = all(math.isfinite(v) for v in (start,end)) and start >= 0 and end > start
    if not timed:
        problems.append("start/end must be a finite program-time window")
    text = str(text or "").strip()
    secondary = str(secondary or "").strip()
    eyebrow = str(eyebrow or "").strip()
    if not text or len(text)>MAX_TEXT_CHARS or len(secondary)>MAX_TEXT_CHARS or len(eyebrow)>MAX_EYEBROW_CHARS:
        problems.append(f"Keep the main/supporting text to {MAX_TEXT_CHARS} characters each and the eyebrow to {MAX_EYEBROW_CHARS}"
                        f" (got {len(text)}/{len(secondary)}/{len(eyebrow)})")
    if kind == "comparison" and not secondary:
        problems.append("A comparison requires both exact terms")
    words = len((text+" "+secondary+" "+eyebrow).split())
    minimum = max(1.5, words/3.2 + (.45 if motion=="settle" else .15))
    reads = not timed or end-start >= minimum
    if not reads:
        problems.append(f"Allow at least {minimum:.2f}s to read this composition, or shorten the copy"
                        f" (window is {end-start:.2f}s; end ≥ {start+minimum:.2f})")
    if kind == "headline":
        if secondary or eyebrow:
            problems.append("A headline uses speaker and text, without extra supporting labels")
        return _headline(id=id, text=text, speaker=speaker, start=start, end=end,
                         palette=palette if palette in PALETTES else "ink",
                         box=box, font_size=font_size, W=W, H=H, fit=fit,
                         problems=problems, notes=notes, check_reading=reads and timed)
    if speaker is not None or font_size is not None:
        problems.append("speaker and font_size belong to kind=headline")
    box = fit_box(box, DEFAULT_BOXES.get(kind, DEFAULT_BOX), fit, problems,
                  notes, safe=False, min_width=CARD_MIN_WIDTH, label="card box")
    if kind not in KINDS or palette not in PALETTES or not text:
        raise LayoutRejected(problems)
    if not timed:
        start, end = 0., max(minimum, 1.)
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
                 font_size=size,max_width=max(.1,width*CARD_TEXT_COLUMN),uppercase=False,box=False,
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
        if row["font_size"] < MIN_CARD_FONT:
            problems.append("Copy is too dense for this card; shorten it or use a larger box"
                            f" ({key} line)")
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
    if problems:
        raise LayoutRejected(problems)
    return {"texts":texts,"vectors":vectors,"prefix":prefix,"minimum_hold_s":round(minimum,2),
            "fit":{"mode":fit,"box":list(box),"notes":notes}}


def _headline(*, id, text, speaker, start, end, palette, box, font_size, W, H,
              fit="strict", problems=None, notes=None, check_reading=True):
    """A persistent, speaker-first heading with measured wrapping and no panel.

    Identity is supplied from source evidence by the caller, never inferred
    here. Keep the name together and wrap the claim at its requested size.
    strict never shrinks the title to accommodate excess copy; auto may step
    the size down to the validated minimum and reports the applied size.
    """
    from type_metrics import width
    from typography_scenes import compose as typography
    problems = [] if problems is None else problems
    notes = [] if notes is None else notes
    speaker = str(speaker or "").strip()
    if not speaker or len(speaker)>MAX_SPEAKER_CHARS or "\n" in speaker:
        problems.append(f"Provide the verified speaker name (1–{MAX_SPEAKER_CHARS} characters), or use another kind without attribution")
        speaker = speaker.replace("\n", " ")[:MAX_SPEAKER_CHARS] or "?"
    if "\n" in text:
        problems.append("Headline text wraps automatically; omit manual line breaks")
        text = " ".join(text.split())
    size = bounded(HEADLINE_FONT_SIZE if font_size is None else font_size,
                   "Headline font_size", HEADLINE_FONT_RANGE, fit, problems, notes,
                   unit=" of the canvas short edge")
    box = fit_box(box, DEFAULT_BOXES["headline"], fit, problems, notes, safe=True)
    fg,accent = PALETTES[palette][1],PALETTES[palette][3]
    name = speaker.rstrip(":")+":"
    pieces = [(name,accent)] + [(word,fg) for word in text.split()]
    short = min(W,H)
    room = (box[2]-box[0])*W-12
    room_h = (box[3]-box[1])*H

    def wrap(size):
        px = round(short*size)
        rows=[]; row=[]; used=0
        gap=width(" ","Inter Display Bold",px)
        oversize = False
        for word,color in pieces:
            measured=width(word,"Inter Display Bold",px)
            oversize = oversize or measured>room
            if row and used+gap+measured>room:
                rows.append({"runs":row});row=[];used=0
            used += (gap if row else 0)+measured
            row.append({"text":word,"color":color})
        if row:rows.append({"runs":row})
        tall = len(rows)*px*HEADLINE_LEADING > room_h
        return rows, oversize, tall

    if size is None:
        raise LayoutRejected(problems)
    rows, oversize, tall = wrap(size)
    if fit == "auto" and (oversize or len(rows)>HEADLINE_MAX_LINES or tall):
        before = size
        while (oversize or len(rows)>HEADLINE_MAX_LINES or tall) and size > HEADLINE_FONT_RANGE[0]:
            size = max(HEADLINE_FONT_RANGE[0], round(size*.94, 4))
            rows, oversize, tall = wrap(size)
        if size < before:
            notes.append(f"Headline font_size {before:g}→{size:g} so the name and claim fit "
                         f"{len(rows)} line{'s' if len(rows)>1 else ''} inside the box")
    floor = " even at the minimum font_size" if fit == "auto" else ""
    if oversize:
        problems.append("Headline name or word exceeds its box; widen the box or shorten the copy"+floor)
    if len(rows)>HEADLINE_MAX_LINES:
        problems.append("Headline needs more than three lines; shorten the claim instead of shrinking it"
                        f" ({len(rows)} lines{floor})")
    elif tall:
        problems.append("Lines exceed the scene height; use fewer lines or a taller box"
                        f" ({len(rows)} lines need {len(rows)*round(short*size)*HEADLINE_LEADING:.0f}px,"
                        f" the box allows {room_h:.0f}px{floor})")
    minimum=max(2.,len((name+" "+text).split())/3.2+.15)
    if check_reading and end-start<minimum:
        problems.append(f"Allow at least {minimum:.2f}s to read the speaker and headline"
                        f" (window is {end-start:.2f}s; end ≥ {start+minimum:.2f})")
    if problems:
        raise LayoutRejected(problems)
    result=typography(id=id,start=start,end=end,lines=rows,box=box,align="left",
                      reveal="still",motion="none",font_size=size,leading=HEADLINE_LEADING,
                      mute_captions=False,W=W,H=H,fit=fit)
    prefix=f"eg_{id}__"
    for item in result["texts"]:
        item["id"]=item["id"].replace(result["prefix"],prefix,1)
    return {"texts":result["texts"],"vectors":[],"prefix":prefix,
            "minimum_hold_s":round(minimum,2),
            "fit":{"mode":fit,"box":list(box),"font_size":result["fit"]["font_size"],
                   "notes":notes+result["fit"]["notes"]}}
