"""direct: the Director's film on the upload, as an edit op (HARNESS_V2.md
4.1; EDIT-CORE 19 Nov 2026).

    {"direct": {"brief"?: str}}

The Director (score.film: read the upload, Scout, Supply of real pictures,
the bed, Direct, fill, sound, lower) writes a whole film from the full
upload as its own version. In v2 it runs before the staged ops of the same
``edit`` call, so the other ops in that call build on the film it made.

It runs where the Director runs today: the owner's switch on it
(app_kv.director) and the length of material it was built for. When it
cannot run, or a stage of it falls, the envelope says so in its own words
and names what reaches the same film with ops (finish, place, scenes), and
every other op of the call still lands. Never a gate.
"""

INSTEAD = ("the same film with ops: finish (look, captions, moves, music, "
           "sound) plus place/scenes for the pictures")


def run(ctx, value, env, index):
    """Run the Director on ``ctx`` (the real project, not a stage). Returns
    the version it wrote, or None."""
    v = value if isinstance(value, dict) else {}
    try:
        from score import asks as asks_mod
        cfg = asks_mod.read(ctx.db)
    except Exception:  # noqa: BLE001 - the Director's own default deadline
        cfg = {"film_s": 220.0}
    if v.get("brief"):
        # The Scout reads the customer's messages; a brief the model wrote
        # rides on the ask ledger the Director already reads.
        ledger = getattr(ctx, "ask_ledger", None) or {"asks": []}
        ledger = dict(ledger, asks=list(ledger.get("asks") or []) + [
            {"said": str(v["brief"])[:400], "want": str(v["brief"])[:400]}])
        try:
            ctx.ask_ledger = ledger
        except Exception:  # noqa: BLE001
            pass
    try:
        from score import asks as asks_mod
        got = asks_mod.direct_film(ctx, ctx.db, getattr(ctx, "job", None)
                                   or {}, getattr(ctx, "session_id", None),
                                   {"film_s": float(cfg.get("film_s")
                                                    or 220.0)})
    except Exception as exc:  # noqa: BLE001 - one op, never the call
        env.skip(index, f"direct raised {type(exc).__name__}: "
                        f"{str(exc)[:200]}", INSTEAD)
        return None
    version = got.get("version")
    if version:
        env.finding(f"direct: the Director made the film from the whole "
                    f"upload as v{version} in {got.get('s')} s"
                    + (f" ({', '.join(map(str, got.get('applied') or []))})"
                       if got.get("applied") else ""))
        return version
    env.skip(index, f"direct: {got.get('fell') or 'no film was written'}",
             INSTEAD)
    return None


def split(ops):
    """(direct ops [(index, value)], ops with each direct op marked done)."""
    from harness2 import ops as ops_mod
    found, out = [], []
    for i, op in enumerate(ops):
        if isinstance(op, dict) and "direct" in op and \
                ops_mod.op_name(op) is None:
            found.append((i, op.get("direct")))
            out.append({ops_mod.DONE_KEY: "direct"})
        else:
            out.append(op)
    return found, out
