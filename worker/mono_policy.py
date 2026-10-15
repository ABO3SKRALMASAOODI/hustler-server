"""Black and white is a DEVICE, never a flat look (owner taste, 15 Oct 2026).

In the references, B&W is always a moment with a reason:

  * a B&W section that opens to colour ON the drop / the turn (18@0-3.64:
    the intro is grey until the beat lands, colour arrives on that frame);
  * B&W with one colour kept (40@0-23: the red jacket in a grey world);
  * a B&W silhouette hold (37, 23, 38: the figure as a flat shape for one
    thought);
  * negatives on hits (04, 20: one inverted frame on the transient).

What the owner disliked: agents turned those moments into a constant B&W
grade over the whole short (the 'mono_noir' look did exactly that: grade
'bw' from frame 0 to the end card). This module is the rule in code:

  ``whole_mono(edl)``  why the program reads as flat B&W (the 'bw' grade, or
                       desaturating grade windows over most of it), or None;
  ``asked(text)``      True when the user's own words ask for black and white
                       (None when there are no user words: an MCP client or a
                       script, where the reply can only advise);
  ``check_write``      the write gate: a write that makes the program flat
                       B&W is refused when the user's turn did not ask for it,
                       with the devices that do the job named; with no user
                       words it passes with an advisory note.

Pure: imports nothing from the worker, so the backend may load it by path.
"""

import re

# A grade window this desaturated reads as black and white.
MONO_SATURATION = 0.15
# ...and over this share of the program it is the whole short's look.
WHOLE_SHARE = 0.6

_ASK = re.compile(
    r"\b(black[\s-]*(?:and|&|n'?)[\s-]*white|b\s*&\s*w|b/w|bnw|"
    r"monochrome|mono(?:chromatic)?|gr[ae]y\s*-?scale|desaturat\w*|"
    r"colou?rless|no colou?r|noir)\b", re.I)
_REFUSAL = re.compile(
    r"\b(no|not|never|don'?t|do not|without|stop|remove|less|avoid|undo|"
    r"bring back|back to colou?r)\b", re.I)

DEVICES_LINE = (
    "B&W lands as a device on its moment: a mono section that opens to "
    "colour on the turn (set_grade_window saturation 0 with ramp_out_f 3 "
    "ending on the word), the speaker kept in colour in a grey world "
    "(isolate_colour keep='subject'), a silhouette hold "
    "(add_subject_effect kind='poster'), or 1-frame negatives on the "
    "strongest hits (add_accent kind='negative'); suggest_devices proposes "
    "them bound to the right words")


def asked(text):
    """True when ``text`` (the user's own words this turn) asks for black and
    white; False when it does not; None when there are no user words."""
    t = str(text or "").strip()
    if not t:
        return None
    for m in _ASK.finditer(t):
        clause = re.split(r"[.!?;,]|\b(?:but|and then|then)\b",
                          t[:m.start()], flags=re.I)[-1]
        if not _REFUSAL.search(clause):
            return True
    return False


def _program_s(edl):
    total = 0.0
    for span in (edl or {}).get("keep") or []:
        try:
            total += max(0.0, float(span[1]) - float(span[0]))
        except (TypeError, ValueError, IndexError):
            continue
    for ins in (edl or {}).get("inserts") or []:
        try:
            total += float((ins or {}).get("duration_s") or 0.0)
        except (TypeError, ValueError):
            continue
    return total


def _mono_windows(edl):
    out = []
    for it in (edl or {}).get("fx") or []:
        if not isinstance(it, dict) or it.get("type") != "grade_window":
            continue
        if it.get("region") or it.get("isolate") or it.get("lut"):
            continue                     # a kept colour / a LUT is not flat
        sat = it.get("saturation")
        try:
            if sat is None or float(sat) > MONO_SATURATION:
                continue
            out.append((float(it["start"]), float(it["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _covered(spans):
    total, last = 0.0, None
    for a, b in sorted(spans):
        if last is not None and a < last:
            a = last
        if b > a:
            total += b - a
            last = b if last is None else max(last, b)
    return total


def whole_mono(edl):
    """Why the program reads as one flat black-and-white look, or None."""
    fx = (edl or {}).get("effects") or {}
    if fx.get("grade") == "bw":
        return "the 'bw' grade turns the whole short black and white"
    prog = _program_s(edl)
    if prog <= 0:
        return None
    share = _covered(_mono_windows(edl)) / prog
    if share >= WHOLE_SHARE:
        return (f"black-and-white grade windows cover {100 * share:.0f}% of "
                "the short")
    return None


def check_write(user_text, before, after):
    """(refusal or None, note or None) for a write from ``before`` to
    ``after``. Only a write that MAKES the program flat B&W is judged; an
    edit that was already flat B&W keeps it (the user asked earlier)."""
    why = whole_mono(after)
    if not why or whole_mono(before):
        return None, None
    ask = asked(user_text)
    if ask:
        return None, None
    if ask is False:
        return (f"REJECTED: {why}, and the user did not ask for black and "
                "white. In this product B&W is a device, never a flat look. "
                + DEVICES_LINE + "."), None
    return None, (f"NOTE (taste): {why}. Keep it only if the user asked for "
                  "black and white; otherwise " + DEVICES_LINE + ".")
