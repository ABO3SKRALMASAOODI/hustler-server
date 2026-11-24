"""Carry original interview evidence from a source-aspect reading to the portrait scene."""
import hashlib
import json
import math
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image

from placement_check import file_digest
from wf1_documents import image_part, visible_lines


PROMPT = """Read the numbered words and the actual uncropped frames of this interview together.
Return source_inserts: [{first,last,frame,box,tile,shows}], and quote_segments: [segment ID].
On a clause describing or pointing at visible evidence, choose the clearest supplied frame showing
that exact state of the artifact, including a cue and the reveal it introduces. Use inclusive word
IDs. Frame IDs refer to supplied pictures. box is the tight normalized [left,top,right,bottom]
enclosing the actual document, plot title, legend and BOTH labeled axes. Include a little clearance.
tile is the normalized rectangle of the live PRESENTING speaker in that frame if visibly clear,
else null. Do not interpret sample numbers as measured values. Never infer a cost chart from an
output-only chart. Leave guesses, unrelated visuals and ordinary conversational shots on the speaker.
When a presenter explains how one product works and then shows its actual result in this take,
return live_demo:{first,cue,last,frame,box,tile,shows,speaker,term_word}. first starts the explanation
(including its unanswered setup), cue is the first word pointing the viewer to the result, and last
includes the presenter's personal conclusion about it. term_word is the first word actually naming
the mechanism, when defined. Select the verified frame of the result, box around its label and one
readable completed item, retaining every source redaction. Give the synchronous presenter's tile.
Describe one item by its visible wording, including truncation. Keep the lead-in and illustrative list
of inputs on the presenter, including the hands. A list of connected applications describes sources
for this product, not separate demos of those applications. Keep independent events outside this
passage. Omit live_demo for ordinary charts, recalled demos or general conversations.
Plan the whole screen-share passage together. Return source_panels: [{frame,box,shows}] for
reusable clean original views, and presenter_tiles: [{frame,box}] for each visible speaker.
Choose a frame without an obstructing pointer, tooltip or webcam over the evidence. Keep one
panel per distinct artifact state. In source_inserts reference its zero-based panel index on
every clause that reads or compares it, including adjacent clauses and later returns.
When the opening introduces a chart, establish the full clean panel showing its title, scope and units
on the lead-in before going closer. Include every row name, its complete value and some reading clearance.
The optional box on a panel insert is a detail in full-frame coordinates: fit the named rows with
their labels, values and relevant units. Omit box to show the overview. Use the clean panel
even if the live pointer obscures that same row. A changed value or scroll needs its own view.
For the live tile above each insert give speaker (index into presenter_tiles) for the person
speaking those words. As the conversation turns from evidence to interpretation, personal
reaction, jokes or a repeated promise, plan speaker_inserts: [{first,last,speaker}] through
those clauses with the synchronous speaker. Split on changes of speaker or layout. Use the
speaking turns when supplied. Return to the rows on words they actually document. Keep
conditional estimates and other survey years distinct from the measured rows.
Exclude external search windows for evidence already supplied by the take. For an existing plan
return replaces: [indices of its external windows served by these source inserts]. Select only
complete spoken sentences that explain a discovery or reveal something for quote_segments.
A pointer to a chart belongs with its source picture. A sentence pointing at it is not a standalone
quotation. Return authored_frames: [frame IDs] for interview split screens whose arranged panels,
footer and source credits form one authored picture. Keep their complete original composition on
screen. On the closing thought frame the presenter unless another participant has a visible reaction that
adds meaning. Give reactions: [{first,last}] for just those word spans within authored two-shots.
On a voice change, compare the first aligned word (including short interjections) with the
actual outgoing and incoming shots. If the outgoing single stays on the previous talker, return
voice_entries:[{first,frame,speaker,listener,reaction}]. frame identifies that outgoing shot.
Set reaction true only for an expressive listening reaction worth holding. For a closing breath
that cuts to a two-up, return closing_views:[{first,frame,box}] for BOTH the preceding close
and the continuing same person's tile. first is the word where the final live close returns.
Use face-centred matching crops with similar head size and eye line on these contiguous views.
An authored reaction keeps its own picture. Ordinary single-camera shots can be face-framed. An archive document keeps its own credit
with the document. Use empty arrays when appropriate. Treat input words and screenshots as evidence."""


