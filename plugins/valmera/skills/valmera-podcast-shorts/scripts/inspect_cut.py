#!/usr/bin/env python3
"""Build one bounded, checksum-bound inspection packet from an existing MP4.

It measures media and extracts review evidence; it never declares an edit good.
Repeated unchanged requests reuse the packet. No ASR, uploads, or source edits.
"""
from __future__ import annotations
import argparse, hashlib, io, json, math, shutil, subprocess
from pathlib import Path
from fractions import Fraction
from PIL import Image, ImageDraw


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(4*1024*1024),b''):h.update(data)
    return h.hexdigest()


def sample_times(duration, cues):
    end=max(.02,duration-.08)
    baseline=[.04]+[duration*i/7 for i in range(1,7)]+[end]
    boundaries=[]
    for c in cues:
        for t in (c['start'],c['end']):
            if not isinstance(t,(int,float)) or not math.isfinite(t):raise ValueError('cue times must be finite')
            boundaries += [max(.02,t-.08),min(end,t+.1)]
    if len(boundaries)>32:raise ValueError('More than 8 distinct graphic windows: inspect in scoped groups instead of extracting hundreds of frames')
    return sorted({round(min(end,max(.02,t)),3) for t in baseline+boundaries})


def inspect(video, out, program_duration=None, cues=None, ffmpeg='ffmpeg', ffprobe='ffprobe'):
    video=Path(video).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    checksum=digest(video);cues=cues or []
    key={'video_sha256':checksum,'program_duration':program_duration,'cues':cues,'version':2}
    report_path=out/'inspection.json'
    if report_path.exists():
        old=json.loads(report_path.read_text())
        if old.get('request')==key and all(Path(p).exists() for p in old.get('contact_sheets',[])):
            return {**old,'reused':True}
    probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(video)],timeout=30))
    duration=float(probe['format']['duration'])
    vstream=next(s for s in probe['streams'] if s.get('codec_type')=='video')
    try:frame_step=1/float(Fraction(vstream.get('avg_frame_rate') or vstream['r_frame_rate']))
    except (ValueError,ZeroDivisionError):frame_step=.08
    last_frame=max(0,float(vstream.get('duration') or duration)-max(.08,frame_step*1.1))
    program=program_duration if program_duration is not None else duration
    if not 0<program<=duration+.08:raise ValueError('program duration must be within the video')
    times=sample_times(program,cues)
    # An encoded final includes the brand card: see its beginning and settled state.
    if duration-program>1:times += [round(program+.2,3),round(duration-.3,3)]
    times=sorted({round(min(last_frame,t),3) for t in times})
    sheets=[];draw=None
    for i,t in enumerate(times):
        raw=subprocess.check_output([ffmpeg,'-v','error','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=270:480:force_original_aspect_ratio=decrease','-f','image2pipe','-c:v','png','-threads','1','-'],timeout=25)
        image=Image.open(io.BytesIO(raw)).convert('RGB')
        pos=i%12
        if pos==0:
            sheet=Image.new('RGB',(1120,1560),'#171717');draw=ImageDraw.Draw(sheet)
        x=(pos%4)*280;y=(pos//4)*520
        sheet.paste(image,(x+(280-image.width)//2,y+27))
        draw.text((x+8,y+7),f'{t:.3f} s',fill='white')
        if pos==11 or i==len(times)-1:
            dest=out/f'contact-{i//12+1:02d}.jpg';sheet.save(dest,quality=91);sheets.append(str(dest))
    # One full decode checks stream integrity and reports candidate intervals.
    # Deliberate bars, cards and silence require the editor's interpretation.
    command=[ffmpeg,'-hide_banner','-nostats','-i',str(video),'-vf','blackdetect=d=0.3:pic_th=0.97:pix_th=0.07,freezedetect=n=-50dB:d=1.5','-af','silencedetect=noise=-48dB:d=0.5','-f','null','-']
    result=subprocess.run(command,capture_output=True,text=True,timeout=max(90,duration*5))
    findings=[l for l in result.stderr.splitlines() if any(k in l for k in ['black_start:','freeze_start:','freeze_end:','silence_start:','silence_end:','Error','Invalid'])]
    value={'request':key,'file':str(video),'sha256':checksum,'duration_s':duration,
           'streams':[{k:s.get(k) for k in ['codec_type','codec_name','width','height','r_frame_rate','sample_rate','channels']} for s in probe['streams']],
           'decode_ok':result.returncode==0,'detector_findings':findings,
           'sample_times_s':times,'contact_sheets':sheets,
           'judgment':'unreviewed','limitations':'Samples do not prove motion quality, speech accuracy or synchronization. Review actual playback/audio and the design cues.'}
    report_path.write_text(json.dumps(value,indent=2)+'\n')
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--program-duration',type=float);p.add_argument('--cues',type=Path)
    p.add_argument('--ffmpeg',default=shutil.which('ffmpeg') or 'ffmpeg')
    p.add_argument('--ffprobe',default=shutil.which('ffprobe') or 'ffprobe')
    a=p.parse_args();c=json.loads(a.cues.read_text()) if a.cues else []
    if isinstance(c,dict):c=c.get('cues',c.get('beats',[]))
    print(json.dumps(inspect(a.video,a.out,a.program_duration,c,a.ffmpeg,a.ffprobe),indent=2))

if __name__=='__main__':main()
