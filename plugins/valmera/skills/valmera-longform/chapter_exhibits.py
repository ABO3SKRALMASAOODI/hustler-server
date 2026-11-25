"""Supply authored documentary passages from the same originals used by the writer."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote as escape, urlsplit
import json

from PIL import Image

import cited_page
import design
from idea_exhibits import acquire, key, read_source


def passages(ctx, script, segments):
    exhibits = script.get('idea_full', {}).get('supply', {}).get('exhibits', [])
    originals = {e['url']: e for e in exhibits}
    records = {(e['url'], e['quote']): e for e in exhibits}
    facts = {}
    pages = {}
    authored = {(s.get('design') or {}).get('fact') for s in segments}
    for fact in script['dossier'].get('facts', []):
        url, quote = fact['url'], fact['quote']
        if url not in originals or not quote:
            continue
        pair = url, quote
        if pair not in records:
            # Additional dossier facts can cite the same verified report. Locate
            # their own words; a shared URL alone does not establish the quote.
            if fact['id'] not in authored:
                continue
            if url not in pages:
                try:
                    path = acquire(Path(ctx.run_dir)/'idea-originals', url, 50_000_000)
                    pages[url] = read_source(path, url)[0]
                except Exception as exc:
                    ctx.log(f'cannot locate additional passage at {url}: {exc}')
                    pages[url] = []
            page = next((i for i, body in enumerate(pages[url]) if quote in ' '.join(body.split())), None)
            if page is None:
                continue
            original = originals[url]
            records[pair] = dict(original, quote=quote, page=page+1 if original['kind']=='pdf' else None)
        facts[fact['id']] = records[pair]
    return facts


def select(ctx, dest, segments, facts, previous):
    import cinema as C
    choices, undecided = {}, []
    for i, seg in segments.items():
        design = seg.get('design') or {}
        fid = design.get('fact')
        if design.get('kind') == 'document' and fid in facts:
            choices[i] = dict(fact=fid, mark=design.get('mark'), motion='')
        else:
            undecided.append(dict(i=i, say=seg['say'], pictures=seg.get('pictures'), facts=seg.get('facts'),
                                  staging=previous.get(i)))
    if not facts or not undecided:
        return choices
    material = dict(lines=undecided, authored=list(choices.values()), exhibits={k:{f:v[f] for f in ('title','subject','quote','url')} for k,v in facts.items()})
    # A fresh motion search writes its own staging after this selection.
    # Resume the placement for the same screenplay and original sources.
    signature = key(json.dumps(dict(material, lines=[{k:v for k,v in row.items() if k!='staging'}
                                                     for row in undecided]), sort_keys=True))
    cache = dest/('passages-'+signature[:16]+'.json')
    if cache.exists():
        result = C.read(cache)
    else:
        try:
            text, usage = C.wf4.story.writer.ask(
                'Assign verified passages to documentary lines whose intended picture is the actual document, '
                'its claim, or its output. Prefer the authored staging. Preserve on-camera people, demonstrations, '
                'and reviewers doing work as motion. A citation alone does not make a line a document shot. '
                'Return {lines:[{i,fact,mark,motion}]} only for fitting exhibits. Copy an exact short substring '
                'from its quote for mark. Give each line a distinct passage or detail from the page, taking the '
                'already authored marks into account. For a mixed beat put the remaining specific visible action in motion. '
                'For a complete document beat use an empty motion string. Never invent evidence or a recording.',
                json.dumps(material), effort='low', max_tokens=1800, what='WF4 chapter passage placement')
            result = dict(C.wf4.story.writer.json_block(text), usage=usage)
            C.save(cache, result)
        except (RuntimeError, ValueError, OSError) as exc:
            ctx.log(f'using authored document placements after passage planning failed: {exc}')
            return choices
    wanted = {row['i'] for row in undecided}
    for row in result.get('lines', []):
        e = facts.get(row.get('fact'))
        if row.get('i') in wanted and e and row.get('mark') and row['mark'] in e['quote']:
            choices[row['i']] = row
    return choices


def supply_clip(ctx, exhibit, mark):
    import pypdfium2 as pdfium
    root = Path(ctx.run_dir)/'idea-originals'
    e = exhibit
    mark = mark if mark and mark in e['quote'] else e['quote']
    url = e['url'].split('#')[0]
    path = acquire(root, e['url'], 50_000_000)
    is_pdf = e['kind'] == 'pdf'
    date = e.get('date') or ''
    label_date = ('Source index ' + date) if date and e.get('parent') else date
    if is_pdf and not date:
        with pdfium.PdfDocument(path) as doc:
            stamp = doc.get_metadata_dict().get('CreationDate', '')
        try:
            date = datetime.strptime(stamp.removeprefix('D:')[:8], '%Y%m%d').date().isoformat()
            label_date = 'PDF created ' + date
        except ValueError:
            pass
    source_url = url + (f"#page={e['page']}&search=" if is_pdf else '#:~:text=') + escape(mark, safe='')
    asset = root/(key(json.dumps([e, mark], sort_keys=True))+'-highlight.png')
    if not asset.exists():
        with TemporaryDirectory(dir=root) as tmp:
            out = Path(tmp)/'marked.png'
            if is_pdf:
                cited_page.capture(dict(e, url=source_url, mark=mark), out, original_pdf=path)
            else:
                pages, _, _ = read_source(path, e['url'])
                if not any(e['quote'] in ' '.join(page.split()) for page in pages):
                    raise ValueError('quoted passage absent from the retained original')
                # Keep the qualification and figures around a marked phrase readable.
                excerpt = e['quote']
                if len(excerpt.split()) > 55:
                    before, _, after = excerpt.partition(mark)
                    left = before.rsplit(None,14)
                    right = after.split(None,18)
                    start = len(left[0]) if len(left)>14 else 0
                    end = len(excerpt)-len(right[-1]) if len(right)>18 else len(excerpt)
                    snippet = excerpt[start:end].strip()
                    excerpt = ('…' if start else '') + snippet + ('…' if end<len(excerpt) else '')
                design.document(excerpt, e['title'], url, mark=mark, date=label_date, out=str(out))
            out.replace(asset)
    with Image.open(asset) as image:
        w,h = image.size
    import cinema as C
    source = dict(url=source_url, title=e['title'], credit=e['author'], date=date, parent=e.get('parent'),
                  presentation='Marked original PDF' if is_pdf else 'Attributed quotation card',
                  primary_context=[dict(source_url=source_url, quote=e['quote'], page=e['page']-1 if is_pdf else None)])
    label = ' · '.join(filter(None,[urlsplit(url).hostname,label_date]))
    return dict(path=str(asset), kind='image', document=True, primary_passage=True, mark=mark,
                source=source, shows=e['quote'], w=w, h=h, seconds=7.8, time_range=[0,7.8],
                content_hash=C.digest(asset), editorial_label=label, source_label=label)


def place(ctx, script, segments, dest, previous):
    if not script.get('idea_full', {}).get('supply', {}).get('exhibits'):
        return {}, {}
    facts = passages(ctx, script, segments.values())
    choices = select(ctx, dest, segments, facts, previous)
    clips, motion = {}, {}
    for i,row in choices.items():
        try:
            clips[i] = dict(supply_clip(ctx, facts[row['fact']], row.get('mark')), fact=row['fact'])
        except Exception as exc:
            ctx.log(f'line {i}: original passage unavailable, continuing archive search: {exc}')
            continue
        if row.get('motion'):
            motion[i] = row['motion']
    return clips, motion