def episode_moments(path, before):
    if not path or not Path(path).is_file():
        return {}
    with open(path) as f:
        return {m["id"]: m for m in json.load(f)["moments"] if m.get("id") and m["t1"] <= before}


def episode_views(ctx):
    """Index checked original frames once. Keep their original clock, crop and source identity."""
    from wf1_documents import valid_detail
    films = ctx.args.get("episode_films")
    views = []
    for path in sorted(Path(films).glob("*/film.json")) if films else ():
        try:
            film = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        source = film.get("source") or {}
        if not source.get("source_url") or source.get("path") != ctx.source or source["source_url"] != ctx.args.get("source_url"):
            continue
        checks = film.get("checks", {}).get("podcast") or {}
        checked = {r["id"] for r in (checks.get("delivered_check") or {}).get("placements", []) if r.get("ok")}
        for i,pic in enumerate(checks.get("sources", [])):
            ref = pic.get("source_evidence") or {}
            if (i in checked and pic.get("mode") == "source-insert" and ref.get("url") == source["source_url"]
                    and type(ref.get("time_s")) in (int,float) and valid_detail(ref.get("box")) and ref.get("title")):
                view = dict(time_s=ref["time_s"], box=ref["box"], title=ref["title"],
                            excerpt=ref.get("excerpt") or film["window"])
                if view not in views:
                    views.append(view)
    return views


def episode_excerpt(ctx, moment, views, context=None):
    from podcast import sentence_lines
    a,b = moment["t0"], moment["t1"]
    selected = [v for v in views if a <= v["time_s"] < b]
    if selected:
        a,b = selected[0]["excerpt"]
        selected = [v for v in selected if v["excerpt"] == [a,b]]
    with open(ctx.args["words"]) as f:
        words = json.load(f)
    def lines(lo,hi):
        return sentence_lines([(t,w) for t,w in words if lo <= t < hi])
    excerpt = dict(excerpt=[a,b], url=ctx.args.get("source_url"), publisher=ctx.args.get("channel"),
                   transcript=lines(a,b), views=selected)
    if context and context != moment:
        lo,hi = context["t0"],context["t1"]
        excerpt["context"] = dict(excerpt=[lo,hi], transcript=lines(lo,hi))
    return excerpt


def place_callback(src, coverage, work, ffmpeg):
    """Recover the exact approved frame from the local original, retaining its blur and both clocks."""
    cue = coverage.get("callback") or {}
    excerpt = (coverage.get("story") or {}).get("callback") or {}
    views = excerpt.get("views") or []
    i = cue.get("view")
    if type(i) is not int or not 0 <= i < len(views):
        return
    view = views[i]
    t = view["time_s"]
    dest = Path(work) / f"episode-{t:.3f}.png"
    try:
        if not dest.exists():
            raw = subprocess.check_output([ffmpeg, "-v","error","-ss",str(t),"-i",src,
                "-frames:v","1","-c:v","png","-f","image2pipe","-"],timeout=60)
            if not raw:
                return
            write_cache(dest,raw)
        with Image.open(dest) as im:
            width,height = im.size
    except (OSError, subprocess.SubprocessError):
        # The earlier transcript still supplies the concrete action on this question.
        return
    row = dict(path=str(dest), t=t, box=view["box"], shows=view["title"], w=width,h=height,
               shot=excerpt["excerpt"], episode_excerpt=excerpt["excerpt"], first=cue["first"],last=cue["last"])
    reading = coverage.setdefault("source_reading", {})
    # The callback occupies only the words that refer back to this picture.
    inserts = []
    for old in reading.get("inserts", []):
        if old.get("episode_excerpt"):
            continue
        for a,b in ((old["first"], min(old["last"],row["first"]-1)),
                    (max(old["first"],row["last"]+1), old["last"])):
            if a <= b:
                inserts.append(dict(old,first=a,last=b))
    reading["inserts"] = sorted([*inserts,row], key=lambda r:r["first"])


