"""Loudness correction retains headroom established on the actual codec."""
from pathlib import Path

import mastering


def test_loudness_retry_does_not_recreate_the_codec_peak(monkeypatch, tmp_path):
    path = tmp_path / "film.mp4"
    path.write_bytes(b"film")
    levels = iter([{'i': -15.36, 'tp': -1.91},
                   {'i': -16.85, 'tp': -1.61},
                   {'i': -14.20, 'tp': -1.52}])
    monkeypatch.setattr(mastering, 'measure', lambda _: next(levels))
    peaks = iter([{'limit_dbfs': -7.11, 'held': True},
                  {'limit_dbfs': None, 'held': True}])
    monkeypatch.setattr(mastering.true_peak, 'guard', lambda *a, **k: next(peaks))
    limits = []
    real_chain = mastering.true_peak.limiter_chain
    def chain(db):
        limits.append(db)
        return real_chain(db)
    monkeypatch.setattr(mastering.true_peak, 'limiter_chain', chain)
    commands = []
    def encode(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"corrected")
    monkeypatch.setattr(mastering.subprocess, 'run', encode)
    report = mastering.guard(str(path), str(tmp_path), bitrate='128k')
    assert report['held'] and report['passes'] == 2
    assert limits == [-2., -7.11]
    assert report['i'] == -14.20 and report['tp'] == -1.52
    assert all(command[command.index('-c:v') + 1] == 'copy' for command in commands)
    assert path.read_bytes() == b'corrected'
    assert not list(tmp_path.glob('master_guard_*'))


def test_limited_mix_converges_within_the_existing_pass_budget(monkeypatch, tmp_path):
    path = tmp_path / 'film.mp4'
    path.write_bytes(b'film')
    # p5574's measured first two passes: the codec guard establishes its
    # ceiling, then only .91 of a requested 2.85 LU reaches the mix.
    levels = iter([{'i': -15.36, 'tp': -1.91},
                   {'i': -16.85, 'tp': -1.61},
                   {'i': -15.94, 'tp': -1.76},
                   {'i': -14.53, 'tp': -1.79}])
    monkeypatch.setattr(mastering, 'measure', lambda _: next(levels))
    peaks = iter([{'limit_dbfs': -7.11}, {}, {}])
    monkeypatch.setattr(mastering.true_peak, 'guard', lambda *a, **k: next(peaks))
    gains = []
    def encode(command, **kwargs):
        chain = command[command.index('-af') + 1]
        gains.append(float(chain.split('volume=')[1].split('dB')[0]))
        Path(command[-1]).write_bytes(b'corrected')
    monkeypatch.setattr(mastering.subprocess, 'run', encode)
    report = mastering.guard(str(path), str(tmp_path))
    assert report['held'] and report['passes'] == 3
    assert abs(report['i'] - mastering.TARGET) <= mastering.TOLERANCE
    assert report['tp'] <= -1
    assert 5 < gains[-1] <= 12
