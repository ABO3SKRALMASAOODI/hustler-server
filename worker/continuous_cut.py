"""Plan continuous, non-speech footage from visible action, not shot quotas."""
import base64
import json
import subprocess
from types import SimpleNamespace

import llm
import request_contract

SCHEMA = {
    "type": "object", "required": ["subject", "reason", "start_boundary", "end_boundary", "adjustments"],
    "properties": {
        "subject": {"type": "string"}, "reason": {"type": "string"},
        "start_boundary": {"type": "integer", "minimum": 0, "maximum": 12},
        "end_boundary": {"type": "integer", "minimum": 1, "maximum": 12},
        "adjustments": {"type": "object", "properties": {
            key: {"type": "number", "minimum": -.15, "maximum": .15}
            for key in ("exposure", "contrast", "saturation", "temperature", "shadows", "highlights")},
            "additionalProperties": False},
    }, "additionalProperties": False,
}
SYSTEM = """Choose a first edit of continuous footage without intelligible speech.
The timestamped frames span the entire source; boundary IDs are actual source
times. Identify its visible subject and coherent action. Preserve a dance,
performance, musical phrase, or sports sequence continuously. Do not manufacture
four cuts, change speed, replace its audio, invent an unseen payoff, or add a
generic title. Keep the whole take when its continuity matters; otherwise choose
ONE coherent excerpt using supplied boundary IDs, including setup and aftermath.
Avoid trimming through an action just to hit a target duration. Do not assume
that no transcript means no important audio. Suggest only modest color controls
justified by the visible source, or adjustments={} when already well exposed.
These controls use small signed deltas: zero is neutral. Return the typed plan
with plan_continuous_take, grounded in what these frames actually show."""


def whole_take(ctx):
    """The plan with nothing decided: the whole take, untouched."""
    return {"subject": "", "reason": "kept whole", "start_boundary": 0,
            "end_boundary": 12, "adjustments": {},
            "keep": [[0.0, round(float(ctx.index["video"]["duration"]), 3)]]}


def plan_or_whole(ctx, spend):
    """The model's plan, or the whole take when there is no Claude lane or no
    usable answer. Choosing the excerpt is an edit, so it never runs on
    another provider, and an unanswered look never sinks the First cut."""
    if not getattr(ctx, "first_cut_model", None):
        return whole_take(ctx)
    try:
        return plan(ctx, spend)
    except Exception as exc:  # noqa: BLE001 - the take is still the first cut
        print(f"[first_cut] continuous plan failed: {type(exc).__name__}: "
              f"{str(exc)[:160]}", flush=True)
        return whole_take(ctx)


def plan(ctx, spend):
    video = ctx.index["video"]
    duration = float(video["duration"])
    boundaries = [round(duration * i / 12, 3) for i in range(13)]
    path = ctx.proxy_path()
    parts = [{"type": "text", "text": json.dumps({
        "video": video, "boundaries": dict(enumerate(boundaries)),
        "audio_is_preserved": True})}]
    for i in range(12):
        at = (boundaries[i] + boundaries[i + 1]) / 2
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-threads", "1", "-ss", str(at),
            "-i", path, "-filter_threads", "1", "-frames:v", "1", "-vf", "scale=256:-2",
            "-c:v", "mjpeg", "-threads", "1", "-q:v", "6", "-f", "image2", "-"],
            capture_output=True, timeout=30)
        if result.returncode or not result.stdout:
            raise ValueError(f"Cannot inspect source interval {i}; frame decoding failed")
        parts.extend([
            {"type": "text", "text": f"Source {at:.3f}s, between boundaries {i} and {i + 1}:"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," +
                                                     base64.b64encode(result.stdout).decode()}}])
    import claude_lane
    model = ctx.first_cut_model
    client = claude_lane.ClaudeLaneClient(timeout=90)
    tool = {"type": "function", "function": {
        "name": "plan_continuous_take", "description": "Record the evidence-based continuous edit.",
        "parameters": SCHEMA}}
    response = llm.create_with_dialect(client, model,
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": parts}],
        max_tokens=1600, tools=[tool],
        tool_choice={"type": "function", "function": {"name": "plan_continuous_take"}})
    calls = list(response.choices[0].message.tool_calls or [])
    spend["usd"] += float(llm.provider_cost_usd(response.usage, model) or 0)
    llm.record("first_cut_continuous_plan", {"model": model, "frames": 12},
               {"calls": [{"name": c.function.name, "arguments": c.function.arguments} for c in calls]},
               response.usage)
    if len(calls) != 1 or calls[0].function.name != "plan_continuous_take":
        raise ValueError("Continuous footage needs a grounded edit plan")
    chosen = json.loads(calls[0].function.arguments)
    request_contract._validate(chosen, SCHEMA)
    start, end = chosen["start_boundary"], chosen["end_boundary"]
    if start >= end or boundaries[end] - boundaries[start] < min(3.0, duration):
        raise ValueError("Continuous selection must preserve a playable action interval")
    chosen["keep"] = [[boundaries[start], boundaries[end]]]
    return chosen