def write_cache(path, data):
    with NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def samples(src, shots, dur, work, ffmpeg):
    """One native snapshot per short shot, with additional looks inside long continuous demos."""
    digest = file_digest(src)
    folder = Path(work) / ('source-frames-' + digest[:20])
    folder.mkdir(exist_ok=True)
    times = []
    for shot in shots or [dict(start=0, end=dur)]:
        a, b = float(shot['start']), min(float(shot['end']), dur)
        if b <= a:
            continue
        count = max(1, math.ceil((b-a)/4))
        times.extend((a+(i+.5)*(b-a)/count, a, b) for i in range(count))
    if len(times) > 16:
        times = [times[round(i*(len(times)-1)/15)] for i in range(16)]
    frames = []
    for t, a, b in times:
        path = folder / f'{t:.3f}.png'
        if not path.is_file():
            raw = subprocess.check_output([ffmpeg, '-v','error','-ss',f'{t:.3f}','-i',src,
                       '-frames:v','1','-c:v','png','-f','image2pipe','-'], timeout=60)
            if not raw:
                continue
            write_cache(path,raw)
        frames.append(dict(path=str(path), t=round(t,3), shot=[a,b]))
    return frames


def frame_parts(frames):
    parts = []
    for i, frame in enumerate(frames):
        with Image.open(frame['path']) as im:
            parts.extend([dict(type='text',text=f"Frame {i} at {frame['t']} seconds, camera interval {frame['shot']} on the take clock"),image_part(im)])
    return parts


def signature(frames):
    pixels = [(f['t'],f['shot'],file_digest(f['path'])) for f in frames]
    return hashlib.sha256(json.dumps([PROMPT,pixels]).encode()).hexdigest()


def read(coverage, frames, llm, work):
    """Memoize the joint reading by visible pixels and the already resolved spoken beats."""
    words = coverage['words']
    windows = coverage['windows']
    brief = dict(words=[[i,w.get('seg'),w['w'],w['start']] for i,w in enumerate(words)],
                 named=coverage.get('named'), story=coverage.get('story'), windows=[{k:w.get(k) for k in ('first','last','must_show')} for w in windows])
    brief['speaking_turns'] = coverage.get('speaking_turns') or []
    by_time = {f['t']:i for i,f in enumerate(frames)}
    brief['previous_views'] = [dict(frame=by_time[r['t']], box=r['box'], shows=r.get('shows'))
                               for r in (coverage.get('source_reading') or {}).get('inserts', [])
                               if r['t'] in by_time]
    identity = [signature(frames), brief]
    key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    record = Path(work) / ('source-reading-' + key[:20] + '.json')
    if record.exists():
        result = json.loads(record.read_text())
    else:
        parts = [dict(type='text',text=json.dumps(brief))]
        parts.extend(frame_parts(frames))
        text,use = llm.ask(PROMPT,parts,effort='medium',max_tokens=3500)
        result = dict(llm.json_block(text),spend=use)
        write_cache(record,json.dumps(result).encode())
    return bind(coverage, frames, result)


