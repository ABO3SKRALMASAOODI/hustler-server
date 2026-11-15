import json
from types import SimpleNamespace
import pytest
from footage import fetch


def test_external_fetch_keeps_identity_and_run_directory(tmp_path,monkeypatch):
    movie=tmp_path/'whole.mp4';movie.write_bytes(b'x'*3000)
    monkeypatch.setenv('FOOTAGE_FETCH_CMD','operator-fetch --original')
    def external(command,**kw):
        assert command==['operator-fetch','--original']
        assert json.loads(kw['input'])==dict(url='https://example.org/original.mp4',out_dir=str(tmp_path))
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(path=str(movie),url='https://example.org/original.mp4',rung='box-primary')))
    monkeypatch.setattr(fetch.subprocess,'run',external)
    assert fetch.whole_av('https://example.org/original.mp4',str(tmp_path))==(str(movie),'external-original:box-primary')


@pytest.mark.parametrize('mismatch',['url','path'])
def test_external_fetch_cannot_substitute_another_source_or_file(tmp_path,monkeypatch,mismatch):
    movie=tmp_path/'whole.mp4';movie.write_bytes(b'x'*3000)
    receipt=dict(path=str(movie),url='https://example.org/original.mp4')
    receipt[mismatch]='https://example.org/unrelated.mp4' if mismatch=='url' else str(tmp_path.parent/'unrelated.mp4')
    monkeypatch.setenv('FOOTAGE_FETCH_CMD','operator-fetch')
    monkeypatch.setattr(fetch.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(receipt)))
    with pytest.raises(fetch.FetchError,match='external source transport failed'):
        fetch.whole_av('https://example.org/original.mp4',str(tmp_path))



def test_original_survives_later_acquisition_failure(tmp_path,monkeypatch):
    downloaded=tmp_path/'fetch';downloaded.mkdir();movie=downloaded/'whole.mp4';movie.write_bytes(b'x'*3000)
    kept=tmp_path/'retained';monkeypatch.setenv('FOOTAGE_KEEP_SOURCES',str(kept))
    monkeypatch.setenv('FOOTAGE_FETCH_CMD','operator-fetch')
    monkeypatch.setattr(fetch.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(dict(path=str(movie),url='https://example.org/original.mp4',rung='box'))))
    fetch.whole_av('https://example.org/original.mp4',str(downloaded))
    movie.unlink()  # the later stage failed and its temporary source folder was cleaned
    record=json.load(open(kept/'manifest.json'))[0]
    from pathlib import Path
    assert record['url']=='https://example.org/original.mp4'
    assert Path(record['path']).read_bytes()==b'x'*3000


def test_structured_transport_failure_preserves_source_access_reason(tmp_path,monkeypatch):
    monkeypatch.setenv('FOOTAGE_FETCH_CMD','operator-fetch')
    url='https://example.org/original.mp4'
    receipt=dict(ok=False,url=url,error='source unavailable on authorized egress')
    monkeypatch.setattr(fetch.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=json.dumps(receipt)))
    with pytest.raises(fetch.FetchError,match='external original fetch failed: source unavailable on authorized egress'):
        fetch.whole_av(url,str(tmp_path))
