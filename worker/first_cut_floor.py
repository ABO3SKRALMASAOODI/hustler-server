"""Never replace a before-style first cut with a demonstrably thinner film.

Both contenders are built by the product on this job, then measured from
rendered pixels and audio against the upload by the fixed edit_delta engine.
No subjective rating is assigned, and unavailable evidence never wins.
"""
import copy
import json
import os
import signal
import subprocess
import sys

CLASSES = {'same_upload': 0, 'trim_only': 1, 'light_touch': 2, 'edited': 3}


def prefer_floor(candidate, floor):
    """A department floor: an extra crop or silent SFX item earns nothing."""
    if candidate.get('class') not in CLASSES or floor.get('class') not in CLASSES:
        return False
    if floor.get('taste', {}).get('off_edit_steps', 0) > candidate.get('taste', {}).get('off_edit_steps', 0):
        return False
    def vector(m):
        dur = max(float(m.get('program_s') or 1), 1)
        sounds = len((m.get('taste') or {}).get('sound_times') or [])
        return (CLASSES[m['class']], float(m.get('shots_added') or 0) * 10 / dur,
                sounds * 10 / dur, float(m.get('camera_moves_added') or 0) * 10 / dur)
    c, f = vector(candidate), vector(floor)
    # The before-style cut is the floor, not a weighted score to beat by
    # trading away a department. Extra motion cannot buy inaudible sound,
    # and a classifier's "edited" label alone cannot waive missing pictures.
    if f[1] + .05 < c[1]:
        return False
    return any(a > b + .05 for a, b in zip(f, c))


def needs_floor(measured):
    return measured.get('class') in CLASSES and (
        measured.get('class') != 'edited' or not measured.get('shots_added'))


def measure(ctx, worker_db, label):
    import db
    import storage
    import schemas
    preview = getattr(ctx, 'last_preview', None) or {}
    row = ctx.latest_edl() or {}
    if preview.get('edl_version') != row.get('version'):
        raise ValueError('comparison requires the current complete preview')
    asset = worker_db.run(db.get_asset, preview.get('render_asset_id')) or {}
    if not asset.get('storage_key'):
        raise ValueError('comparison preview has no file')
    path = os.path.join(ctx.workdir, 'floor_' + label + '.mp4')
    report = os.path.join(ctx.workdir, 'floor_' + label + '.json')
    source = ctx.proxy_path()
    storage.download_to(asset['storage_key'], path)
    prog = float(schemas.program_duration(row['json']))
    cmd = [sys.executable, '-m', 'film_measure', path, source, str(prog), report]
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        process.wait(timeout=45)
        if process.returncode:
            raise RuntimeError('rendered comparison failed')
        with open(report) as handle:
            return json.load(handle)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for file in (path, report):
            if os.path.exists(file):
                os.remove(file)


def choose(ctx, worker_db, started, start_edl, steps, applied, picked, render, build):
    """Compare a missing-footage Director film with today's chain before delivery.

    The chain is itself the before-style floor on jobs that miss the Director.
    It must not be stripped by a later missing-footage fallback. Rich films
    keep their checked pictures when the measured candidate is already edited.
    """
    if not picked or picked.get('how') != 'director' or not start_edl:
        return steps, applied, picked, render
    row = ctx.latest_edl() or {}
    edl = row.get('json') or {}
    rec = {}
    old = copy.deepcopy(edl)
    saved = (steps, applied, picked, render)
    changed = False
    more, floor_applied, floor_picked, floor_render = [], applied, picked, render
    try:
        candidate = measure(ctx, worker_db, 'candidate')
        rec['candidate'] = candidate
        if not needs_floor(candidate):
            rec['decision'] = 'candidate'
            return saved
        # Allow the chain its ordinary generation budget. Never silently
        # skip it because the Director consumed the old chain's time limit.
        result = ctx.apply_score(start_edl['json'], 'build the before-style first-cut comparison')
        if not str(result).startswith('EDL v'):
            raise RuntimeError('cannot build baseline from upload')
        changed = True
        ctx.director_version = None
        more, floor_applied, floor_picked, floor_render = build()
        baseline = measure(ctx, worker_db, 'before_style')
        rec['before_style'] = baseline
        if prefer_floor(candidate, baseline):
            rec['decision'] = 'before_style'
            ctx.director_version = None
            # No delayed footage upgrade may overwrite the winning floor.
            for name in ('director_upgrade', 'director_state'):
                state = getattr(ctx, name, None)
                if state and state.get('work'):
                    import shutil
                    shutil.rmtree(state['work'], ignore_errors=True)
                setattr(ctx, name, None)
            return steps + more, floor_applied, floor_picked, floor_render
        rec['decision'] = 'candidate'
    except Exception as exc:
        rec['unavailable'] = f'{type(exc).__name__}: {str(exc)[:120]}'
    finally:
        phases = dict(getattr(ctx, 'first_cut_phases', None) or {})
        phases['never_below'] = rec
        ctx.first_cut_phases = phases
    if changed:
        result = ctx.apply_score(old, 'keep the better measured first film')
        if str(result).startswith('EDL v'):
            ctx.director_version = (ctx.latest_edl() or {}).get('version')
            import first_cut
            render = first_cut._render_cut(ctx, steps, picked)
        else:
            rec['restore_error'] = 'candidate write refused; before-style preview retained'
            return steps + more, floor_applied, floor_picked, floor_render
    return steps, applied, picked, render
