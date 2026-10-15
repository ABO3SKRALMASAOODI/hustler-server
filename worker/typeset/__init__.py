"""Typesetter v2: the layout solver (ROADMAP Wave 2, track 2A).

Smart solver, dumb renderer: Python solves absolute boxes on the page's own
font files (shaping), in a free-space map of the frame (freespace), with
layout families (layouts), word roles (roles), type voices (voices) and a
search that maximises the payload under every constraint (solver). The
result is a LayoutDoc (layoutdoc) the ``typeset`` template only draws.

Contract: worker/devices/INTERFACES_W2.md §3.
"""

from . import voices  # noqa: F401  (re-exported names below)

VOICES = voices.NAMES


def families():
    from . import layouts
    return layouts.FAMILIES