def bind(coverage, frames, result):
    words, windows = coverage['words'], coverage['windows']
    inserts = []
    def box(value):
        if (isinstance(value,list) and len(value)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in value)
                and 0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1):
            return value
    def catalog(key):
        regions = {}
        for i, row in enumerate(result.get(key) or []):
            if not isinstance(row, dict):
                continue
            n = row.get('frame')
            bounds = box(row.get('box'))
            if type(n) is int and 0 <= n < len(frames) and bounds:
                regions[i] = dict(frames[n], box=bounds, shows=row.get('shows'))
        return regions

    panels, tiles = catalog('source_panels'), catalog('presenter_tiles')
    speakers = []
    def addresses(row):
        if isinstance(row, dict):
            a,b = row.get('first'), row.get('last')
            return type(a) is int and type(b) is int and 0 <= a <= b < len(words)

    def reference(row, key, catalog):
        i = row.get(key)
        return catalog.get(i) if type(i) is int else None

    def in_shot(row, shot):
        selected = [i for i in range(row['first'],row['last']+1)
                    if shot[0] <= (words[i]['start']+words[i]['end'])/2 < shot[1]]
        if selected:
            return dict(row, first=selected[0], last=selected[-1])

    for row in result.get('speaker_inserts') or []:
        if addresses(row) and (tile := reference(row, 'speaker', tiles)):
            row = in_shot(row, tile['shot'])
            if not row:
                continue
            speakers.append(dict(first=row['first'], last=row['last'], tile=tile['box'], shot=tile['shot']))
    demo = result.get("live_demo") or {}
    demo_rows = []
    if (addresses(demo) and type(demo.get("cue")) is int and demo["first"] <= demo["cue"] <= demo["last"]
            and type(demo.get("frame")) is int and 0 <= demo["frame"] < len(frames) and box(demo.get("box"))
            and isinstance(demo.get("shows"),str) and demo["shows"].strip()):
        demo_rows = [dict(demo, first=demo["cue"], demonstration=True)]
    else:
        demo = {}
    def outside_demo(row):
        return not demo or row["last"] < demo["first"] or row["first"] > demo["last"]
    speakers = [r for r in speakers if outside_demo(r)]
    additional = [r for r in result.get("source_inserts") or [] if addresses(r) and outside_demo(r)]
    for row in additional + demo_rows:
        if not addresses(row):
            continue
        panel = reference(row, 'panel', panels)
        if panel:
            frame = panel
            row = in_shot(row, panel['shot'])
            if not row:
                continue
            outer = panel['box']
            bounds = box(row.get('box'))
            if not bounds or not (outer[0]<=bounds[0]<bounds[2]<=outer[2] and outer[1]<=bounds[1]<bounds[3]<=outer[3]):
                bounds = outer
        else:
            n = row.get('frame')
            bounds = box(row.get('box'))
            if not (type(n) is int and 0<=n<len(frames) and bounds):
                continue
            frame = frames[n]
        tile = reference(row, 'speaker', tiles)
        with Image.open(frame['path']) as im:
            w,h=im.size
        inserts.append(dict(frame, first=row['first'], last=row['last'], box=bounds,
                            tile=tile['box'] if tile else box(row.get('tile')),
                            tile_shot=tile['shot'] if tile else frame['shot'],
                            shows=row.get('shows') or frame.get('shows'), w=w, h=h,
                            **(dict(demonstration=True) if row.get('demonstration') else {}),
                            **(dict(panel_box=panel['box']) if panel else {})))
    # A retained pack already indexes its external windows. Only a fully covered
    # window can be retired without losing research on a different adjacent clause.
    covered = {i for row in inserts + (speakers if inserts or panels else []) for i in range(row['first'],row['last']+1)}
    if demo:
        covered.update(range(demo["first"],demo["last"]+1))
    replaces = [i for i,w in enumerate(windows) if all(j in covered for j in range(w['first'],w['last']+1))]
    authored = sorted({tuple(frames[i]['shot']) for i in result.get('authored_frames') or []
                       if type(i) is int and 0 <= i < len(frames)})
    reactions = [dict(first=r['first'], last=r['last']) for r in result.get('reactions') or [] if addresses(r)]
    def identified(key):
        for row in result.get(key) or []:
            if not isinstance(row, dict):
                continue
            first, frame = row.get('first'), row.get('frame')
            if type(first) is int and 0 <= first < len(words) and type(frame) is int and 0 <= frame < len(frames):
                yield row, frames[frame]['shot']

    # A reader may identify a presenter by tile index. Resolve it using the named
    # episode turn covering most of that tile's addressed utterance.
    owners = {}
    for r in result.get('speaker_inserts') or []:
        if addresses(r) and type(r.get('speaker')) is int:
            a, b = words[r['first']]['start'], words[r['last']]['end']
            for t in coverage.get('speaking_turns') or []:
                key = (r['speaker'], t['speaker'])
                owners[key] = owners.get(key, 0.) + max(0., min(t['end'],b)-max(t['start'],a))
    def name(ref):
        if type(ref) is int:
            matches = [(v,n) for (i,n),v in owners.items() if i == ref and v > 0]
            return max(matches)[1] if matches else None
        return ref
    entries = []
    for row, shot in identified('voice_entries'):
        speaker, listener = name(row.get('speaker')), name(row.get('listener'))
        first = row['first']
        # The claim reader can include the clipped lead-in before the episode captions switch.
        first = min([first] + [c['first'] for c in coverage.get('speaker_claims') or []
                               if c.get('speaker') == speaker and c.get('first') == first-1
                               and c.get('last',-1) >= first])
        if (all(isinstance(name, str) and name.strip() for name in (speaker,listener)) and speaker != listener
                and shot[0] <= words[first]['start'] < shot[1]):
            entries.append(dict(first=first, shot=shot, speaker=speaker, listener=listener,
                                reaction=row.get('reaction') is True))
    closing = [dict(first=r['first'], shot=shot, box=r['box']) for r,shot in identified('closing_views') if box(r.get('box'))]
    return dict(live_demo=demo, voice_entries=entries, closing_views=closing, speaker_inserts=speakers, reactions=reactions, authored_shots=[list(span) for span in authored], inserts=sorted(inserts,key=lambda r:r['first']),replaces=replaces,
                quote_segments=result.get('quote_segments') or [],spend=result.get('spend') or {})


