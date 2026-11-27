"""Draw a sourced editorial explanation. The citation and diagram have separate seats."""
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
import math
import subprocess

from PIL import Image, ImageDraw, ImageFont
import design


from design import face_for


def fallback_faces(panel,source):
    texts=[panel['excerpt'],panel['heading'],source['url'],str(source.get('date') or '')]
    texts.extend(row.get('label','') for row in panel.get('dots',[]))
    texts.extend(row['text'] for row in panel.get('labels',[]))
    base=design.font('InterDisplay-Bold.ttf',24).path
    return sorted({Path(path).name for text in texts if (path:=face_for(text))!=base})


def wrap(text, font, width):
    lines = []
    for word in text.split():
        # Long source URLs and identifiers have no word breaks.
        if font.getlength(word)>width:
            if lines and lines[-1]:lines.append('')
            for char in word:
                if not lines or font.getlength(lines[-1]+char)>width:lines.append(char)
                else:lines[-1]+=char
        elif lines and font.getlength(lines[-1]+' '+word)<=width:
            lines[-1]+=' '+word
        else:
            lines.append(word)
    return [line.strip() for line in lines]


def block(draw, text, box, size, ink):
    x,y,right,bottom=box
    while size>=12:
        font=ImageFont.truetype(face_for(text),size)
        lines=wrap(text,font,right-x)
        if len(lines)*size*1.27<=bottom-y:break
        size-=1
    for line in lines:
        draw.text((x,y),line,font=font,fill=ink,anchor='lt')
        y+=size*1.27


def render(panel, source, frames, output, ffmpeg):
    w,h=1280,720
    paper,ink,accent,muted='#f1ece0','#172b30','#b24624','#596763'
    base=Image.new('RGB',(w,h),paper);d=ImageDraw.Draw(base)
    d.line((64,108,1216,108),fill=ink,width=2)
    block(d,'EDITORIAL DIAGRAM',(68,75,600,101),19,muted)
    block(d,panel['heading'],(66,127,1205,200),44,ink)
    d.line((778,218,778,554),fill='#b4b5a9',width=2)
    block(d,'SOURCE EXCERPT',(811,226,1210,253),18,muted)
    block(d,panel['excerpt'],(810,273,1210,478),34,ink)
    attribution=' · '.join(filter(None,[urlsplit(source['url']).hostname,source.get('date')]))
    block(d,attribution,(811,492,1210,554),21,muted)
    def xy(point):
        return (82+6.52*min(100,max(0,float(point[0]))),
                221+3.15*min(100,max(0,float(point[1]))))
    def caption(draw,x,y,text):
        if not text:return
        font=ImageFont.truetype(face_for(text),21)
        width=font.getlength(text)
        if width>640:font=ImageFont.truetype(face_for(text),max(13,int(21*640/width)))
        width=font.getlength(text)
        x=min(754-width/2,max(64+width/2,x))
        draw.text((x,y),text,font=font,fill=ink,anchor='mm',stroke_width=2,stroke_fill=paper)
    paths=[([xy(p) for p in line['points']],line.get('accent')) for line in panel.get('lines',[]) if len(line.get('points',[]))>=2]
    strokes=[]
    for pts,accented in paths:
        if accented:
            strokes.extend((a,b,math.dist(a,b)) for a,b in zip(pts,pts[1:]))
        else:
            d.line(pts,fill=muted,width=3)
    labels=Image.new('RGBA',base.size)
    text_draw=ImageDraw.Draw(labels)
    dots=panel.get('dots',[])
    for dot in dots:
        x,y=xy((dot['x'],dot['y']))
        caption(text_draw,x,y-18,dot.get('label'))
    for label in panel.get('labels',[]):
        caption(text_draw,*xy((label['x'],label['y'])),label['text'])
    label_box=labels.getbbox()
    if label_box:labels=labels.crop(label_box)
    length=sum(s[2] for s in strokes)
    # The evidence is readable from the cut. Its relationships resolve in spoken order.
    args=[ffmpeg,'-v','error','-y','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{w}x{h}',
          '-framerate','30','-i','-','-an','-threads','2','-c:v','libx264','-pix_fmt','yuv420p',
          '-crf','18','-preset','veryfast','-movflags','+faststart']
    with TemporaryDirectory(dir=output.parent) as tmp:
        staged=Path(tmp)/'panel.mp4'
        with subprocess.Popen(args+[str(staged)],stdin=subprocess.PIPE) as encoder:
            for index in range(frames):
                progress=index/max(1,frames-1)
                im=base.copy();draw=ImageDraw.Draw(im)
                left=length*min(1,.08+1.75*progress)
                for a,b,n in strokes:
                    if left<=0:break
                    t=min(1,left/max(.001,n))
                    draw.line((a,(a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)),fill=accent,width=5)
                    left-=n
                for j,dot in enumerate(dots):
                    x,y=xy((dot['x'],dot['y']))
                    t=min(1,max(0,(progress-.40*j/max(1,len(dots)))*9+.20))
                    radius=6*t
                    draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=accent)
                if label_box:im.paste(labels,label_box,labels)
                encoder.stdin.write(im.tobytes())
            encoder.stdin.close()
            if encoder.wait():raise RuntimeError('editorial diagram encoder failed')
        staged.replace(output)
