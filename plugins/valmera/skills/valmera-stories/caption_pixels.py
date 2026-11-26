"""Measure planned glyph shapes in encoded picture and paint any lost caption sprites."""
import os
import subprocess
import tempfile
from collections import defaultdict

import cv2
import numpy as np
from PIL import Image, ImageColor, ImageFilter
from scene.plan import A
from scene.type_atlas import build_sprite


def geometry(item, size, frame, sprite):
    width, height = size
    mask, meta = sprite
    lo = item["f"][0]
    scale = float(A(item.get("s", 1.), frame, lo))
    px = A(item["x"], frame, lo)*width + meta["dx"]
    py = A(item["y"], frame, lo)*height
    x = px + meta["cx"] + (meta["x0"] - meta["cx"])*scale
    y = py + meta["cy"] + (meta["y0"] - meta["cy"])*scale
    return (round(x/2), round(y/2)), mask.resize(
        (max(1,round(meta["w"]*scale/2)), max(1,round(meta["h"]*scale/2))), Image.Resampling.LANCZOS)


def visible(frame, mask, pos):
    """Test the actual letter shapes against their immediate surroundings, allowing codec blur."""
    ink = np.asarray(mask)
    x,y = pos
    h,w = ink.shape
    left,top = max(x,0),max(y,0)
    right,bottom = min(x+w,frame.shape[1]),min(y+h,frame.shape[0])
    if right <= left or bottom <= top:
        return False
    ink = ink[top-y:bottom-y,left-x:right-x]
    roi = frame[top:bottom,left:right].astype(np.float32)
    inside = ink > 135
    ring = (cv2.dilate(ink,np.ones((5,5),np.uint8)) > 100) & (ink < 40)
    if not inside.any() or not ring.any():
        return False
    # Coloured and dark captions are both allowed. The statistic remains zero
    # on a plain black seat that the old ground-only contrast probe accepted.
    return abs(float(np.median(roi[inside])-np.median(roi[ring]))) >= 14


def missing_ink(film, frames, sprites, size, ffmpeg):
    w,h = [int(n)//2 for n in size]
    pick = "+".join(f"eq(n\\,{f})" for f in sorted(frames))
    cmd = [ffmpeg,"-v","error","-i",film,"-vf",f"select={pick},scale={w}:{h}","-fps_mode","vfr",
           "-an","-pix_fmt","gray","-f","rawvideo","pipe:1"]
    missing = []
    with subprocess.Popen(cmd,stdout=subprocess.PIPE) as reader:
        for f, items in sorted(frames.items()):
            data = reader.stdout.read(w*h)
            if len(data) != w*h:
                raise IOError(f"Incomplete encoded caption sample at frame {f}")
            frame = np.frombuffer(data,np.uint8).reshape(h,w)
            for it in items:
                pos,mask = geometry(it,size,f,sprites[it["v"],it["t"]])
                if not visible(frame,mask,pos):
                    missing.append(it)
        if reader.wait():
            raise IOError("Could not decode caption samples")
    return missing


def repair(film, doc, ffmpeg):
    captions = [it for it in doc["items"] if it["k"] == "word" and it.get("role") == "cap" and it["t"].strip()]
    size = doc.get("frame") or [1080,1920]
    w,h = [int(n)//2 for n in size]
    frames = defaultdict(list)
    sprites = {}
    for it in captions:
        key = it["v"], it["t"]
        if key not in sprites:
            arr, meta = build_sprite(it["t"], doc["voices"][it["v"]], size[0], 1, 0)
            sprites[key] = Image.fromarray(arr[:,:,0]), meta
        a,b = it["f"]
        f = (max(a,it.get("on",a)) + b-1)//2
        if a <= f < b and A(it.get("op",1.), f, a) > .7:
            frames[f].append(it)
    if not frames:
        return dict(sampled=0, repaired=0, unreadable=0)
    missing = missing_ink(film,frames,sprites,size,ffmpeg)
    remaining = []
    if missing:
        # Only the missing items go through the independent CPU lettering path.
        # Keep their authored clock, face, colour and seat and copy the original audio.
        with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(film))) as tmp:
            result = os.path.join(tmp,"captioned.mp4")
            cmd = [ffmpeg,"-v","error","-i",film,"-f","rawvideo","-pix_fmt","rgba","-s",f"{w}x{h}",
                   "-r",str(doc["fps"]),"-i","pipe:0","-filter_complex",
                   f"[1:v]scale={size[0]}:{size[1]}[ink];[0:v][ink]overlay=eof_action=pass[v]",
                   "-map","[v]","-map","0:a?","-c:a","copy","-c:v","libx264","-preset","veryfast","-crf","17",result]
            blank = bytes(w*h*4)
            with subprocess.Popen(cmd,stdin=subprocess.PIPE) as writer:
                for f in range(doc["dur_f"]):
                    layer = None
                    for it in missing:
                        a,b = it["f"]
                        if not max(a,it.get("on",a)) <= f < b:
                            continue
                        if layer is None:
                            layer = Image.new("RGBA",(w,h))
                        pos,mask = geometry(it,size,f,sprites[it["v"],it["t"]])
                        alpha = float(A(it.get("op",1.),f,a))
                        mask = mask.point(lambda v: v*alpha)
                        shadow = mask.filter(ImageFilter.MaxFilter(3))
                        tile = Image.new("RGBA",mask.size,(20,20,20)); tile.putalpha(shadow)
                        fill = Image.new("RGBA",mask.size,ImageColor.getrgb(it.get("ink","#FFFFFF")))
                        fill.putalpha(mask)
                        tile.alpha_composite(fill)
                        layer.alpha_composite(tile,pos)
                    writer.stdin.write(layer.tobytes() if layer else blank)
                writer.stdin.close()
                if writer.wait():
                    raise IOError("Caption repair encode failed")
            damaged = {it["id"] for it in missing}
            review = {f: [it for it in items if it["id"] in damaged] for f,items in frames.items()}
            remaining = missing_ink(result,{f: items for f,items in review.items() if items},sprites,size,ffmpeg)
            os.replace(result,film)
    return dict(sampled=sum(map(len,frames.values())),repaired=len(missing),
                items=[it["id"] for it in missing], unreadable=len(remaining))
