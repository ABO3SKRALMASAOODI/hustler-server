"""Splice and level original voices inside the installed narrator environment."""
import contextlib
import io
import json
import hashlib
import wave
from pathlib import Path
import sys
from types import SimpleNamespace


def snapshot(job):
    nd=Path(job['narration_dir'])
    paths=[nd/name for name in ('read-stage.json','narration_narrator.wav','words_narrator.json',
                                'narration.wav','words.json','footage_voices.json','footage_voice_levels.json')]
    paths += [Path(job['footage_dir'])/v['wav'] for v in job['voices']]
    hashes={}
    for p in paths:
        with p.open('rb') as f:hashes[str(p)]=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(job=job,sha256=hashes)


def completed(job,saved_job):
    """Resume the spliced clock only when its inputs and completed outputs agree.

    Older successful runs have no hash stamp. Their final level report must
    follow all outputs, describe every placement and match the exact saved job.
    Adopting that checkpoint never bypasses source or encoded-film checks.
    """
    nd=Path(job['narration_dir']);stamp=nd/'witness-complete.json'
    try:
        if json.loads(Path(saved_job).read_text())!=job or not job['voices']:return False
        current=snapshot(job)
        if stamp.exists():return json.loads(stamp.read_text())==current
        words=json.loads((nd/'words.json').read_text())
        placed=json.loads((nd/'footage_voices.json').read_text())
        levels=json.loads((nd/'footage_voice_levels.json').read_text())['levelled']
        if words.get('voices')!=placed or len(placed)!=len(job['voices']):return False
        if [(v['start'],v['end']) for v in levels]!=[(v['start'],v['end']) for v in placed]:return False
        for v,p in zip(sorted(job['voices'],key=lambda v:v['after']),placed):
            if any(v[k]!=p[k] for k in ('after','person','said','source','time_range')):return False
            if any(str(Path(job['footage_dir'])/v[k])!=p[k] for k in ('file','wav')):return False
        if any(w.get('voice') for w in json.loads((nd/'words_narrator.json').read_text())['words']):return False
        if not any(w.get('voice') for w in words['words']):return False
        last=(nd/'footage_voice_levels.json').stat().st_mtime_ns
        if any((nd/n).stat().st_mtime_ns>last for n in ('narration.wav','words.json','footage_voices.json')):return False
        with wave.open(str(nd/'narration.wav')) as audio:
            if abs(audio.getnframes()/audio.getframerate()-words['duration'])>.02:return False
        stamp.write_text(json.dumps(current,indent=1))
        return True
    except (OSError,ValueError,KeyError,TypeError,wave.Error):return False


def run(job):
    here=Path(__file__).resolve().parent
    sys.path[:0]=[str(here.parent/'valmera-stories'),str(here.parent/'_shell'/'narrate')]
    import narrate
    import realvoice
    import voice
    narrate.malloc_returns_memory()
    nd=Path(job['narration_dir'])
    (nd/'witness-complete.json').unlink(missing_ok=True)
    placed=realvoice.splice(nd,job['voices'],job['footage_dir'])
    (nd/'footage_voices.json').write_text(json.dumps(placed,indent=1))
    output=io.StringIO()
    with contextlib.redirect_stdout(output):voice.level_voices(SimpleNamespace(dir=str(nd)))
    levels=json.loads(output.getvalue())
    (nd/'footage_voice_levels.json').write_text(json.dumps(levels,indent=1))
    # Test fixtures can omit the reader checkpoint; production runs cannot.
    if (nd/'read-stage.json').exists():
        (nd/'witness-complete.json').write_text(json.dumps(snapshot(job),indent=1))
    return dict(voices=len(placed),levels=levels)


if __name__=='__main__':
    print(json.dumps(run(json.loads(Path(sys.argv[1]).read_text()))))
