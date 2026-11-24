"""Read the maker's explanation beside an episode's own product demonstration."""
import hashlib
import json
import subprocess
from urllib.parse import urljoin,urlsplit
from pathlib import Path
from tempfile import TemporaryDirectory

from wf1_publisher_reading import key_of
from wf1_source_frame import write_cache


PROMPT = """Edit the explanation leading up to this original product demo. The supplied speech and publisher
pages are evidence. Return {"readings":[{"first":word index,"last":word index,"heading":short label,
"body":short plain sentence,"source":URL,"quotation":short literal supporting passage}]}.
At the FIRST word actually naming the mechanism, place one definition that makes a documented relationship
concrete. Use the named directory/source and who reports to whom when those facts are documented. Make the
heading the spoken term. Describe what the configured source CAN contribute, e.g. "A map of the company.
A connected directory can link each employee to their manager." Give its documented name. Do not imply that
every deployment uses that directory or that this speaker uses it. The compositor prints the publisher
and publication date, so avoid repeating either in the body. Let the setup play on the speaker. Around the list of connected applications,
give one dated, accurate product qualification that explains what enables this job: scoped permissions
and announced availability of autonomous or scheduled jobs. Use two brief lines/readings if needed.
Never generalize personal access to every app or file. Fit roughly 3 words per second; retain a complete
sentence long enough to read. The operator's firsthand claim and the visible result belong to the episode.
Do not repeat an explanation at the reveal or infer what an obscured item says. For the applications/access clause, choose one moving figure from the supplied publisher pages if it
shows the relevant connected-source setup or work process. Return figure_url on that reading. It is
a publisher example, never the interviewee's inbox. Use the page containing the figure as source.
Keep the original camera on the definition and gesture. Return only supported copy."""


def publication(url, folder):
    from lxml import html
    from wf1_documents import download
    from writer import _both
    data, resolved = download(url, str(folder))
    doc = html.fromstring(data)
    figures = []
    for node in doc.xpath("//img[@src]"):
        link = urljoin(resolved,node.get("src"))
        if urlsplit(link).path.lower().endswith(".gif"):
            figures.append(dict(url=link, alt=node.get("alt"), context=node.xpath("string(preceding::p[1])")[-540:]))
    return dict(sha256=hashlib.sha256(data).hexdigest(), text=_both(data.decode("utf-8","replace"))[1], figures=figures)


def moving_figure(url, folder, ffmpeg):
    from PIL import Image
    from wf1_documents import download
    src = folder / (key_of(url)+".gif")
    if not src.exists():
        data, _ = download(url,str(folder))
        write_cache(src,data)
    with Image.open(src) as im:
        if not getattr(im,"is_animated",False):
            return None
        width,height = im.size
        seconds = 0
        for i in range(im.n_frames):
            im.seek(i)
            seconds += im.info.get("duration",100)/1000
    target = src.with_suffix(".mp4")
    if not target.exists():
        with TemporaryDirectory(dir=folder) as tmpdir:
            tmp = Path(tmpdir)/"figure.mp4"
            subprocess.run([ffmpeg,"-v","error","-y","-ignore_loop","1","-i",str(src),"-an","-vf",
                        "fps=30,pad=ceil(iw/2)*2:ceil(ih/2)*2","-c:v","libx264","-crf","16",
                        "-preset","veryfast","-pix_fmt","yuv420p",str(tmp)],check=True,timeout=150)
            tmp.replace(target)
    return dict(path=str(target),w=width,h=height,seconds=seconds,fps=30,figure_url=url)


def read(demo, words, references, pages, work, llm, ffmpeg):
    from writer import has_words
    import httpx
    import openai_budget
    from net_fetch import FetchError

    sources = {}
    for row in [*references, *(f for p in pages for f in p["facts"])]:
        for source in row.get("sources", []) + ([row["source"]] if row.get("source") else []):
            if isinstance(source, dict) and source.get("publisher") and source.get("url") and source.get("publication_date"):
                sources[source["url"]] = source
    if not sources:
        return [], []
    folder = Path(work, "demonstration")
    folder.mkdir(exist_ok=True)
    originals = {}
    for url, source in sources.items():
        path = folder / (key_of(url)+".json")
        if not path.exists():
            try:
                record = publication(url, folder)
            except (FetchError, ValueError, RuntimeError, OSError):
                continue
            write_cache(path,json.dumps(record).encode())
        originals[url] = json.loads(path.read_text())
    if not originals:
        return [], []
    request = dict(demo=demo, words=[[i,w["w"],w["start"]] for i,w in enumerate(words)],
                   publications={u:dict(text=p["text"][:45000],date=sources[u]["publication_date"],figures=p["figures"]) for u,p in originals.items()})
    cache = folder / (key_of([PROMPT,request])+".json")
    if cache.exists():
        saved = json.loads(cache.read_text())
        for page in saved:
            figure = page.get("figure")
            if figure and not Path(figure["path"]).is_file():
                # Keep the verified quotation and its chosen publisher when a
                # copied run needs to restore only the figure's media bytes.
                try:
                    page["figure"] = moving_figure(figure["figure_url"],folder,ffmpeg)
                except (FetchError, ValueError, RuntimeError, OSError, subprocess.SubprocessError):
                    page["figure"] = None
        return saved, []
    try:
        result, use = llm.ask(PROMPT,json.dumps(request),effort="medium",max_tokens=1700)
        records = llm.json_block(result).get("readings") or []
    except openai_budget.OverBudget:
        raise
    except (httpx.HTTPError, ValueError, RuntimeError, OSError):
        return [], []
    selected = []
    for row in records:
        if not isinstance(row,dict):
            continue
        a,b,url = (row.get(k) for k in ("first","last","source"))
        term = demo.get("term_word")
        if type(a) is int and type(b) is int and type(term) is int and a <= term <= b:
            a = term
        if (type(a) is int and type(b) is int and demo["first"] <= a <= b < demo["cue"]
                and isinstance(url,str) and url in originals and all(isinstance(row.get(k),str) and row[k].strip()
                for k in ("heading","body","quotation")) and has_words(originals[url]["text"],row["quotation"])):
            figure = None
            if row.get("figure_url") in [f["url"] for f in originals[url]["figures"]]:
                try:
                    figure = moving_figure(row["figure_url"],folder,ffmpeg)
                except (FetchError, ValueError, RuntimeError, OSError, subprocess.SubprocessError):
                    pass
            selected.append(dict(first=a,last=b,demo_note=row["heading"], figure=figure, facts=[dict(
                value=row["body"], source=sources[url], quotation=row["quotation"],sha256=originals[url]["sha256"])]))
    if selected:
        write_cache(cache,json.dumps(selected).encode())
    return selected, [dict(use,stage="score",what="demonstration publisher scope")]
