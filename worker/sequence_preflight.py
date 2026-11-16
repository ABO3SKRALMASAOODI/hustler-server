"""A reminder for an explicitly ordered request (cold open, then logo, then
the episode) whose turn has spent its last writes on cleanup.

Information on a write's result, never a refusal: the second-model order
check that could refuse a render before it started was removed (owner,
16 Nov 2026: the editing model looks at its own render and decides).
"""
import re


def cleanup_reminder(request, writes):
    if not re.search(r'\bcold[- ]open\b', str(request or ''), re.I):
        return ''
    cleanup = {'cut_range', 'cut_output_range', 'cut_silences', 'remove_filler_words'}
    if len(writes) >= 6 and len(writes) % 6 == 0 and set(writes[-6:]) <= cleanup:
        return ('\nREQUEST PRIORITY: the last six writes were cleanup. Check the '
                'requested cold-open/episode structure now and build its missing '
                'parts before further micro-polish. Use changed-section proof for '
                'cleanup checks; a new full-program render is not a planning step.')
    return ''