def camera_follows_voice(entry, cuts, turns):
    """The next source cut shows the new talker while their identified turn is live."""
    join = entry["shot"][1]
    for outgoing, incoming in zip(turns, turns[1:]):
        if (outgoing["speaker"] == entry["listener"] and incoming["speaker"] == entry["speaker"]
                and incoming["start"] <= join < incoming["end"]):
            return any(abs(shot["t0"]-join) < 1e-4 and shot.get("faces")
                       and any(span["who"] == "speaker" and span["t0"] <= join < span["t1"]
                               for span in shot.get("spans", [])) for shot in cuts)
    return False


def reaction_portraits(reading):
    """When a screen share has two known cameras, hold the listener on the directed reaction."""
    speakers = reading.get("speaker_inserts") or []
    portraits = []
    for cue in reading.get("reactions") or []:
        before = next((r for r in speakers if r["last"] == cue["first"]-1), None)
        after = next((r for r in speakers if r["first"] == cue["last"]+1), None)
        if not before or not after or before["shot"] != after["shot"] or before["tile"] != after["tile"]:
            continue
        speaker = before["tile"]
        others = {tuple(r["tile"]) for r in speakers if r["shot"] == before["shot"]
                  and (r["tile"][2] <= speaker[0] or speaker[2] <= r["tile"][0]
                       or r["tile"][3] <= speaker[1] or speaker[3] <= r["tile"][1])}
        if len(others) == 1:
            portraits.append(dict(cue, shot=before["shot"], tile=list(others.pop())))
    return portraits


def presenter_crop(inserts, faces, src, aspect):
    """Frame the talking head within its documented tile between evidence clauses."""
    if not faces:
        return None
    face = max((f['box'] for f in faces), key=lambda b:(b[2]-b[0])*(b[3]-b[1]))
    centre = (face[0]+face[2])/2
    mid = sum(src)/2
    for rec in sorted(inserts,key=lambda r:abs(r['t']-mid)):
        tile = rec.get('tile')
        shot = rec.get('tile_shot', rec['shot'])
        if not tile or src[0]<shot[0]-.04 or src[1]>shot[1]+.04:
            continue
        l,t,r,b = tile
        if l<=face[0]<face[2]<=r and t<=face[1]<face[3]<=b:
            w = (b-t)*rec['h']/rec['w']*aspect
            if face[2]-face[0] <= w <= r-l:
                x = min(max(l,centre-.43*w),r-w)
                return tile,[x,t,x+w,b]
    return None


