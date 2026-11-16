"""The repair pass: what validate_edl would refuse, made valid and said.

Reuses edit_batch's own near-miss fixers (they already make an edit batch
land): keeps sorted, merged and held inside the source, inserts moved onto a
cut, items held to the programme's end, a styling field the schema refuses
left out. Each repair is one line for the envelope. Nothing here reads the
request: these are shape and clock repairs only.
"""
import copy

import edit_batch
from schemas import EDLValidationError, validate_edl

ALL_LAYERS = frozenset(edit_batch.LAYERS) | {"keep", "inserts", "overlays",
                                             "texts", "captions"}


def _validate(edl, duration):
    return validate_edl(edl, duration).model_dump()


def repair(edl, duration, before=None):
    """(normalized EDL or None, repair lines, error text or '').

    Tries the EDL as it is, then once more after every shape repair.
    ``before`` is the EDL the change started from (for the programme-end
    hold). Never raises."""
    try:
        return _validate(edl, duration), [], ""
    except (EDLValidationError, ValueError, TypeError) as exc:
        first_error = str(exc)
    work = copy.deepcopy(edl)
    notes = []
    try:
        if isinstance(work.get("keep"), list):
            notes += edit_batch._tidy_keep(work)
            if duration:
                notes += edit_batch._keep_inside_source(work, float(duration))
        notes += edit_batch._item_near_misses(work, ALL_LAYERS)
        notes += edit_batch._on_keep_boundaries(work)
        notes += edit_batch.end_with_programme(before or work, work, [])
    except Exception as exc:  # noqa: BLE001 - a repair never raises
        notes.append(f"repair pass stopped early ({str(exc)[:120]})")
    try:
        return _validate(work, duration), notes, ""
    except (EDLValidationError, ValueError, TypeError) as exc:
        message = str(exc)
    dropped = edit_batch._drop_refused_values(work, message)
    if dropped:
        notes += dropped
        try:
            return _validate(work, duration), notes, ""
        except (EDLValidationError, ValueError, TypeError) as exc:
            message = str(exc)
    return None, notes, message or first_error
