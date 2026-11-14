"""A silent 60s picture comparison with separate candidate/reference audio tracks."""
from pathlib import Path
import argparse
import subprocess


def make(candidate,reference,out,start=0,reference_start=0,ffmpeg='ffmpeg'):
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    subprocess.run([ffmpeg,'-v','error','-y','-ss',str(start),'-i',candidate,'-ss',str(reference_start),'-i',reference,
        '-filter_complex',"[0:v]scale=960:540,setsar=1,pad=960:580:0:40:black,drawtext=text='WF4 candidate':fontcolor=white:fontsize=24:x=24:y=8[l];[1:v]scale=960:540,setsar=1,pad=960:580:0:40:black,drawtext=text='Long-form reference':fontcolor=white:fontsize=24:x=24:y=8[r];[l][r]hstack[v]",
        '-map','[v]','-map','0:a','-map','1:a','-t','60','-c:v','libx264','-preset','medium','-crf','19','-c:a','aac','-b:a','192k',
        '-metadata:s:a:0','title=WF4 candidate','-metadata:s:a:1','title=Reference','-movflags','+faststart',out],check=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('candidate');ap.add_argument('reference');ap.add_argument('out')
    ap.add_argument('--start',type=float,default=0);ap.add_argument('--reference-start',type=float,default=0)
    ap.add_argument('--ffmpeg',default='ffmpeg');a=ap.parse_args();make(**vars(a))
