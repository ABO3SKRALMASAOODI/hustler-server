"""Cut verified chapter moments on the final word clock, before the film scratch is removed."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shell"))
import brand
from delivered import _shell_llm
from batch_films import cost, safe_name

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Arial,76,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,2,85,85,1290,1
Style: Caption,Arial,68,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,8,85,85,1300,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def moments(script, words, ask=None):
    """A single selection call indexes complete spoken segments within each chapter."""
    segments = script["segments"]
    lines = [dict(id=i, chapter=s["chapter"], words=s["say"], beat=s.get("beat"))
             for i, s in enumerate(segments)]
    prompt = ("Choose the strongest self-contained excerpt from each chapter, up to ten different chapters. "
              "Use consecutive complete segments from that chapter. Aim for 20–50 seconds. Open with a line "
              "that makes sense to a new viewer and end on the answer. Give a short on-screen hook grounded "
              "entirely in the words. Return JSON {moments:[{chapter,first,last,hook}]}, using segment IDs.")
    llm = _shell_llm()
    ask = ask or llm.ask_openai
    response, usage = ask(prompt, json.dumps(lines, ensure_ascii=False), model="gpt-6.2-sol", effort="medium")
    picks = {r["chapter"]: r for r in llm.json_block(response)["moments"]}
    found = []
    for chapter in script["chapters"]:
        cid = chapter["id"]
        indices = [i for i, s in enumerate(segments) if s["chapter"] == cid and
                   any(w["seg"] == i for w in words)]
        if not indices or (len(script["chapters"]) > 10 and cid not in picks):
            continue
        pick = picks.get(cid, {})
        a, b = pick.get("first"), pick.get("last")
        if a not in indices or b not in indices or b < a or not set(range(a, b + 1)).issubset(indices):
            turn = next((i for i in indices if segments[i].get("beat") == "turn"), indices[0])
            a, b = indices[max(0, indices.index(turn)-1)], turn
            pick = {}
        spoken = [w for w in words if a <= w["seg"] <= b]
        start, end = spoken[0]["t0"], spoken[-1]["t1"]
        found.append(dict(chapter=cid, title=pick.get("hook") or chapter["title"], first=a, last=b,
                          start=start, end=end, words=spoken,
                          facts=sorted({fact for i in range(a, b+1) for fact in segments[i].get("facts", [])})))
    return found[:10], usage


def timestamp(seconds):
    cs = max(0, round(seconds * 100))
    return f"{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}"


def event(start, end, style, text):
    plain = " ".join(text.split()).replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    return f"Dialogue: 0,{timestamp(start)},{timestamp(end)},{style},,0,0,0,,{plain}\n"


def subtitles(moment):
    start = moment["start"]
    out = [HEADER, event(0, min(4, moment["end"] - start), "Hook", moment["title"])]
    group = []
    for word in moment["words"]:
        if group and (len(group) == 5 or word["t0"] - group[-1]["t1"] > .35 or
                      word["seg"] != group[-1]["seg"]):
            out.append(event(group[0]["t0"]-start, group[-1]["t1"]-start, "Caption", " ".join(w["w"] for w in group)))
            group = []
        group.append(word)
    if group:
        out.append(event(group[0]["t0"]-start, group[-1]["t1"]-start, "Caption", " ".join(w["w"] for w in group)))
    return "".join(out)


def render(source, moment, folder, scratch, stems=None):
    """Preserve the entire cited landscape image and its checked chapter score in a phone frame."""
    duration = moment["end"] - moment["start"]
    (scratch / "words.ass").write_text(subtitles(moment))
    programme = scratch / "programme.mp4"
    graph = ("[0:v]split[fg][bg];[bg]scale=270:480:force_original_aspect_ratio=increase,"
             "crop=270:480,gblur=sigma=16,eq=brightness=-0.18,scale=1080:1920[base];"
             "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2[image];"
             "[base][image]overlay=(W-w)/2:(H-h)/2,setsar=1,ass=words.ass[v]")
    inputs = ["-ss", str(moment["start"]), "-i", str(source)]
    if stems:
        # Refit the chapter's licensed cue to this excerpt's opening and landing using the lossless stems.
        for stem in stems:
            inputs.extend(["-ss", str(moment["start"]), "-i", str(stem)])
        fade = min(1.2, duration / 5)
        graph += (f";[2:a]afade=t=in:d={min(.4,fade)},afade=t=out:st={duration-fade}:d={fade}[bed];"
                  "[1:a][bed]amix=inputs=2:duration=first:normalize=0[shortmix]")
        audio = "[shortmix]"
    else:
        audio = "[0:a]"
    graph += (f";{audio}atrim=duration={duration},afade=t=in:d=0.025,"
              f"afade=t=out:st={max(0,duration-.1):.3f}:d=0.1,loudnorm=I=-14:LRA=9:TP=-1.8,aresample=48000[a]")
    subprocess.run([brand.FF, "-v", "error", "-y", *inputs, "-t", str(duration), "-filter_complex", graph,
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(programme)], cwd=scratch, check=True)
    finish = brand.finish(str(programme), str(folder), str(scratch), accent=False, last_word_s=duration)
    return dict(finish=finish, checks=brand.check(str(programme), str(folder), str(scratch)))


def write(ctx, part, results):
    import film
    from shell import jsave, jload
    run, supply = Path(ctx.run_dir), Path(ctx.supply)
    # A repaired render updates the saved score and render. Read the same screenplay and clock it used.
    script = jload(run / "script.json")["script"]
    pack = jload(supply / "pack.json")["pack"]
    rd = jload(run / "render.json") or results["render"]
    clock = film.Clock(pack["words"], script)
    cache = run / "chapter-shorts"
    cache.mkdir(exist_ok=True)
    if not (cache / "started").exists():
        (cache / "started").write_text(str(time.time()))
    tag = ctx.run_id + "-chapter-shorts"
    parent = os.environ.get("CODEX_PROXY_PROOF")
    import chain
    fingerprint = chain.file_sha(rd["film"])
    plan_file = cache / "plan.json"
    plan = jload(plan_file) or {}
    if plan.get("master_sha256") != fingerprint:
        os.environ["CODEX_PROXY_PROOF"] = tag
        try:
            picks, use = moments(script, clock.words)
        finally:
            if parent is None:
                os.environ.pop("CODEX_PROXY_PROOF", None)
            else:
                os.environ["CODEX_PROXY_PROOF"] = parent
        plan = dict(master_sha256=fingerprint, moments=picks, spend=use)
        jsave(str(plan_file), plan)
    dest = Path(part) / "Shorts"
    dest.mkdir()
    listing = []
    for n, pick in enumerate(plan["moments"], 1):
        filename = f"{n:02d} - {safe_name(pick['title'], 70)}.mp4"
        file = cache / f"{fingerprint}-{n:02d}.mp4"
        receipt = file.with_suffix(".json")
        if not receipt.exists() or not file.exists():
            with tempfile.TemporaryDirectory(dir=ctx.tmp) as tmp:
                result = render(rd["film"], pick, file, Path(tmp),
                                stems=(rd["clean"], Path(rd["film"]).with_suffix(".bed.wav")))
            jsave(str(receipt), result)
        from batch_films import copy_master
        copy_master(file, dest / filename)
        music = next((m.get("choice") for m in rd["music"]["chapters"] if m["chapter"] == pick["chapter"]), None)
        listing.append(dict(n=n, file=filename, title=pick["title"], chapter=pick["chapter"], music=music,
                            source_window=[pick["start"], pick["end"]], facts=pick["facts"], **jload(receipt)))
    metrics = cost(cache, {tag}, len(listing)) if listing else None
    state = jload(run / "run.json")
    started = dt.datetime.fromisoformat(state["started"].replace("Z", "+00:00")).timestamp()
    whole_run = cost(cache, {ctx.run_id, tag, parent} - {None, ""}, len(listing) + 1, started=started)
    jsave(str(dest / "shorts.json"), dict(contract=1, published=False, shorts=listing, master_sha256=fingerprint,
                                         cost=metrics, cost_including_long_film=whole_run))
    return dict(folder="Shorts", count=len(listing), manifest="Shorts/shorts.json", cost_including_long_film=whole_run)
