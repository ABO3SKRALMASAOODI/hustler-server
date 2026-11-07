"""Bound trial exploration without treating an unfinished render as delivery.

Only distinct complete previews consume the exploration allowance. Diagnostic
proofs, failed calls and re-reading the same render do not. Proven defects may
use bounded repair passes; each subsequent pass must reduce the outstanding
defect count. The ordinary money, deadline and no-progress guards still apply.
All state is JSON-safe and belongs to the logical request, across continuations.
"""

MAX_REPAIR_PASSES = 3

# Writes that only take away something an edit added. A stop may end new work,
# never the turn's way back: on p5518 the cycle stopped with four grey blur
# boxes on the customer's video and refused remove_blur five times, so the
# damage stayed in the saved version. This classifies tools, never a request.
# reset_edit is left out (it also drops the customer's earlier work), and
# remove_filler_words is a cut.
UNDO_TOOLS = frozenset((
    "remove_aspect_shift", "remove_blur", "remove_broll",
    "remove_cursor_enhance", "remove_depth_time",
    "remove_editorial_graphic", "remove_erase", "remove_finish",
    "remove_insert", "remove_motion_graphic", "remove_music",
    "remove_overlay", "remove_picture_card", "remove_punctuation",
    "remove_region_effect", "remove_screen_frame", "remove_screen_takeover",
    "remove_sfx", "remove_speed", "remove_stem_mix", "remove_stylize",
    "remove_subject_effect", "remove_text", "remove_typeset",
    "remove_typography_scene", "remove_ui_scene", "remove_vector_graphic",
    "remove_voiceover", "remove_world", "remove_zoom", "remove_zoom_path",
    "restore_range",
))
MAX_UNDO_RENDERS = 2


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


def _finish_undo(ctx, version):
    """An undo pass ends when a preview of the undone version exists; the
    render is counted here so a failed one can be retried."""
    repairs = getattr(ctx, 'trial_preview_repairs', None) or {}
    if repairs.get('undo_from') is None or int(version) == repairs['undo_from']:
        return
    repairs.pop('undo_from', None)
    repairs['undo_renders'] = int(repairs.get('undo_renders') or 0) + 1
    ctx.trial_preview_repairs = repairs


def _only_removes(name, args):
    """A batch of nothing but removals is the same way back as a remove_* tool
    (edit_batch removes items by id, never a cut of the footage)."""
    ops = (args or {}).get('operations') if name == 'apply_edit_batch' else None
    return bool(ops) and all(isinstance(op, dict) and op.get('action') == 'remove'
                             for op in ops)


def undo_allowed(ctx, name, args=None):
    """Past every allowance, the turn may still take back what it added and
    show the result. Bounded: undo only removes, new work stays refused, and
    there are at most MAX_UNDO_RENDERS such passes."""
    state = dict(getattr(ctx, 'trial_preview_repairs', None) or {})
    if name in UNDO_TOOLS or _only_removes(name, args):
        if int(state.get('undo_renders') or 0) >= MAX_UNDO_RENDERS:
            return False
        if state.get('undo_from') is None:
            version = (getattr(ctx, 'last_preview', None) or {}).get('edl_version')
            if version is None:
                return False
            state['undo_from'] = int(version)
            ctx.trial_preview_repairs = state
        return True
    if name != 'render_preview' or state.get('undo_from') is None:
        return False
    try:
        return int(ctx.latest_edl()['version']) != state['undo_from']
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def count(ctx):
    preview = getattr(ctx, 'last_preview', None) or {}
    version = preview.get('edl_version')
    if not getattr(ctx, 'trial_preview_cap', None) or version is None:
        return
    _finish_repair(ctx)
    _finish_undo(ctx, version)
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
    import request_contract
    assessment = request_contract.current_assessment(ctx) if request_contract.enabled(ctx) else None
    if assessment:
        blocking += [dict(finding_id='request:' + row['id'], severity='error')
                     for row in assessment['requirements']
                     if row['status'] in {'unmet', 'unknown'}]
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
