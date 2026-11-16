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


def test_codec_headroom_can_settle_twice_before_loudness_converges(monkeypatch, tmp_path):
    path = tmp_path / 'film.mp4'
    path.write_bytes(b'film')
    # Actual unchanged p5574 approval EDL and FFmpeg 8 encoder. The two
    # initial peak corrections leave the third pass at -15.01 LUFS.
    levels = iter([{'i': -15.88, 'tp': -1.26},
                   {'i': -15.91, 'tp': -1.08},
                   {'i': -15.55, 'tp': -2.41},
                   {'i': -15.01, 'tp': -2.49},
                   {'i': -14.31, 'tp': -2.44}])
    monkeypatch.setattr(mastering, 'measure', lambda _: next(levels))
    peaks = iter([{'limit_dbfs': -6.52}, {'limit_dbfs': -7.18}, {}, {}])
    monkeypatch.setattr(mastering.true_peak, 'guard', lambda *a, **k: next(peaks))
    monkeypatch.setattr(mastering.subprocess, 'run',
                        lambda cmd, **kw: Path(cmd[-1]).write_bytes(b'corrected'))
    report = mastering.guard(str(path), str(tmp_path), bitrate='128k')
    assert report['passes'] == 4 and report['held']
    assert abs(report['i'] - mastering.TARGET) <= mastering.TOLERANCE
    assert report['tp'] <= -1
    assert not list(tmp_path.glob('master_guard_*'))


def test_nonconverging_mix_is_still_bounded_and_rejected(monkeypatch, tmp_path):
    import pytest
    path = tmp_path / 'film.mp4'
    path.write_bytes(b'film')
    monkeypatch.setattr(mastering, 'measure', lambda _: {'i': -20., 'tp': -2.})
    monkeypatch.setattr(mastering.true_peak, 'guard', lambda *a, **k: {})
    encodes = []
    def encode(cmd, **kw):
        encodes.append(cmd)
        Path(cmd[-1]).write_bytes(b'corrected')
    monkeypatch.setattr(mastering.subprocess, 'run', encode)
    with pytest.raises(ValueError, match='social master did not hold'):
        mastering.guard(str(path), str(tmp_path))
    assert len(encodes) == mastering.MAX_PASSES == 5
    assert not list(tmp_path.glob('master_guard_*'))