def shared_presenter_region(reading):
    """Adjacent webcam tiles can share one lossless decode of the camera column."""
    tiles = {tuple(r['tile']) for r in reading.get('inserts', []) + reading.get('speaker_inserts', [])
             if r.get('tile')}
    if not tiles:
        return None
    left,top,right,bottom = zip(*tiles)
    region = [min(left),min(top),max(right),max(bottom)]
    area = (region[2]-region[0])*(region[3]-region[1])
    return region if area <= sum((r-l)*(b-t) for l,t,r,b in tiles)+1e-8 else None


def presenter_card(plan, span, take_key, dest, tile, crop, src, work, ffmpeg, decode_region=None, **style):
    """Decode just the webcam pixels for live panels. The source clock stays intact."""
    take = plan.assets[take_key]
    pw, ph = take['w'], take['h']
    # Even outward bounds preserve the source's chroma grid. The card still
    # uses the exact verified rectangle inside this lossless intermediate.
    region = decode_region or tile
    x,y = [2*math.floor(v*n/2) for v,n in zip(region[:2],(pw,ph))]
    right,bottom = [min(n,2*math.ceil(v*n/2)) for v,n in zip(region[2:],(pw,ph))]
    w,h = right-x,bottom-y
    key = take_key
    if region == [0,0,1,1] and crop == region and style.get("presenter_insert"):
        # A full-camera panel retains the original speaker asset and all four corners.
        style["source_frame"] = True
    if work and (w,h) != (pw,ph) and Path(take['path']).is_file() and w>=2 and h>=2:
        digest = file_digest(take['path'])[:20]
        key = f"presenter-{digest}-{x}-{y}-{w}-{h}"
        path = Path(work) / (key+'.mp4')
        if key not in plan.assets:
            if not path.is_file():
                with NamedTemporaryFile(dir=work,suffix='.mp4',delete=False) as handle:
                    tmp = Path(handle.name)
                try:
                    subprocess.run([ffmpeg,'-v','error','-y','-i',take['path'],'-map','0:v:0','-an',
                                    '-vf',f'crop={w}:{h}:{x}:{y}','-c:v','libx264','-preset','ultrafast',
                                    '-qp','0',str(tmp)],check=True,timeout=120)
                    tmp.replace(path)
                except (OSError,subprocess.SubprocessError):
                    # Still draw from the parent take if its compact decode is unavailable.
                    pass
                finally:
                    tmp.unlink(missing_ok=True)
            if path.is_file():
                plan.video(key,dict(path=str(path),w=w,h=h,fps=take['fps'],seconds=take['dur']))
        if key in plan.assets:
            crop = [(crop[0]*pw-x)/w,(crop[1]*ph-y)/h,(crop[2]*pw-x)/w,(crop[3]*ph-y)/h]
        else:
            key = take_key
    plan.card(span,key,dest,crop=crop,src=src,**style)


def spoken_cells(rec):
    """Locate literal spoken cell values side by side on the same approved detail row."""
    import string
    if not rec.get("panel_box") or rec["box"] == rec["panel_box"] or not rec.get("clause"):
        return []
    def words(text):
        return [token.strip(string.punctuation).casefold() for token in text.replace("&"," and ").split()]
    spoken = words(rec["clause"])
    left,top,right,bottom = rec["box"]
    matches = []
    for text,box in visible_lines(rec["path"]):
        tokens = words(text)
        if (tokens and left <= box[0] < box[2] <= right and top <= box[1] < box[3] <= bottom
                and any(spoken[i:i+len(tokens)] == tokens for i in range(len(spoken)))):
            matches.append(box)
    for i,first in enumerate(matches):
        for other in matches[i+1:]:
            if (max(first[1],other[1]) < min(first[3],other[3]) and
                    (first[2] < other[0] or other[2] < first[0])):
                return sorted([first,other],key=lambda box:box[0])
    return []


