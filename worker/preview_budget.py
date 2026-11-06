"""Bound trial exploration without treating an unfinished render as delivery.

Only distinct complete previews consume the exploration allowance. Diagnostic
proofs, failed calls and re-reading the same render do not. Proven defects may
use bounded repair passes; each subsequent pass must reduce the outstanding
defect count. The ordinary money, deadline and no-progress guards still apply.
All state is JSON-safe and belongs to the logical request, across continuations.
"""

MAX_REPAIR_PASSES = 3


def restore(ctx, checkpoint):
    # Include a complete render adopted at resume and the prepared first cut.
    # Neither necessarily returned through the model's tool-dispatch counter.
    versions = (list(checkpoint.get('trial_preview_versions') or [])
                + list(checkpoint.get('rendered_versions') or [])
                + list(getattr(ctx, 'rendered_versions', None) or []))
    ctx.trial_preview_versions = sorted({int(v) for v in versions})
    # Older checkpoints counted proof calls. Receipts, not their call counter,
    # are authoritative for migration and for newly adopted completions.
    ctx.trial_previews = len(ctx.trial_preview_versions)
    ctx.trial_preview_repairs = dict(checkpoint.get('trial_preview_repairs') or {})
    _finish_repair(ctx)


def checkpoint(ctx):
    return {
        'trial_preview_versions': list(getattr(ctx, 'trial_preview_versions', [])),
        'trial_preview_repairs': dict(getattr(ctx, 'trial_preview_repairs', {})),
    }


def _finish_repair(ctx):
    preview = getattr(ctx, 'last_preview', None) or {}
    version = preview.get('edl_version')
    repairs = getattr(ctx, 'trial_preview_repairs', None) or {}
    active = repairs.get('active_from')
    if version is not None and active is not None and int(version) != active:
        repairs.pop('active_from', None)
        ctx.trial_preview_repairs = repairs


def count(ctx):
    preview = getattr(ctx, 'last_preview', None) or {}
    version = preview.get('edl_version')
    if not getattr(ctx, 'trial_preview_cap', None) or version is None:
        return
    _finish_repair(ctx)
    if preview.get('cached'):
        return
    # last_preview is set only when the complete preview job finishes. A
    # changed-section proof has a separate artifact and never sets this field.
    versions = getattr(ctx, 'trial_preview_versions', None) or []
    if int(version) in versions:
        return
    ctx.trial_preview_versions = versions + [int(version)]
    ctx.trial_previews = int(getattr(ctx, 'trial_previews', 0) or 0) + 1


def repair_allowed(ctx):
    """Reserve one edit-and-verification cycle against actual defect evidence."""
    state = dict(getattr(ctx, 'trial_preview_repairs', None) or {})
    if state.get('active_from') is not None:
        return True
    try:
        version = int(ctx.latest_edl()['version'])
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    preview = getattr(ctx, 'last_preview', None) or {}
    if preview.get('edl_version') != version:
        return False
    record = (getattr(ctx, 'verification_records', None) or {}).get(version) or {}
    findings = record.get('unresolved_findings') or []
    blocking = [f for f in findings if isinstance(f, dict)
                and f.get('severity') == 'error']
    history = state.get('history') or []
    if not blocking or len(history) >= MAX_REPAIR_PASSES:
        return False
    # Writing another EDL version, or a reviewer renaming a finding, is not
    # progress. A repeated or growing defect set cannot renew this allowance.
    if history and len(blocking) >= history[-1]['remaining']:
        return False
    state.update(active_from=version, history=history + [{
        'version': version, 'remaining': len(blocking),
        'findings': [f.get('finding_id') or f.get('code') for f in blocking],
    }])
    ctx.trial_preview_repairs = state
    return True
