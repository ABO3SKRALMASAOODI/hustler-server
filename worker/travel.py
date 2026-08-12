"""Round 51 — the ONE traveling-zoom implementation.

A zoom whose centre moves is the move that makes a screen recording readable:
the frame stays pushed in and glides from the button that was clicked to the
next one, instead of cutting in, out and in again. Round 45 built it for
`showcase_demo` (mode 'follow') and left the maths inline in three places —
the tool that builds the waypoints, the renderer that emits the expressions,
and nothing else could reach either. `add_zoom_path` needs exactly the same
motion on footage the user recorded themselves, so all of it lives here and
BOTH callers go through it. There is no second traveling zoom.

Two things this module owns, and why they are separated:

  waypoints_to_path()  — absolute OUTPUT seconds in, FRACTIONS OF THE WINDOW
      out. Path points are stored as fractions for the reason round 45
      documented on ZoomPathPoint: zooms are content-anchored, so
      remap_program_items slides a zoom's start/end whenever an unrelated cut
      moves the footage under it. Absolute path times would be stranded by
      that move; fractions ride along for free.

  path_value_expr() / strength_expr()  — the per-frame ffmpeg expressions.
      Emitted as ONE expression per axis (not a filter per segment) because
      zoompan evaluates them per frame, so the editor may use as many
      keyframes as the motion requires.

BYTE-IDENTITY: mode 'follow' (ease None) emits character-for-character what
round 45 emitted. Renders are cached by EDL fingerprint, and a cosmetic change
to a legacy expression would silently re-encode every demo ever made.
"""

# The easing applied BETWEEN keyframes. Both are exact and cheap to evaluate.
#
# 'cubic_in_out' is the cubic Hermite ease 3u^2-2u^3: zero velocity at every
# keyframe, so the frame arrives at a button, settles, and leaves again —
# which is what "smooth" means to the eye. It is written as u*u*(3-2*u) with
# three references to u rather than the piecewise penner form (four references
# plus a pow() per segment), keeping large paths' filtergraphs and per-frame
# work smaller without limiting how many keyframes the editor may use.
#
# 'linear' is constant velocity through the keyframes — right for a steady
# scan across a wide screenshot, wrong for a cursor stopping at a button.
EASES = ("cubic_in_out", "linear")
DEFAULT_EASE = "cubic_in_out"

# A path is a keyframe track, not a spline: two points minimum (a single point
# is a fixed target, which is add_zoom's job). There is no arbitrary maximum.
PATH_MIN_POINTS = 2
# Two keyframes closer together than this in window-fraction terms are the
# same instant at any sane window length; keeping both would emit a segment
# with a near-zero denominator.
MIN_POINT_GAP_F = 0.004


def ease_value(u, ease=None):
    """Python mirror of the emitted curve. The tests assert the rendered
    frames against THIS, so the two must never drift."""
    u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else float(u))
    if ease == "linear" or ease is None:
        return u
    return u * u * (3.0 - 2.0 * u)


def _ease_expr(inner, ease):
    """Wrap a 0..1 ffmpeg expression in the easing curve.

    `inner` is referenced up to three times, so it is always the cheap
    clip() form — never something with a function call in it.
    """
    if ease == "linear" or ease is None:
        return inner
    return f"({inner})*({inner})*(3-2*({inner}))"


def path_points(z):
    """The waypoints of a traveling zoom, or [] for any other kind."""
    if (z.get("mode") or "punch") not in ("follow", "path"):
        return []
    return list(z.get("path") or [])


def is_travel(z):
    return len(path_points(z)) >= PATH_MIN_POINTS


def waypoints_to_path(waypoints, start, end, *, with_strength=False,
                      default_strength=0.25):
    """[{t, cx, cy, strength}] in absolute OUTPUT seconds -> window fractions.

    Returns (points, error). Points carry `f` (0 = window start, 1 = window
    end), `cx`/`cy`, and — only when with_strength — `s`. Waypoints are sorted
    by time and de-duplicated; a `t` outside [start, end] is clamped, because
    the window is derived from the waypoints by every caller here and a
    clamped endpoint is the honest reading of "start the move here".

    `s` is omitted (not written as null) when with_strength is False so a
    'follow' path dumps exactly the keys round 45 wrote.
    """
    span = float(end) - float(start)
    if span <= 0:
        return None, "the zoom window has no duration."
    out = []
    for w in sorted(waypoints, key=lambda p: float(p["t"])):
        f = (float(w["t"]) - float(start)) / span
        f = 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)
        if out and f - out[-1]["f"] < MIN_POINT_GAP_F:
            # Same instant: the LAST one wins. A caller correcting a keyframe
            # by re-stating it at the same time means the correction.
            out.pop()
        p = {"f": round(f, 4),
             "cx": round(min(max(float(w["cx"]), 0.0), 1.0), 3),
             "cy": round(min(max(float(w["cy"]), 0.0), 1.0), 3)}
        if with_strength:
            s = w.get("strength")
            s = default_strength if s is None else float(s)
            p["s"] = round(s, 3)
        out.append(p)
    if len(out) < PATH_MIN_POINTS:
        return None, ("a path needs at least two keyframes at different "
                      "times — one position is a fixed target, which is "
                      "add_zoom's job.")
    # Pin the ends to the window so the move starts and finishes ON the
    # positions the caller named, rather than a fraction of a frame inside.
    out[0] = dict(out[0], f=0.0)
    out[-1] = dict(out[-1], f=1.0)
    return out, None


