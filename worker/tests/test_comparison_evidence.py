"""Broad comparisons reuse real persisted pixels and their actual clocks."""
import os
import sys
import shutil

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import comparison_evidence
import visual_index


def storyboard(tmp_path, monkeypatch, count=8, per_sheet=6):
    monkeypatch.setattr(visual_index.config, 'VISUAL_SHEET_FRAMES', per_sheet)
    rows = []
    for i in range(count):
        path = tmp_path / f'original-{i}.jpg'
        Image.new('RGB', (400, 225), (20 * i, 40, 70)).save(path)
        rows.append({'evidence_id': f've_{i}', 'frame_hash': str(i),
                     'representative_t': i + 0.25, 'source_clock': str(i + .25),
                     'covered_time_range': [i, i + 1], '_path': str(path)})
    sheets = visual_index._build_sheets(rows, str(tmp_path / 'sheets'))
    downloads = []
    def fetch(key, target):
        downloads.append(key)
        shutil.copyfile(key, target)
    monkeypatch.setattr(comparison_evidence.storage, 'download_to', fetch)
    index = {'visual_storyboard': {'version': 1, 'evidence': rows,
             'sheets': [dict(sheet, key=sheet['path']) for sheet in sheets]}}
    workdir = tmp_path / 'work'
    workdir.mkdir()
    return index, workdir, downloads


@pytest.mark.parametrize('per_sheet', [4, 6])
def test_reuses_actual_pixels_across_pages_with_real_clocks(tmp_path, monkeypatch, per_sheet):
    idx, workdir, downloads = storyboard(tmp_path, monkeypatch, per_sheet=per_sheet)
    result = comparison_evidence.indexed_frames(idx, [1, 3, 5, 7], str(workdir))
    assert [(r[0], r[2]) for r in result] == [(i + .25, f've_{i}') for i in (1, 3, 5, 7)]
    for i, (_, path, _) in zip((1, 3, 5, 7), result):
        with Image.open(path) as frame:
            assert frame.size == (400, 225)
            assert abs(frame.getpixel((200, 100))[0] - 20 * i) < 4
    assert len(downloads) == 2
    assert comparison_evidence.indexed_frames(idx, [1, 3, 5, 7], str(workdir)) == result
    assert len(downloads) == 2


def test_missing_corrupt_or_unknown_evidence_uses_source_fallback(tmp_path, monkeypatch):
    idx, workdir, downloads = storyboard(tmp_path, monkeypatch)
    idx['visual_storyboard']['version'] = 999
    assert comparison_evidence.indexed_frames(idx, [1], str(workdir)) is None
    idx['visual_storyboard']['version'] = 1
    def broken(key, path):
        open(path, 'wb').write(b'not a jpeg')
    monkeypatch.setattr(comparison_evidence.storage, 'download_to', broken)
    assert comparison_evidence.indexed_frames(idx, [1], str(workdir)) is None


def test_duplicate_clusters_do_not_force_another_source_decode(tmp_path, monkeypatch):
    idx, workdir, _ = storyboard(tmp_path, monkeypatch, count=2)
    result = comparison_evidence.indexed_frames(idx, [0, 1, 2, 3], str(workdir))
    assert [(r[0], r[2]) for r in result] == [(.25, 've_0'), (1.25, 've_1')]


def test_unknown_sheet_layout_never_claims_to_have_seen_frames(tmp_path, monkeypatch):
    idx, workdir, _ = storyboard(tmp_path, monkeypatch)
    def wrong(key, path):
        Image.new('RGB', (100, 100)).save(path)
    monkeypatch.setattr(comparison_evidence.storage, 'download_to', wrong)
    assert comparison_evidence.indexed_frames(idx, [1], str(workdir)) is None
