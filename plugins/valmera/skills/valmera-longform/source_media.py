"""Recover the selected archive interval, keeping its source, trim and grade."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import os
import shlex
import subprocess

import footage_box


def decoded(path, ffmpeg):
    if not path.is_file():
        return False
    proc = subprocess.run([ffmpeg,'-v','error','-xerror','-threads','2','-i',str(path),
                           '-map','0:v:0','-f','null','-'],capture_output=True)
    return proc.returncode == 0 and not proc.stderr


def original_cut(clip, root, out):
    """The box's retained whole film is read-only. Transfer just this source interval."""
    from footage import cut, fetch
    url = clip['source']['url']
    a,b = clip['time_range']
    local = fetch.local_source(url)
    if local:
        try:
            cut.local(local,a,b,str(out))
            return
        except (RuntimeError,OSError,subprocess.SubprocessError):
            pass
    folder = '/media/raw-media/whole/'+hashlib.sha256(url.encode()).hexdigest()[:16]
    # The same batch image that acquired the film contains its decoder.
    try:
        images = footage_box.ssh("docker ps --format '{{.Names}} {{.Image}}'",capture_output=True,text=True).stdout
    except (OSError,subprocess.SubprocessError):
        images = ''
    image = next((line.split(' ',1)[1] for line in images.splitlines() if line.startswith('vbox-batch')),None)
    if image:
        cache = footage_box.cache_path(root)
        cmd = ['docker','run','--rm','--network','none','--cpus','2','--memory','2g',
               '-v',cache+':/media:ro','--entrypoint','sh',image,'-c',
               'for f in "$1"/whole.*; do test -f "$f" || continue; exec ffmpeg -v error -xerror -ss "$2" -i "$f" -t "$3" -an -c:v libx264 -crf 12 -preset veryfast -pix_fmt yuv420p -f matroska -; done; exit 1',
               'recut',folder,str(a),str(b-a)]
        try:
            with out.open('wb') as stream:
                footage_box.ssh(shlex.join(cmd),stdout=stream,stderr=subprocess.PIPE,timeout=180)
            return
        except (subprocess.SubprocessError,OSError):
            pass
    # Missing cache or damaged download: reopen the original URL at the recorded times.
    cut.grab(fetch.streams(url,local=False),url,a,b,str(out))


def restore(ctx, shots):
    import cinema as C
    root=Path(ctx.supply)
    receipt=root/'source-media.json'
    checks=C.read(receipt) if receipt.exists() else {}
    unavailable={}
    for row in shots:
        clip=row['clip']
        if clip.get('kind')=='image' or not clip.get('file'):
            continue
        path=Path(clip['path'])
        stamp=lambda: [path.stat().st_size,path.stat().st_mtime_ns] if path.exists() else None
        key=str(path)
        if key in unavailable or (key in checks and checks[key]['file']==stamp()):
            continue
        if decoded(path,C.PX.FF):
            checks[key]=dict(file=stamp())
        else:
            try:
                path.parent.mkdir(parents=True,exist_ok=True)
                with TemporaryDirectory(dir=path.parent) as tmp:
                    raw=Path(tmp)/'cut.mp4'
                    original_cut(clip,root/'footage',raw)
                    output=Path(tmp)/'finished.mp4'
                    from footage import cut
                    cut.finish(str(raw),str(output),clip['w'],clip['h'],clip.get('x',.5),
                               clip.get('eq'),trim=clip.get('trim'))
                    if not decoded(output,C.PX.FF):
                        raise ValueError('recut did not decode')
                    os.replace(output,path)
                checks[key]=dict(file=stamp(),source=clip['source']['url'],time_range=clip['time_range'])
            except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as exc:
                unavailable[key]=str(exc)[:300]
        C.save(receipt,checks)
    return unavailable