def path_value_expr(pts, key, t_expr, a, b, *, default=0.5, offset=0.0,
                    ease=None):
    """One ffmpeg expression for a keyframed value across the window [a, b].

        value(t) = v0 + SUM_i (v_i+1 - v_i) * E(clip((t - t_i)/dt_i, 0, 1))

    Every completed segment has contributed its whole delta and the current
    one contributes its eased fraction, which IS keyframe interpolation —
    and it stays a single expression, evaluated once per frame.

    `offset` is subtracted from the emitted value (the renderer wants centres
    as offsets from 0.5). Returns None when nothing varies.
    """
    if len(pts) < 2:
        return None
    v0 = float(pts[0].get(key, default))
    parts = [f"{v0 - offset:.4f}"]
    for p0, p1 in zip(pts, pts[1:]):
        t0 = a + float(p0["f"]) * (b - a)
        t1 = a + float(p1["f"]) * (b - a)
        dt = t1 - t0
        if dt <= 1e-4:
            continue
        dv = float(p1.get(key, default)) - float(p0.get(key, default))
        if abs(dv) < 1e-4:
            continue
        u = f"clip(({t_expr}-{t0:.3f})/{dt:.3f},0,1)"
        parts.append(f"{dv:.4f}*{_ease_expr(u, ease)}")
    return "+".join(parts).replace("+-", "-")


def centre_terms(z, t_expr, a, b):
    """(cx_term, cy_term) for a traveling zoom — offsets from frame centre,
    gated to the zoom's own window so several zooms can each aim somewhere
    else. Either may be None when that axis never moves.

    Legacy 'follow' (no ease key) takes the linear branch and reproduces the
    round-45 strings exactly.
    """
    pts = path_points(z)
    if len(pts) < 2:
        return None, None
    ease = z.get("ease")
    gate = f"*between({t_expr},{a:.3f},{b:.3f})"
    out = []
    for key in ("cx", "cy"):
        expr = path_value_expr(pts, key, t_expr, a, b, default=0.5,
                               offset=0.5, ease=ease)
        out.append(f"({expr}){gate}" if expr else None)
    return out[0], out[1]


def strength_term(z, t_expr, a, b):
    """The zoom AMOUNT for a traveling zoom, as a term to add to 1.

    Two shapes, and the difference is the whole reason mode 'path' exists:

    'follow' holds ONE strength and ramps it in and out at the window edges —
    the frame pushes in, travels, pulls out. The ramp is what keeps a demo
    zoom from popping.

    'path' interpolates the strength BETWEEN the keyframes and applies no
    hidden ramp at all. That is deliberate: the caller asked for a specific
    push at a specific time, the tests assert the frame at exactly those
    times, and a courtesy ramp would mean the frame at keyframe 0 is not what
    keyframe 0 says. A caller who wants a smooth entry starts (and ends) at
    strength 0 — which the tool description tells them.
    """
    st = float(z.get("strength", 0.25))
    if (z.get("mode") or "punch") != "path":
        # Ramp length: a quarter of the window, clamped — long enough to read
        # as a move, short enough that a 1s zoom still reaches full strength.
        r = max(0.15, min(0.4, (b - a) / 4.0))
        return (f"{st:.2f}*clip(({t_expr}-{a:.3f})/{r:.3f},0,1)"
                f"*clip(({b:.3f}-{t_expr})/{r:.3f},0,1)")
    pts = path_points(z)
    expr = path_value_expr(pts, "s", t_expr, a, b, default=st,
                           ease=z.get("ease"))
    if expr is None:
        expr = f"{st:.3f}"
    return f"({expr})*between({t_expr},{a:.3f},{b:.3f})"


def describe(z):
    """One human line about where a traveling zoom goes — used in tool results
    and in the EDL description, so the sentence the user reads is generated
    from the same points the renderer will use."""
    pts = path_points(z)
    if len(pts) < 2:
        return ""
    strengths = [p.get("s") for p in pts if p.get("s") is not None]
    bits = (f"travelling ({pts[0]['cx']:g},{pts[0]['cy']:g}) → "
            f"({pts[-1]['cx']:g},{pts[-1]['cy']:g}) across {len(pts)} "
            "keyframes")
    if strengths and (max(strengths) - min(strengths)) > 0.01:
        bits += (f", zoom {int(min(strengths) * 100)}%→"
                 f"{int(max(strengths) * 100)}%")
    return bits
