"""Critical-path changes retain evidence, bytes, retries and ownership."""
import hashlib
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import boto3
import numpy as np
import pytest
from botocore.config import Config

import artifact_uploads
import io_telemetry
import storage
from score import source_audio


@pytest.mark.parametrize("failure", ["slowdown", "timeout", "denied"])
def test_multipart_retries_only_failed_part_and_keeps_exact_bytes(monkeypatch, tmp_path, failure):
    data = b'abcdefgh' * (3 * 1024 * 1024)
    source = tmp_path / 'film.mp4'
    source.write_bytes(data)
    attempts, parts = Counter(), {}
    active = [0, 0]
    aborted = []
    lock = threading.Lock()

    class Server(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body=b'', **headers):
            self.send_response(code)
            self.send_header('Content-Length', str(len(body)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if 'uploads' in query:
                self.reply(200, b'<InitiateMultipartUploadResult><Bucket>test</Bucket><Key>film</Key><UploadId>one</UploadId></InitiateMultipartUploadResult>')
            else:
                self.reply(200, b'<CompleteMultipartUploadResult><ETag>"done"</ETag></CompleteMultipartUploadResult>')

        def do_DELETE(self):
            aborted.append(self.path)
            self.reply(204)

        def do_PUT(self):
            part = int(parse_qs(urlsplit(self.path).query)['partNumber'][0])
            with lock:
                attempts[part] += 1
                attempt = attempts[part]
                active[0] += 1
                active[1] = max(active)
            body = self.rfile.read(int(self.headers['Content-Length']))
            time.sleep(.03)
            with lock:
                active[0] -= 1
            if part == 1 and failure == 'denied':
                self.reply(403, b'<Error><Code>AccessDenied</Code></Error>')
            elif part == 1 and attempt == 1 and failure == 'slowdown':
                self.reply(503, b'<Error><Code>SlowDown</Code></Error>')
            elif part == 1 and attempt == 1 and failure == 'timeout':
                time.sleep(.3)
                self.reply(200, ETag='"late"')
            else:
                parts[part] = body
                self.reply(200, ETag='"' + hashlib.md5(body).hexdigest() + '"')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    client = boto3.client('s3', endpoint_url=f'http://127.0.0.1:{server.server_port}',
                          aws_access_key_id='test', aws_secret_access_key='test',
                          region_name='us-east-1', config=Config(**dict(storage._connection_config(), read_timeout=.15)))
    monkeypatch.setattr(storage, 'store_client', lambda name: client)
    monkeypatch.setattr(storage, 'store_bucket', lambda name: 'test')
    monkeypatch.setattr(storage, '_plan', lambda: type('Plan', (), {'write': ('legacy',)})())
    try:
        if failure == 'denied':
            with pytest.raises(boto3.exceptions.S3UploadFailedError):
                storage.upload_file(str(source), 'film', 'video/mp4')
        else:
            storage.upload_file(str(source), 'film', 'video/mp4')
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    if failure == 'denied':
        assert len(aborted) == 1 and attempts[1] == 1
        return
    assert not aborted
    assert b''.join(parts[i] for i in sorted(parts)) == data
    assert attempts == {1: 2, 2: 1, 3: 1}
    assert active[1] >= 2
    assert storage._connection_config()['read_timeout'] <= 15


def test_artifact_failure_joins_other_upload_and_accounts_to_job(monkeypatch):
    finished = threading.Event()
    def upload(path, key, mime):
        if key == 'bad':
            raise RuntimeError('failed part')
        time.sleep(.02)
        io_telemetry.add_uploaded(11)
        finished.set()
    monkeypatch.setattr(storage, 'upload_file', upload)
    token = io_telemetry.begin()
    batch = artifact_uploads.ArtifactUploads()
    try:
        batch.submit('one', 'bad', 'video/mp4')
        batch.submit('two', 'good', 'image/jpeg')
        with pytest.raises(RuntimeError, match='failed part'):
            batch.wait()
        assert finished.is_set()
        assert batch.keys == ['good']
    finally:
        batch.close()
    assert io_telemetry.finish(token)['uploaded_bytes'] == 11


def test_audio_cache_reuses_samples_but_invalidates_replaced_source(monkeypatch, tmp_path):
    src = tmp_path / 'source.mp4'
    src.write_bytes(b'first')
    calls = []
    def decode(*a, **kw):
        calls.append(1)
        return type('Result', (), {'stdout': np.array([.1, -.2, .3], np.float32).tobytes()})()
    monkeypatch.setattr(source_audio.subprocess, 'run', decode)
    one = source_audio.decode(str(src), str(tmp_path))
    two = source_audio.decode(str(src), str(tmp_path))
    assert np.array_equal(one, two) and len(calls) == 1
    src.write_bytes(b'replacement')
    assert np.array_equal(one, source_audio.decode(str(src), str(tmp_path)))
    assert len(calls) == 2


def test_first_board_and_remaining_pages_have_the_same_sampling(monkeypatch, tmp_path):
    from score import brief
    seen = []
    monkeypatch.setattr(brief, 'frame_board', lambda src, per, idx, times, *args: list(times))
    monkeypatch.setattr(brief, 'detail_board', lambda *a: 'detail')
    monkeypatch.setattr(brief.boards, 'save', lambda board, path, **k: seen.append((path, board)))
    per = {'W': 720, 'H': 1280, 'dur': 20, 'shots': [{'t': [0, 20]}]}
    full = brief.boards_for(str(tmp_path), {}, per)
    original = list(seen)
    seen.clear()
    first = brief.boards_for(str(tmp_path), {}, per, pages=(1,))
    rest = brief.boards_for(str(tmp_path), {}, per, pages=(2, 3, 4))
    assert full == first == rest and seen == original


def test_proof_keeps_sound_in_last_frame_without_weakening_authored_edl():
    from schemas import validate_edl, EDLValidationError
    import stitch
    from timeline import Timeline
    original = validate_edl({'keep': [[0, 10]], 'captions': None,
                            'sfx': [{'id': 'hit', 'storage_key': 'sfx:whoosh',
                                     'at': 2.3}]}, 10).model_dump()
    fragment = stitch.window_edl(original, Timeline(original['keep']),
                                 1, 2.34, keep_audio=True, fps=30)
    checked = validate_edl(fragment, 10, render_fragment=True)
    assert checked.sfx[0].at == 1.3
    with pytest.raises(EDLValidationError, match='past the end'):
        validate_edl(fragment, 10)
    fragment['sfx'][0]['at'] = 1.34
    with pytest.raises(EDLValidationError, match='past the end'):
        validate_edl(fragment, 10, render_fragment=True)


@pytest.mark.parametrize('factor', [1, 2])
def test_proof_retains_boundary_frame_and_its_graphic_clock(factor):
    from schemas import validate_edl, program_duration, EDLValidationError
    import stitch
    from timeline import Timeline
    original = {'keep': [[0, 1.2], [1.2, 3]], 'captions': None,
                'speed': ([{'id': 'fast', 'start': 0, 'end': 1.2,
                            'factor': factor}] if factor != 1 else []),
                'fx': [{'id': 'look', 'type': 'grade_window', 'stage': 'screen',
                        'start': 0, 'end': 3 if factor == 1 else 2.4,
                        'contrast': 1.12}]}
    original = validate_edl(original, 3).model_dump()
    start = 1.2 / factor - .03 / factor
    stop = start + 1.33
    fragment = stitch.window_edl(original, Timeline(original['keep'], [],
                                original['speed']), start, stop,
                                fps=30, render_fragment=True)
    checked = validate_edl(fragment, 3, render_fragment=True).model_dump()
    assert checked['keep'][0] == [1.17, 1.2]
    assert program_duration(checked) == pytest.approx(1.33, abs=.01)
    assert checked['fx'][0]['end'] <= program_duration(checked) + .01
    with pytest.raises(EDLValidationError, match='shorter'):
        validate_edl(fragment, 3)
