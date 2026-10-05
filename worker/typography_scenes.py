"""Speech-cued, mixed-font compositions built from editable native text.

Layout is measured once from the COMPLETE scene. New words keep their final
slots; earlier words do not re-centre, replay an entrance or change type size.
The editor supplies the meaning, line breaks and real program cue times.
"""
import math
import re

from type_metrics import FILES, ITALICS, width


def _number(value, name, low, high):
    v = float(value)
    if not math.isfinite(v) or not low <= v <= high:
        raise ValueError(f'{name} must be between {low:g} and {high:g}')
    return v


def _color(value):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(value)):
        raise ValueError('Colors must be #RRGGBB')
    return value.upper()


def compose(*, id, start, end, lines, box=None, align='center',
            reveal='build', motion='settle', color='#F4F2EE',
            font_size=.075, leading=1.16,
            mute_captions=True, W=1080, H=1920):
    from schemas import Frame
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', str(id)):
        raise ValueError('id must be 1–48 letters, numbers, underscores or hyphens')
    start = _number(start,'start',0,86400)
    end = _number(end,'end',start+.3,86400)
    if align not in ('left','center','right') or reveal not in ('build','still') or motion not in ('settle','none'):
        raise ValueError('Choose a listed alignment, reveal and motion')
    if not isinstance(mute_captions,bool):
        raise ValueError('mute_captions must be true or false')
    if not isinstance(lines,list) or not 1 <= len(lines) <= 6:
        raise ValueError('Use 1–6 deliberate lines')
    color = _color(color)
    font_size = _number(font_size,'font_size',.03,.2)
    leading = _number(leading,'leading',1.05,1.8)
    x0,y0,x1,y1 = Frame._picture_rectangle(box or [.08,.28,.92,.66])
    # Match the renderer's edge-safe region rather than silently moving a
    # deliberately authored left/right composition to a different location.
    if x0 < .045 or x1 > .955 or y0 < .04 or y1 > .96:
        raise ValueError('Keep the scene inside the frame safe area')
    short = min(W,H)
    rows=[]; count=0
    for line in lines:
        if not isinstance(line,dict) or set(line)-{'runs','size'}:
            raise ValueError('Each line has runs and an optional size multiplier')
        runs=line.get('runs')
        if not isinstance(runs,list) or not runs:
            raise ValueError('Each line needs at least one run')
        px=round(font_size*short*_number(line.get('size',1),'line size',.6,1.8))
        made=[]
        for run in runs:
            if not isinstance(run,dict) or set(run)-{'text','at','font','italic','scale','color'}:
                raise ValueError('A run accepts text, at, font, italic, scale and color')
            text=str(run.get('text') or '').strip()
            if not text or len(text)>100 or '\n' in text:
                raise ValueError('Use a short literal run; put new lines in separate lines')
            family=run.get('font') or 'Inter Display Bold'
            italic=run.get('italic',False)
            if family not in FILES or not isinstance(italic,bool) or (italic and family not in ITALICS):
                raise ValueError('Choose a bundled font; italic needs a bundled italic face')
            size=round(px*_number(run.get('scale',1),'run scale',.6,1.8))
            if not .03 <= size/short <= .3:
                raise ValueError('Run is too small or too large; change its line size')
            at = round(_number(run.get('at',start),'at',start,end-.3),2)
            remaining=end-at
            # Cue times remain speech-led; reject text that can only flash.
            minimum=max(.3, len(text.split())/5 + (.12 if motion=='settle' and reveal=='build' else 0))
            if remaining+1e-6 < minimum:
                raise ValueError(f'Allow {minimum:.2f}s after the cue for "{text[:30]}"')
            made.append(dict(text=text,at=at,font=family,italic=italic,px=size,
                             color=_color(run.get('color') or color),
                             width=width(text,family,size,italic=italic)))
            count+=1
        if count>32:
            raise ValueError('Use at most 32 runs per scene')
        gap=width(' ', 'Inter Display Bold', px)
        row_width=sum(r['width'] for r in made)+gap*(len(made)-1)
        if row_width > (x1-x0)*W-12:
            raise ValueError('Line exceeds its box; shorten it, break it, or reduce its explicit size')
        rows.append(dict(runs=made,width=row_width,gap=gap,height=max(r['px'] for r in made)*leading))
    height=sum(r['height'] for r in rows)
    if height>(y1-y0)*H:
        raise ValueError('Lines exceed the scene height; use fewer lines or a taller box')
    prefix=f'ts_{id}__'; items=[]; y=(y0+y1)*H/2-height/2
    for li,row in enumerate(rows):
        cy=(y+row['height']/2)/H
        left=(x0*W if align=='left' else x1*W-row['width'] if align=='right'
              else (x0+x1)*W/2-row['width']/2)
        for ri,run in enumerate(row['runs']):
            cx=(left+run['width']/2)/W
            item=dict(id=f'{prefix}{li}_{ri}',text=run['text'],start=run['at'],end=end,
                template='title',font=run['font'],italic=run['italic'],text_align='center',
                tracking=0,font_size=run['px']/short,max_width=min(.96,max(.1,run['width']/W+.015)),
                x=cx,y=cy,color=run['color'],uppercase=False,box=False,
                outline_width=0,shadow=0,entrance='none',exit='none',mute_captions=mute_captions)
            if reveal=='still':
                item['start']=start
            elif motion=='settle':
                edge=min(.18,(end-run['at'])*.22)
                item['motion']={'y':[{'t':0,'v':cy+.006}, {'t':edge,'v':cy,'ease':'out'}],
                                'opacity':[{'t':0,'v':0},{'t':min(.07,edge),'v':1,'ease':'out'}]}
            items.append(item)
            left+=run['width']+row['gap']
        y+=row['height']
    return {'texts':items,'prefix':prefix,'runs':count,'height_px':height,
            'note':'Cue times are program seconds. Inspect face clearance and the full moving phrase.'}
