"""Department ``verify``: pre-production (Wave 3, 3F).

  plan_footage  READ: the shortest shot list that covers the devices a brief
                asks for (<= 12 shot cards, each with framing, lens, camera,
                light, action and a framing diagram; filmable in <= 20 min on
                a phone) plus the acquisition dialogue: at most three ways
                forward, each with its cost and time (coverage.py)

Browserless and model-free (a fixed shot library, lexical parsing); works
on the agent, MCP and Shorts lanes. The shot sheet is drawn with OpenCV and
shown to an editor that sees; the SVG cards ride in the structured result.
Contract: devices/INTERFACES_W3.md §9.
"""

import os
import tempfile

DEPARTMENT = "verify"
VERBS = ("plan_footage",)


def _aspect(ctx):
    try:
        edl = ctx.latest_edl()["json"] or {}
        out = edl.get("output") or {}
        a = out.get("aspect") or edl.get("aspect")
        if a:
            return str(a)
    except Exception:  # noqa: BLE001
        pass
    return "9:16"


def _uploaded_minutes(ctx):
    try:
        d = float(((ctx.index or {}).get("video") or {}).get("duration")
                   or 0.0)
        return d / 60.0
    except Exception:  # noqa: BLE001
        return 0.0


def _have(have):
    import coverage
    out = []
    for h in have or []:
        k = str(h).strip().lower().replace(" ", "_").replace("-", "_")
        if k in coverage.SHOTS:
            out.append(k)
            continue
        for key, spec in coverage.SHOTS.items():
            if k and k in spec["name"].lower().replace(" ", "_"):
                out.append(key)
                break
    return out


def _sees(ctx):
    try:
        import llm
        return bool(getattr(ctx, "sight_out", False) or (
            getattr(ctx, "direct_sight", False)
            and llm.agent_sees(ctx.agent_model)))
    except Exception:  # noqa: BLE001
        return False


def plan_footage(ctx, brief, have=None, aspect=None):
    """READ: shot cards for the brief's devices + the acquisition options."""
    import coverage
    brief = str(brief or "").strip()
    if not brief:
        return ("plan_footage needs a brief: what the piece should do "
                "(e.g. 'type on the floor under my shadow, the price on the "
                "desk, a card on my fingertip').")
    aspect = str(aspect or _aspect(ctx))
    plan = coverage.plan_footage(brief, aspect=aspect, have=_have(have))
    options = coverage.acquisition(plan, _uploaded_minutes(ctx))
    text = coverage.format_plan(plan, options)
    if not plan["devices"]:
        text = ("No world device in the brief (floor, desk, wall, behind an "
                "object, hands, head, silhouette, product, screen): the "
                "base shots below cover a talking-head piece.\n" + text)
    if _sees(ctx) and plan["shots"]:
        try:
            fd, path = tempfile.mkstemp(prefix="shots_", suffix=".png")
            os.close(fd)
            if coverage.sheet_png(plan, path, aspect=aspect):
                ctx.pending_images.append(
                    ("plan_footage — the shot cards (framing diagrams)",
                     path))
        except Exception as e:  # noqa: BLE001 — the text is the answer
            print(f"[plan_footage] sheet skipped: {e}", flush=True)
    return text


TOOL_SPECS = {
    "plan_footage": (
        plan_footage,
        "READ: pre-production. From a brief, the shortest shot list that "
        "covers the devices it asks for — <= 12 shot cards (framing, lens, "
        "camera, light, what to do, which device it enables, a framing "
        "diagram), filmable in <= 20 min on a phone — and at most three "
        "ways forward with their cost and time (shoot the list / edit what "
        "was uploaded / stand-ins). Use it when footage cannot carry a "
        "device (no surface, no object in front, no raised hand) instead "
        "of saying it is not possible. have: shots the user already has.",
        {"brief": {"type": "string"},
         "have": {"type": "array", "items": {"type": "string"}},
         "aspect": {"type": "string", "enum": ["9:16", "4:5", "1:1",
                                               "16:9"]}}),
}

REQUIRED_ARGS = {"plan_footage": ["brief"]}
WRITE_TOOLS = set()
COMPACT = {
    "plan_footage": (
        "READ: shot list for a brief's devices (<=12 cards with framing "
        "diagrams, <=20 min on a phone) + <=3 options with cost and time. "
        "Use when the footage cannot carry a device."),
}