def compose(plan, span, rec, key, take_key, src, width, height, work, ffmpeg, decode_region=None, report=False):
    """A pixel-preserving document and the synchronous presenter, clear of the caption seat."""
    from scene.motion import fit_panel
    tile = rec.get('tile')
    shot = rec.get('tile_shot', rec['shot'])
    live = tile and shot[0] <= src[0]+.04 and src[1] <= shot[1]+.04
    size = rec['w'], rec['h']
    canvas = width, height
    if rec.get("demonstration"):
        from scene.motion import demonstration_panels
        overview, detail = demonstration_panels(rec["box"], size, canvas)
        plan.page(span, "#14171B", grain=0)
        # The overview runs on the original clock. The magnifier uses the actual
        # verified frame, retaining baked-in masks even if the presenter scrolls.
        in_shot = rec["shot"][0] <= src[0]+.035 and src[1] <= rec["shot"][1]+.035
        if rec.get("presenter_result") and live:
            dest = fit_panel(tile,size,canvas,[.02,.035,.98,.405])
            presenter_card(plan,span,take_key,dest,tile,tile,src,work,ffmpeg,decode_region,
                           source_ink=[dest])
        else:
            plan.card(span, take_key if in_shot else key, overview, crop=[0,0,1,1],
                      **({"src":src} if in_shot else {}), source_ink=[overview])
        plan.card(span, key, detail, crop=rec["box"], source_ink=[detail], caption_baseline=.89)
        return
    cells = spoken_cells(rec)
    if cells:
        plan.page(span, "#151A20", grain=0)
        if live:
            dest = fit_panel(tile,size,canvas,[.025,.025,.975,.39],align_y=1)
            presenter_card(plan,span,take_key,dest,tile,tile,src,work,ffmpeg,decode_region)
        overview = fit_panel(rec["box"],size,canvas,[.035,.402,.965,.555])
        plan.card(span,key,overview,crop=rec["box"],source_ink=[overview])
        # Both labels stay still at the same scale. The complete row above preserves their relation.
        for i,(left,top,right,bottom) in enumerate(cells):
            dx,dy = .012,(bottom-top)*.29
            crop = [max(rec["box"][0],left-dx),max(rec["box"][1],top-dy),
                    min(rec["box"][2],right+dx),min(rec["box"][3],bottom+dy)]
            y = .58+i*.09
            dest = fit_panel(crop,size,canvas,[.075,y,.925,y+.066])
            plan.card(span,key,dest,crop=crop,source_ink=[dest],cell_detail=True,caption_baseline=.88)
        return
    if report:
        seat = [0,.15 if live else .03,1,.40]
    else:
        seat = [0,.26,1,.79] if live else [0,.12,1,.82]
    if rec.get("episode_excerpt") and report:
        # Keep the original rows together, below the brand and above the scenario.
        seat = [0,.31,1,.47]
    rect = fit_panel(rec['box'], size, canvas, seat,
                     align_y=1. if live else .5)
    plan.page(span,'#151A20',grain=0)
    if live:
        dest = fit_panel(tile, size, canvas, [0.,0.,1.,rect[1]], align_y=1.)
        # Centre the pair as a single composition when a short row leaves spare height.
        shift = max(0., dest[1]-.04)/2
        for panel in (rect,dest):
            panel[1] -= shift
            panel[3] -= shift
        presenter_card(plan,span,take_key,dest,tile,tile,src,work,ffmpeg,decode_region)
    plan.card(span,key,rect,crop=rec['box'],source_ink=[rect],caption_baseline=min(.90,rect[3]+.08))
