#!/usr/bin/env python3
"""Compile a small editorial storyboard to editable Valmera text/vector layers.

No media encoding, uploads, transcript changes or fabricated quality verdicts.
Use the returned operations with apply_edit_batch at the current EDL version.
"""
from __future__ import annotations
import argparse
import json
import math
import re
from pathlib import Path


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def copy_text(value, label, limit):
    if not isinstance(value,str) or not value.strip():
        raise ValueError(f'{label} needs actual story-specific words')
    if len(value.split()) > limit:
        raise ValueError(f'{label} is too long for a phone graphic; shorten the idea, not the type')
    return value.strip()


def settle(y, duration):
    end = min(.24, duration*.2)
    return {'y':[{'t':0,'v':y+.018},{'t':end,'v':y,'ease':'out'}],
            'scale':[{'t':0,'v':.93},{'t':end,'v':1,'ease':'out'}],
            'opacity':[{'t':0,'v':0},{'t':min(.1,end),'v':1},
                       {'t':duration-.08,'v':1},{'t':duration,'v':0}]}


def compile_design(design, existing=None):
    """Four compositional primitives, with agent-authored words, cues and palette."""
    duration=number(design['duration_s'],'duration_s')
    if duration <= 0: raise ValueError('duration_s must be positive')
    prefix=design.get('id','story')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',prefix): raise ValueError('invalid design id')
    prefix='design-'+prefix+'-'
    accent=design.get('accent','#BDF76A')
    background=design.get('background','#101411')
    for c in [accent,background]:
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}',c): raise ValueError('colors must be #RRGGBB')
    picture=design.get('picture',[0,.2890625,1,.7109375])
    if len(picture)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in picture):
        raise ValueError('picture needs four finite frame fractions')
    x0,y0,x1,y1=picture
    if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1: raise ValueError('invalid picture rectangle')
    cx=(x0+x1)/2; cy=(y0+y1)/2; height=y1-y0
    texts=[]; vectors=[]; cues=[]

    def text(id, words, start, end, y, scale=.65, color='#FFFFFF', motion=True, mute=True):
        item={'id':prefix+id,'text':words,'start':start,'end':end,'template':'title',
              'x':cx,'y':y,'size_scale':scale,'font':design.get('font','Inter Display Black'),
              'color':color,'uppercase':False,'box':False,'mute_captions':mute,
              'outline_width':0,'shadow':0,
              'entrance':'none','exit':'none'}
        if motion:item['motion']=settle(y,end-start)
        texts.append(item)

    if design.get('headline'):
        # A persistent title coexists with dialogue; never encode a PNG just
        # to avoid caption suppression. Its text remains readable at frame 1.
        h=design['headline']
        text('headline',copy_text(h['text'],'headline',12),0,duration,
             number(h.get('y',y0-.057),'headline y'),h.get('scale',.47),
             motion=False,mute=False)

    for i,beat in enumerate(design.get('beats',[])):
        a=number(beat['start'],'start');b=number(beat['end'],'end')
        if not 0 <= a < b <= duration:raise ValueError('graphic window outside program')
        kind=beat['kind']; stem=f'b{i}'
        # This is a picture cutaway over uninterrupted original dialogue.
        # A word hit may deliberately live over the footage with panel=false.
        panel=beat.get('panel',True)
        if panel:
            vectors.append({'id':prefix+stem+'-panel','kind':'rectangle','start':a,'end':b,
                            'x':cx,'y':cy,'width':x1-x0,'height':height,
                            'color':background,'opacity':1})
        start_count=len(texts)
        if kind=='word':
            words=copy_text(beat['text'],'word hit',4)
            text(stem+'-word',words,a,b,cy,1.0,accent)
        elif kind=='statement':
            words=copy_text(beat['text'],'statement',8)
            text(stem+'-statement',words,a,b,cy,.72)
        elif kind=='contrast':
            first=copy_text(beat['before'],'before',4)
            second=copy_text(beat['after'],'after',4)
            text(stem+'-before',first,a,b,cy-height*.2,.58,'#BBBBBB')
            text(stem+'-after',second,a+min(.22,(b-a)*.12),b,cy+height*.16,.8,accent)
            vectors.append({'id':prefix+stem+'-rule','kind':'line','start':a,'end':b,
                            'x':cx,'y':cy-height*.035,'width':.5*(x1-x0),'height':.001,
                            'color':accent,'stroke_width':.003,'opacity':.8,
                            'motion':{'scale':[{'t':0,'v':.05},{'t':min(.25,(b-a)/4),'v':1,'ease':'out'}]}})
            words=first+' '+second
        elif kind=='metric':
            value=copy_text(beat['value'],'metric value',3)
            label=copy_text(beat['label'],'metric meaning',6)
            text(stem+'-value',value,a,b,cy-height*.1,1.2,accent)
            text(stem+'-label',label,a+.12,b,cy+height*.22,.44)
            words=value+' '+label
        else:raise ValueError('kind must be word, statement, contrast or metric')
        # Catch the actual observed failure: a sentence plus fine print shown
        # for .8 s. Agents can choose another primitive or more reading time.
        minimum=max(.65,len(words.split())/4.5+.25)
        if b-a < minimum:
            raise ValueError(f'beat {i} needs at least {minimum:.2f}s to read {len(words.split())} words')
        if beat.get('keep_captions',False):
            for tx in texts[start_count:]:tx['mute_captions']=False
        cues.append({'kind':kind,'start':a,'end':b,'text':words,
                     'purpose':beat.get('purpose'),'cue':beat.get('cue'),
                     'minimum_reading_s':round(minimum,3)})

    operations=[]
    current_ids={item['id'] for item in texts+vectors}
    for layer in ['texts','vectors']:
        for old in (existing or {}).get(layer,[]):
            if old.get('id','').startswith(prefix) and old['id'] not in current_ids:
                operations.append({'action':'remove','layer':layer,'id':old['id']})
    for layer,items in [('vectors',vectors),('texts',texts)]:
        operations += [{'action':'upsert','layer':layer,'id':item['id'],'value':item} for item in items]
    if len(operations)>64:raise ValueError('too many graphic operations; simplify the sequence')
    return {'operations':operations,'cues':cues,'texts':texts,'vectors':vectors,
            'note':'Authoring only. Inspect actual moving pixels and speech cues before approval.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('design',type=Path);ap.add_argument('--edl',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    edl=json.loads(args.edl.read_text()) if args.edl else None
    if edl:edl=edl.get('json',edl.get('edl',edl))
    result=compile_design(json.loads(args.design.read_text()),edl)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'output':str(args.out.resolve()),'operations':len(result['operations']),'cues':result['cues']}))

if __name__=='__main__': main()
