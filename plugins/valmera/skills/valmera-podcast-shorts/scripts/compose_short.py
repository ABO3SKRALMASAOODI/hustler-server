#!/usr/bin/env python3
"""Compile an initial short plan against its original index, without media work.

Choose cuts, meaning-bearing words and graphics yourself. This removes manual
source/output-clock arithmetic and provides editable, phone-sized typography.
Use only on a fresh shared-source child: later structural revisions must also
remap that child's existing media layers through Valmera's timeline tools.
"""
import argparse
import copy
import json
from pathlib import Path
from design_graphics import compile_design, number


RECIPES={
    'composed':{'preset':'composed','size':'m','size_scale':.72,'layout':'stack',
                'animation':'none','emphasis':'big','emphasis_scale':1.28,
                'leading':1.08,'text_align':'center'},
    'kinetic':{'preset':'reels','size':'m','size_scale':.65,'layout':'stack',
               'animation':'rise','emphasis':'big','emphasis_scale':1.30,
               'leading':1.02,'text_align':'center'},
    'quiet':{'preset':'karaoke','size':'m','size_scale':.68,'layout':'stack',
             'animation':'none','emphasis':'big','emphasis_scale':1.15,
             'text_align':'center'},
    'editorial':{'preset':'editorial','size':'m','size_scale':.72,'layout':'stack',
                 'animation':'fade','emphasis':'big','emphasis_scale':1.22,
                 'text_align':'center'},
}


def compile_short(plan,index):
    source_duration=number(index['video']['duration'],'index duration')
    keep=[]
    for span in plan['keep']:
        if len(span)!=2:raise ValueError('keep spans need source start/end')
        a,b=[number(v,'keep time') for v in span]
        if not 0<=a<b<=source_duration:raise ValueError('keep outside original source')
        if keep and a<keep[-1][1]:raise ValueError('keep spans must be chronological and non-overlapping')
        keep.append([a,b])
    if not keep:raise ValueError('keep must retain actual speech')
    duration=round(sum(b-a for a,b in keep),6)
    def source_window(a,b):
        offset=0
        for x,y in keep:
            if x<=a<b<=y:return round(offset+a-x,6),round(offset+b-x,6)
            offset+=y-x
        raise ValueError('source graphic cue crosses a cut or removed speech; choose its resulting output window explicitly')
    frame={'ratio':'9:16','mode':'crop','picture':[0,.2890625,1,.7109375],
           **plan.get('frame',{})}
    picture=frame['picture']
    if len(picture)!=4:raise ValueError('picture needs four fractions')
    accent=plan.get('accent','#D5C5AA')
    recipe=plan.get('caption_recipe','composed')
    if recipe not in RECIPES:raise ValueError('caption_recipe: composed, kinetic, quiet or editorial')
    # Keep dialogue on the lower picture, not lost in detached black space.
    # These are starting coordinates; the editor must inspect actual faces.
    style={**RECIPES[recipe],'dynamic':True,'highlight_color':accent,
           'color':'#FFFFFF','outline_width':.7,'shadow':1.,
           'anchor_y':round(picture[3]-.055,4),**plan.get('caption_style',{})}
    caps={'mode':'from_transcript','design_version':2,'min_words_per_caption':3,
          'max_words_per_caption':plan.get('max_words_per_caption',5),
          'emphasis_words':plan.get('emphasis_words',[]),'emphasis_mode':'manual','style':style}
    design=copy.deepcopy(plan.get('graphics',{}))
    design.update({'id':plan.get('id','short'),'duration_s':duration,
                   'picture':picture,'accent':accent})
    if plan.get('headline'):
        design['headline']=plan['headline']
    for beat in design.get('beats',[]):
        if 'source_start' in beat or 'source_end' in beat:
            if 'start' in beat or 'end' in beat:raise ValueError('choose source OR output cue times')
            beat['start'],beat['end']=source_window(
                number(beat.pop('source_start'),'source_start'),
                number(beat.pop('source_end'),'source_end'))
    layers=compile_design(design)
    edl={'keep':keep,'frame':frame,'captions':caps,
         'texts':layers['texts'],'vectors':layers['vectors']}
    ops=[{'action':'set','layer':k,'value':edl[k]} for k in ['keep','frame','captions']]
    ops+=layers['operations']
    if len(ops)>64:raise ValueError('too many operations for one batch; simplify the design')
    words=[];offset=0
    for a,b in keep:
        for word in index.get('words',[]):
            x,y=word['t0'],word['t1']
            if a<=(x+y)/2<=b:
                words.append({'w':word['w'],'source_start':x,'source_end':y,
                  'start':round(offset+max(a,x)-a,4),'end':round(offset+min(b,y)-a,4)})
        offset+=b-a
    return {'edl':edl,'operations':ops,'cues':layers['cues'],
            'program_duration_s':duration,'expected_final_duration_s':duration+5,
            'program_words':words,'review':'unreviewed',
            'note':'Fresh shared-source child only. Inspect speech joins, face clearance, phone type and moving result.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan',type=Path);p.add_argument('--index',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=compile_short(json.loads(a.plan.read_text()),json.loads(a.index.read_text()))
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    a.out.with_suffix('.edl.json').write_text(json.dumps(result['edl'],indent=2)+'\n')
    print(json.dumps({'output':str(a.out.resolve()),'operations':len(result['operations']),
                      'program_duration_s':result['program_duration_s']}))

if __name__=='__main__':main()
