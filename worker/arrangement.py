"""The arrangement shaper: the user's track bent to the story (track 2D).

ROADMAP 2D. Two halves:

1. ``fit(...)`` SOLVES the music item's own fields (offset_s, start, end,
   fades) so the track's DROP lands on the payoff word's onset (±1 frame)
   and the bed ends on a bar line before the end card — the "bar-aligned
   music edit" expressed in the existing music layer (one item, nothing
   new for the renderer).
2. ``ARRANGE`` compiles ``arrange`` fx items on the music bus (fxgraph
   ``music_bus`` stage: after the item's fit and fades, before the mix, on
   the program clock): gain holes with 10 ms fades, the held silence
   before the payoff, low/high-pass sweeps that open on a moment, and
   1/8 - 1/16 stutters.

Only ever on a track the user supplied (fx_schemas/arrange.py refuses any
other); with no confident downbeat grid the shaper is not offered.
"""

import math

FILTERS = ("aeval", "asplit", "amix", "lowpass", "highpass", "atrim",
           "asetpts", "aloop", "adelay", "anull", "afade", "aformat")
KEEPS_FRAME_TAGS = True            # audio only: picture frames untouched
HOLE_FADE_MS = 10.0
OPEN_S = 0.005                     # a filter opens over this (no click)
SR = 48000                         # the bus's rate (renderer: aresample)


def _gain_expr(a, b, f):
    """volume expression: 1, ramping to 0 over f before a, 0 in [a, b),
    back to 1 over f after b."""
    return (f"if(lt(t,{a - f:.4f}),1,if(lt(t,{a:.4f}),({a:.4f}-t)/{f:.4f},"
            f"if(lt(t,{b:.4f}),0,if(lt(t,{b + f:.4f}),(t-{b:.4f})/{f:.4f},"
            "1))))")


def _gain(expr):
    """A SAMPLE-accurate gain: aeval evaluates per sample (``volume``'s
    eval=frame steps once per audio frame, ~21 ms at 48 kHz: a hole would
    open late and click)."""
    return f"aeval=exprs='val(ch)*({expr})':c=same"


def _xfade_expr(a, b, r, inside):
    """Gain of the FILTERED branch (inside=True) or the dry one: the
    filtered sound owns [a, b); each hand-over is an r-second crossfade
    that STARTS on its moment, so the frame before the drop is still fully
    filtered and the drop frame is open (r = 5 ms: no click, inside one
    frame at any rate)."""
    g = (f"if(lt(t,{a:.4f}),0,if(lt(t,{a + r:.4f}),(t-{a:.4f})/{r:.4f},"
         f"if(lt(t,{b:.4f}),1,if(lt(t,{b + r:.4f}),({b + r:.4f}-t)/{r:.4f},"
         "0))))")
    return g if inside else f"1-({g})"


