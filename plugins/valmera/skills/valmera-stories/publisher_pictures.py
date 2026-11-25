"""Resolve original photographs from the documentary's cited sources after a catalogue miss."""
import hashlib
import json
import tempfile
from pathlib import Path


def lookup(briefs, writer):
    text, usage = writer.ask_web(
        'Find photographs for documentary editors at the cited publishers and their own press or archival collections. '
        'Read the source page. Return original image URLs actually linked there, not invented URLs or web thumbnails. '
        'Prefer the exact product, date and version. Distinguish a photograph date from an object date. '
        'Look for an explicit asset licence permitting editorial reporting, or a freely licensed original. '
        'If the publisher permits research only or requires permission, retain its image for identification with its '
        'actual rights, then also find a freely licensed photograph of the identical object on Wikimedia Commons. '
        'Return JSON {"images":[{"name":"requested subject","page":"original caption page URL","url":"direct image URL",'
        '"title":"original title","credit":"photographer or publisher","caption":"what the source says",'
        '"image_date":"photograph date, if known","publication_date":"date of source",'
        '"licence":"specific licence or rights statement","licence_url":"source of rights","commons_file":"File title if on Commons"}]}. '
        'At most three different source photographs per subject. Do not apply a site-wide text licence to a photograph.',
        json.dumps(briefs, ensure_ascii=False), searches=6, fetches=6, max_tool_calls=6,
        effort='medium', max_tokens=3500, what='Stories archive photographs')
    return writer.json_block(text).get('images') or [], usage


def identifier(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def download(url, destination, cap):
    """The publisher bytes, directly or from a preserved copy of the same URL."""
    import datetime
    import net_fetch
    from urllib.parse import urlsplit
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('expected a web source')
    try:
        net_fetch.download(url, str(destination), max_bytes=cap, timeout_s=15)
    except net_fetch.PolicyError:
        raise
    except net_fetch.FetchError:
        archive = f'https://web.archive.org/web/{datetime.date.today().year}id_/{url}'
        net_fetch.download(archive, str(destination), max_bytes=cap, timeout_s=30)


def original(row, folder, pictures):
    """Verify the exact image on its source page. The Commons API supplies its own rights."""
    from lxml import html
    from PIL import Image
    from urllib.parse import urljoin
    folder.mkdir(exist_ok=True)
    if row.get('commons_file'):
        rec = pictures.commons_file(row['commons_file'], str(folder), want_w=0,
                                    download_original=lambda u, p: download(u, p, 32 << 20))
        return rec, True
    page = folder/'source.html'
    download(row['page'], page, 4 << 20)
    document = html.parse(str(page)).getroot()
    links = {urljoin(row['page'], link) for _, _, link, _ in document.iterlinks()}
    if row['url'] not in links:
        raise ValueError('image not linked from the caption page')
    image = folder/'original.jpg'
    download(row['url'], image, 32 << 20)
    with Image.open(image) as im:
        width, height = im.size
        xmp = im.info.get('xmp') or im.info.get('XML:com.adobe.xmp')
        licence_url = None
        if xmp:
            from lxml import etree
            packet = etree.fromstring(xmp.encode() if isinstance(xmp, str) else xmp,
                                      parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=True))
            if packet is not None:
                licence_url = packet.xpath('string(//cc:license/@rdf:resource)', namespaces={
                    'cc': 'http://creativecommons.org/ns#', 'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'})
        lic = pictures._worker().canon_licence('', licence_url) if licence_url else None
        im.verify()
    return dict(path=str(image), w=width, h=height, provider='publisher', page=row['page'], url=row['url'],
                title=row['title'], credit=row['credit'], description=row['caption'], image_date=row.get('image_date'),
                publication_date=row.get('publication_date'), licence=lic[0] if lic else row.get('licence'),
                licence_url=lic[1] if lic else row.get('licence_url')), bool(lic)


