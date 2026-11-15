"""Keep primary report prose attached to its figures, with exact quote checks.

A cropped figure caption can name a tool without explaining its use. Reviewers
need the original passage, not another model's ungrounded description of it.
"""
from pathlib import Path
from contextlib import closing
import hashlib,json


def enrich(ctx, clips):
    import cinema as C
    from wf4 import story
    import pypdfium2 as pdfium
    from evidence_web import download
    groups={}
    for c in clips:
        url=c['source']['url'].split('#')[0]
        if c.get('document') and url.endswith('.pdf'):
            groups.setdefault(url,[]).append(c)
    root=Path(ctx.supply)/'document-context';root.mkdir(exist_ok=True)
    for url,rows in groups.items():
        figures={c['source']['url']:c for c in rows}
        key=hashlib.sha256(json.dumps(sorted(figures)).encode()).hexdigest()[:16]
        proof=root/(key+'.json')
        if proof.exists():result=C.read(proof)
        else:
            pdf_path=root/(hashlib.sha256(url.encode()).hexdigest()[:16]+'.pdf')
            if not pdf_path.exists():pdf_path.write_bytes(download(url))
            pages=[]
            with pdfium.PdfDocument(pdf_path) as pdf:
                for i in range(len(pdf)):
                    with closing(pdf[i]) as page,closing(page.get_textpage()) as text:
                        pages.append(dict(page=i,text=text.get_text_range()))
            text,usage=story.writer.ask('Locate primary-report pages whose prose explains the exact figures listed below. '
                'A figure caption alone may omit what a tool was used for. Use this report only. '
                'Return JSON {figures:[{url,pages:[zero-based page indexes]}]}. At most two pages per figure. '
                'The program attaches their actual extracted text, so do not write or paraphrase quotations. '
                'Do not infer a role from a tool name or confuse components. '
                'Leave pages empty when the report does not establish the figure’s role.',
                json.dumps(dict(figures=[dict(url=u,caption=c.get('shows')) for u,c in figures.items()],pages=pages)),
                effort='low',max_tokens=5000,what='WF4 primary figure context')
            if usage.get('stop')!='completed':raise ValueError('primary document context response incomplete')
            result=story.writer.json_block(text);result['usage']=usage
            for row in result['figures']:
                if row['url'] not in figures:raise ValueError('unknown primary figure')
                row['passages']=[]
                for pi in row['pages']:
                    if not isinstance(pi,int) or not 0<=pi<len(pages):raise ValueError('invalid primary page')
                    row['passages'].append(dict(page=pi,quote=pages[pi]['text'],source_url=url+'#page='+str(pi+1)))
            C.save(proof,result)
        by={r['url']:r['passages'] for r in result['figures']}
        for c in rows:c['source']['primary_context']=by.get(c['source']['url'],[])