class _Arrange:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = True

    def prepare(self, plan, item):
        # records (montage_intent, fit_intent) compile to nothing; they are
        # never a degradation either
        return None

    def cost_ms(self, plan, item):
        return 5.0

    def music_bus(self, ctx, items):
        label = ctx.label
        parts = ctx.parts
        if any(it.get("kind") not in ("montage_intent", "fit_intent")
               for it in items):
            # one known layout on the bus before any per-sample gain: aeval
            # on a MONO track that is later mixed with the stereo program
            # segfaults ffmpeg (8.0 and 9.0, reproduced) — the whole fx
            # graph then fell back and the arrangement was silently lost
            u0 = ctx.uid("arf")
            parts.append(f"[{label}]aformat=sample_fmts=fltp:"
                         f"channel_layouts=stereo[{u0}]")
            label = u0
        for it in sorted(items, key=lambda x: float(x.get("start") or 0)):
            kind = it.get("kind")
            if kind in ("montage_intent", "fit_intent"):
                continue
            a, b = float(it["start"]), float(it["end"])
            u = ctx.uid("ar")
            if kind in ("hole", "hold"):
                f = float(it.get("fade_ms") or HOLE_FADE_MS) / 1000.0
                parts.append(f"[{label}]{_gain(_gain_expr(a, b, f))}[{u}]")
                label = u
            elif kind == "filter":
                typ = it.get("filter") or "lowpass"
                hz = float(it.get("from_hz") or (600.0 if typ == "lowpass"
                                                 else 1200.0))
                r = float(it.get("open_s") or OPEN_S)
                parts.append(f"[{label}]asplit=2[{u}w][{u}d]")
                parts.append(f"[{u}w]{typ}=f={hz:.1f}:p=2,{typ}=f={hz:.1f}"
                             f":p=2,{_gain(_xfade_expr(a, b, r, True))}"
                             f"[{u}f]")
                parts.append(f"[{u}d]{_gain(_xfade_expr(a, b, r, False))}"
                             f"[{u}g]")
                parts.append(f"[{u}f][{u}g]amix=inputs=2:normalize=0:"
                             f"duration=longest[{u}]")
                label = u
            elif kind == "stutter":
                sl = float(it.get("slice_s") or 0.0)
                if not sl:
                    sl = 0.125 if it.get("slice") == "1/8" else 0.0625
                n = max(1, int(round((b - a) / sl)))
                f = 0.004
                parts.append(f"[{label}]asplit=2[{u}m][{u}s]")
                parts.append(f"[{u}s]atrim=start={a:.4f}:end={a + sl:.4f},"
                             f"asetpts=PTS-STARTPTS,afade=t=in:d={f}:st=0,"
                             f"afade=t=out:d={f}:st={max(0.0, sl - f):.4f},"
                             f"aloop=loop={n - 1}:size={int(round(sl * SR))}"
                             f",adelay={int(round(a * 1000))}:all=1[{u}l]")
                parts.append(f"[{u}m]{_gain(_gain_expr(a, a + n * sl, f))}"
                             f"[{u}q]")
                parts.append(f"[{u}q][{u}l]amix=inputs=2:normalize=0:"
                             f"duration=first[{u}]")
                label = u
        return label


ARRANGE = _Arrange()


# ── fit: land the drop on the payoff ─────────────────────────────────────

def fit(music_item, rmap, payoff_t, program_s, *, fps=30.0, outro_bars=1,
        fade_out_s=None, lead_in_s=None):
    """The music item's fields so that the track's drop (the first drop at
    or after its current position, else the strongest) sounds exactly on
    ``payoff_t`` (program seconds, snapped to a frame), and the bed ends on
    a bar line at most ``program_s``. Returns (fields dict, report)."""
    drops = sorted(rmap.get("drops") or [])
    downs = sorted(rmap.get("downbeats") or [])
    if not drops:
        return None, "the track has no drop to land"
    start = float(music_item.get("start") or 0.0)
    pay = round(round(float(payoff_t) * fps) / fps, 4)
    # the drop whose lead-in fits before the payoff in the program
    cands = [d for d in drops if d - (pay - start) >= 0.0]
    if not cands:
        return None, ("the payoff comes earlier in the program than any "
                      "drop can be reached from the track's start")
    d = cands[0]
    offset = round(d - (pay - start), 3)
    end = float(program_s)
    bar_end = None
    if downs:
        # the bed's last bar line inside the program (track clock ->
        # program clock), after the drop
        prog = [start + (t - offset) for t in downs
                if start + (t - offset) <= program_s - 0.05 and t > d]
        if prog:
            bar_end = prog[-1]
            end = bar_end if program_s - bar_end <= 4.0 else end
    fields = {"offset_s": max(0.0, offset), "start": round(start, 3),
              "end": round(end, 3),
              "fade_in_s": round(float(lead_in_s) if lead_in_s is not None
                                 else min(0.5, max(0.0, pay - start) / 4), 2),
              "fade_out_s": round(float(fade_out_s) if fade_out_s is not None
                                  else 0.4, 2)}
    rep = (f"drop {d:.2f}s of the track lands on the payoff at {pay:.2f}s "
           f"(offset_s {fields['offset_s']:.2f})" +
           (f"; the bed ends on a bar line at {bar_end:.2f}s"
            if bar_end else ""))
    return fields, rep


def hold_before(payoff_t, gap_s=0.45, fps=30.0):
    """(start, end) of a held silence ending one frame before the payoff."""
    end = round(round(float(payoff_t) * fps) / fps, 4)
    return round(max(0.0, end - gap_s), 3), round(end, 3)


def drop_on_program(music_item, rmap):
    """Program seconds of each drop of a fitted music item."""
    start = float(music_item.get("start") or 0.0)
    off = float(music_item.get("offset_s") or 0.0)
    return [round(start + d - off, 3) for d in rmap.get("drops") or []
            if d >= off]