def saved_original(item, folder, pictures):
    receipt = folder / "source.json"
    def digest(path):
        with open(path, "rb") as media:
            return hashlib.file_digest(media, "sha256").hexdigest()
    try:
        record = json.loads(receipt.read_text())
        media = folder / record["file"]
        if record["request"] == identifier(item) and digest(media) == record["sha256"]:
            return dict(record["photo"], path=str(media)), record["usable"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    photo, usable = original(item, folder, pictures)
    with tempfile.NamedTemporaryFile(mode="w", dir=folder, delete=False) as f:
        json.dump(dict(request=identifier(item), file=Path(photo["path"]).name, photo=photo,
                       sha256=digest(photo["path"]), usable=usable), f)
    Path(f.name).replace(receipt)
    return photo, usable


def supply(script, subjects, got, out, per):
    """One research call for the outstanding briefs. Resume the results on the next supply pass."""
    import writer
    import pictures
    facts = {f['id']: f for f in script.get('facts') or [] if f.get('url') and f.get('id')}
    briefs = []
    for s in subjects:
        i = s.get('search_for')
        if got.get(s['name']) or not isinstance(i, int):
            continue
        sources = [facts[k] for k in script['segments'][i].get('facts', []) if k in facts]
        if sources:
            briefs.append(dict(name=s['name'], must_show=s['must_show'], sources=sources))
    if not briefs:
        return [], []
    B = pictures._worker()
    import openai_budget
    root = Path(out)/'publisher'
    root.mkdir(parents=True, exist_ok=True)
    plan = root/(identifier(briefs)+'.json')
    uses, reports = [], []
    try:
        images = json.loads(plan.read_text())
    except (FileNotFoundError, ValueError):
        try:
            images, use = lookup(briefs, writer)
        except Exception as exc:
            openai_budget.propagate(exc)
            return [dict(subject=b['name'], must_show=b['must_show'], errors=[str(exc)[:180]]) for b in briefs], uses
        uses.append(dict(stage='supply', **use))
        with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as f:
            json.dump(images, f)
        Path(f.name).replace(plan)
    for name in dict.fromkeys(b['name'] for b in briefs):
        rows, eligible, local, errors = [], {}, {}, []
        for item in images if isinstance(images, list) else []:
            if not isinstance(item, dict) or item.get('name') != name or not item.get('url'):
                continue
            key = identifier(item['url'])
            try:
                rec, usable = saved_original(item, root/key, pictures)
            except Exception as exc:  # An unavailable original leaves the other sources in the search.
                openai_budget.propagate(exc)
                errors.append(dict(url=item['url'], error=str(exc)[:160]))
                continue
            local[key] = rec['path']
            if usable and max(rec['w'], rec['h']) >= 700 and min(rec['w'], rec['h']) >= 420:
                eligible[key] = rec
            rows.append(dict(id=key, title=rec['title'], description=rec['description'], credit=rec['credit'],
                             image_date=rec.get('image_date'), publication_date=rec.get('publication_date'),
                             page_url=rec['page'], license=rec['licence'], license_url=rec['licence_url'], _url=rec['url']))
        for b in (b for b in briefs if b['name'] == name):
            try:
                kept, checked, usd = B.check_named(b, rows, str(root), local_photos=local) if rows else ([], [], 0)
            except Exception as exc:
                openai_budget.propagate(exc)
                errors.append(dict(error=str(exc)[:180]))
                kept, checked, usd = [], [], 0
            results = got.setdefault(name, [])
            have = {r['url'] for r in results}
            for c in kept:
                rec = eligible.get(c['id'])
                if rec and rec['url'] not in have and len(results) < per:
                    results.append(dict(rec, name=name, shows=c['shows'], grade=c['_grade']))
                    have.add(rec['url'])
            reports.append(dict(subject=name, must_show=b['must_show'], sources=rows, checked=checked,
                                candidates=len(rows), kept=len(results), downloaded=len(results), errors=errors))
            if usd:
                uses.append(dict(stage='supply', what='publisher photograph check', usd=usd))
    return reports, uses
