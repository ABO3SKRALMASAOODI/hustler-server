"""Splice and level original voices inside the installed narrator environment."""
import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def run(job):
    here=Path(__file__).resolve().parent
    sys.path[:0]=[str(here.parent/'valmera-stories'),str(here.parent/'_shell'/'narrate')]
    import narrate
    import realvoice
    import voice
    narrate.malloc_returns_memory()
    nd=Path(job['narration_dir'])
    placed=realvoice.splice(nd,job['voices'],job['footage_dir'])
    (nd/'footage_voices.json').write_text(json.dumps(placed,indent=1))
    output=io.StringIO()
    with contextlib.redirect_stdout(output):voice.level_voices(SimpleNamespace(dir=str(nd)))
    levels=json.loads(output.getvalue())
    (nd/'footage_voice_levels.json').write_text(json.dumps(levels,indent=1))
    return dict(voices=len(placed),levels=levels)


if __name__=='__main__':
    print(json.dumps(run(json.loads(Path(sys.argv[1]).read_text()))))
