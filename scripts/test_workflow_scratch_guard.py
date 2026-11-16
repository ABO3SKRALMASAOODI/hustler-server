import os
from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).parent))
from workflow_scratch_guard import disposition


def test_old_parent_with_current_nested_run_is_kept(tmp_path):
    run = tmp_path / 'lane' / 'run' / 'shorts'
    run.mkdir(parents=True)
    (run / 'pack.json').write_text('{}')
    os.utime(tmp_path / 'lane', (1, 1))
    assert disposition(tmp_path / 'lane', []) == 'recent-nested-write'


def test_live_process_via_lane_symlink_is_kept_even_when_all_files_are_old(tmp_path):
    work = tmp_path / 'physical'
    work.mkdir()
    (work / 'run').mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(work, target_is_directory=True)
    for path in [work, work / 'run']:
        os.utime(path, (1, 1))
    assert disposition(work, [f'python workflow.py --state-root {alias}/run']) == 'live-process'
    assert disposition(work, [f'python workflow.py --work={work}/run']) == 'live-process'


def test_only_inactive_unchanged_tree_is_stale(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'old.json').write_text('{}')
    for path in [work, work / 'old.json']:
        os.utime(path, (1, 1))
    assert disposition(work, ['python unrelated.py']) == 'stale'
    assert disposition(tmp_path / 'missing', []) == 'not-a-directory'


def test_shared_media_links_do_not_keep_inactive_scratch_forever(tmp_path):
    shared = tmp_path / 'shared.mp4'
    shared.write_bytes(b'media')
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'source').symlink_to(shared)
    os.utime(work / 'source', (1, 1), follow_symlinks=False)
    os.utime(work, (1, 1))
    assert disposition(work, []) == 'stale'


def test_cli_detects_live_alias_process_and_does_not_count_itself(tmp_path):
    work = tmp_path / 'physical'
    work.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(work, target_is_directory=True)
    os.utime(work, (1, 1))
    worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', str(alias / 'run')])
    guard = str(Path(__file__).with_name('workflow_scratch_guard.py'))
    try:
        checked = subprocess.run([sys.executable, guard, str(work)], capture_output=True, text=True)
        assert checked.returncode == 1 and 'live-process' in checked.stdout
    finally:
        worker.terminate()
        worker.wait(timeout=5)
    checked = subprocess.run([sys.executable, guard, str(work)], capture_output=True, text=True)
    assert checked.returncode == 0 and 'stale' in checked.stdout
