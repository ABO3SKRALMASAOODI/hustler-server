"""Exercise the normal timing promotion and the native alternative selection."""
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import wf3
import voice  # sets the native narration module path
import voice_joins
from test_timing import _ctx, _sc, _reader


def test_directed_same_words_reach_join_repair_before_donor_cleanup(tmp_path, monkeypatch):
    reads = []
    reader = _reader(reads)

    def narrate(sc, out):
        result = reader(sc, out)
        (Path(out) / 'check.json').write_text(json.dumps({'joins': {'with_artefact': 1}}))
        return result

    def rewrite(sc, measure, notes):
        changed = deepcopy(sc)
        changed['segments'][-1]['direction'] = 'Quieter on the last word.'
        return changed, [], [2]

    seen = []
    def check(argv, **kwargs):
        src, out, donor = map(Path, argv[-3:])
        assert donor.is_dir() and src.is_dir()
        assert kwargs['env']['VALMERA_RUN'] == 't'
        seen.append((src.name, donor.name))
        (out/'words.json').write_bytes((src/'words.json').read_bytes())
        (out/'check.json').write_text(json.dumps({'joins': {'with_artefact': 0}}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, 'run', check)
    monkeypatch.setattr(wf3.PX, 'NARRATE_LOCK', str(tmp_path/'lock'))
    ctx = _ctx(tmp_path)
    wf3.timed(ctx, _sc(), {}, [], narrate=narrate, rewrite=rewrite, final=lambda sc, rep: (sc, rep))
    assert len(reads) == 2 and reads[0] == reads[1]
    assert seen == [('narration_t1', 'narration_t0')]
    assert json.loads((Path(ctx.supply)/'narration'/'check.json').read_text())['joins']['with_artefact'] == 0


def test_native_alternate_compares_both_neighbor_registers(monkeypatch):
    monkeypatch.setattr(voice_joins.measure, '_f0_med', lambda x, sr: np.median(x))
    def take(left, right):
        return dict(_sr=100, _x=np.r_[np.full(150, left), np.full(150, right)])
    expected = [120, 160]
    assert voice_joins.native_edge_cost(take(120, 160), expected) == 0
    assert voice_joins.native_edge_cost(take(100, 160), expected) > 0
    assert voice_joins.native_edge_cost(take(120, 140), expected) > 0
