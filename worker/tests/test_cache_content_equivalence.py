"""Execute pre-fix builders from a pinned Git snapshot and current builders.

Compare exact nonempty text lines (multiplicity), image bytes and tool values.
Whitespace/message boundaries/cache metadata may differ. This proves content
preservation, not identical stochastic answers or instruction-role semantics.
Named thumbnails retain identical pixels; their explicit low detail is separately
asserted and reported as transport metadata, not claimed to be identical output.
"""
import ast
from collections import Counter
import copy
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace as NS
import pytest

ROOT = Path(__file__).resolve().parents[2]
OLD = json.loads((Path(__file__).parent/'fixtures/cache_before.json').read_text())['functions']


def functions(key, namespace):
    item = OLD[key]
    src = (ROOT/item['path']).read_text()
    node = next(n for n in ast.parse(src).body if isinstance(n,ast.FunctionDef) and n.name == item['name'])
    current = '\n'.join(src.splitlines()[node.lineno-1:node.end_lineno])
    result=[]
    for text in (item['source'], current):
        ns = dict(namespace)
        exec(compile(text,item['path'],'exec'), ns)
        result.append(ns[item['name']])
    return result


def content(system, parts):
    parts = [{'type':'text','text':parts}] if isinstance(parts,str) else parts
    texts=[system]+[x.get('text','') for x in parts if x['type']=='text']
    images=[x['source'] for x in parts if x['type']=='image']
    return Counter(line.strip() for line in '\n'.join(texts).splitlines() if line.strip()), images


def test_footage_batches_contain_identical_words_and_image_bytes(tmp_path,monkeypatch):
    from footage import look
    captures=[]
    monkeypatch.setattr(look,'sheet',lambda cells,path,**kw:path)
    monkeypatch.setattr(look.ask,'image_block',lambda p:{'type':'image','source':{'type':'base64','data':Path(p).name}})
    monkeypatch.setattr(look.ask,'ask',lambda s,c,*a,**k: (captures.append((s,copy.deepcopy(c))) or ('{"cells":[]}',0)))
    monkeypatch.setattr(look,'PER_SHEET',2)
    # Compare the same transport unit as the original one-sheet reader.
    monkeypatch.setattr(look,'SHEETS_PER_CALL',1)
    args=(dict(name='Ada',must_show='Ada at work'),[dict(i=0,say='A real line.')],
          [dict(src=0,t=float(i),jpg=str(i),said='heard words') for i in range(5)],
          [dict(title='Source title',channel='source channel')],str(tmp_path))
    before,after=functions('look',vars(look))
    def by_sheet(calls):
        return sorted((content(*call) for call in calls), key=lambda row: row[1][0]['data'])

    before(*args)
    old = by_sheet(captures)
    captures.clear()
    after(*args)
    new = by_sheet(captures)
    for (old_words, old_images), (new_words, new_images), cells in zip(old, new, (2, 2, 1), strict=True):
        assert old_images == new_images
        assert new_words == old_words + Counter({f'Sheet 1: cells 1 to {cells}': 1})


def test_named_check_preserves_pixels_labels_and_rubric(tmp_path,monkeypatch):
    from PIL import Image
    import broll_source as named
    captures=[]
    def download(url,path,**kw): Image.new('RGB',(40,60),(42,86,121)).save(path)
    monkeypatch.setattr(named.net_fetch,'download',download)
    monkeypatch.setattr(named,'_ask',lambda s,c,*a,**k: (captures.append((s,copy.deepcopy(c))) or ('{}',0)))
    for fn in functions('named',vars(named)):
        fn(dict(name='Ada',must_show='the same person'),[{'id':'a','_thumb':'synthetic','title':'Portrait'}],workdir=str(tmp_path))
    assert content(*captures[0]) == content(*captures[1])
    assert [x.get('detail') for x in captures[1][1] if x['type']=='image']==['low']


def test_critic_rubric_and_variable_evidence_are_preserved(monkeypatch):
    import preview_critic as critic
    captures=[]
    def ask(prompt,paths,**kw):
        captures.append((kw.get('system','')+'\n'+prompt,paths,kw['image_names']))
        return '{"verdict":"pass","findings":[]}'
    monkeypatch.setattr(critic.llm,'vision_available',lambda:True)
    monkeypatch.setattr(critic.llm,'ask_vision',ask)
    for fn in functions('critic',vars(critic)):
        fn(['first.jpg','last.jpg'],['opening','ending'],'A synthetic project; preserve punctuation!')
    assert Counter(x.strip() for x in captures[0][0].splitlines() if x.strip()) == Counter(x.strip() for x in captures[1][0].splitlines() if x.strip())
    assert captures[0][1:] == captures[1][1:]


@pytest.mark.parametrize('pending,empty,credit',[(True,False,False),(False,False,True),(False,True,False),(False,False,False)])
def test_greeting_all_branches_are_reorders(pending,empty,credit):
    captures=[]
    def ask(system,user,**kw): captures.append((system,user)); return None
    ns=dict(config=NS(OPENAI_API_KEY='synthetic',AGENT_MODEL='gpt-6-sol'),
            _greet_lane=lambda:None,llm=NS(ask_text=ask,last_error=lambda:None),
            dbx=NS(insert_llm_call=None))
    for fn in functions('greeting',ns):
        fn(NS(run=lambda *a:None),1,'duration 12 s',pending,credit,{'words':[] if empty else [{'w':'Hello, world!'}]})
    assert captures[0][0]==captures[1][0]
    assert Counter(captures[0][1].splitlines())==Counter(captures[1][1].splitlines())


@pytest.mark.parametrize('stage,act',[('no_video',False),('no_video',True),('indexing',False),('index_failed',False)])
def test_concierge_reply_rules_facts_and_history_are_preserved(stage,act):
    captures=[]
    def answer(messages,want_act):
        captures.append(copy.deepcopy(messages));return 'Ready.',{}
    # Execute just the prompt function, with deterministic provider gates.
    ns=dict(_concierge_providers=lambda:[answer],_parse_act=lambda x:(x,False),
            _CONCIERGE_CLAIM=re.compile('NEVER_MATCH'),CONCIERGE_MODEL='gpt-6-sol')
    for name in ('_image_gen_enabled','_video_gen_enabled','_sfx_search_enabled','_web_record_enabled','_url_fetch_enabled'):
        ns[name]=lambda:False
    # Names referenced by this function's provider capability list.
    class Defaults(dict):
        def __missing__(self,key): raise KeyError(key)
    for fn in functions('concierge',ns):
        fn(stage,[{'role':'user','content':'A sample question.'}],[],index_error='decode failed',can_act=act)
    assert captures[0][1:]==captures[1][1:]
    assert Counter(x for x in captures[0][0]['content'].splitlines() if x)==Counter(x for x in captures[1][0]['content'].splitlines() if x)


def test_fit_lines_candidates_images_and_rubric_are_preserved(tmp_path):
    import sys
    sys.path.insert(0,str(ROOT/'plugins/valmera/skills/valmera-stories'))
    import fit
    captures=[]
    def ask(s,c): captures.append((s,copy.deepcopy(c)));return '{"verdicts":[]}',{'usd':0}
    ns=dict(vars(fit),_img=lambda p,d:{'type':'image','source':{'data':'identical-image'}})
    for fn in functions('fit',ns):
        fn([{'path':'a.jpg','kind':'photo','frames':['a.jpg'],'lines':[0],'meta':{}}],
           {0:{'i':0,'say':'The same line.','see':[]}},ask=ask,work=str(tmp_path))
    assert content(*captures[0])==content(*captures[1])


def test_cache_wrapper_preserves_all_tools_and_request_content():
    import openai_cache
    original={'model':'gpt-6-sol','instructions':'Fixed instructions!','input':[{'role':'user','content':[
        {'type':'input_text','text':'Project facts.\nA transcript.'},
        {'type':'input_image','image_url':'data:image/png;base64,AAAA','detail':'low'}]}],
        'max_output_tokens':1000,'tools':[{'type':'function','name':'z','parameters':{'required':['b','a']}},
                                         {'type':'function','name':'a','parameters':{}}]}
    result=openai_cache.responses(original,'role')
    assert result['input'][0]['content'][0]['text']==original['instructions']
    assert result['input'][1:]==original['input']
    assert sorted(result['tools'],key=lambda x:x['name'])==sorted(original['tools'],key=lambda x:x['name'])
    assert result['max_output_tokens']==original['max_output_tokens']


@pytest.mark.parametrize('key',['shell_parts','writer_parts','director_parts'])
def test_provider_converters_preserve_exact_text_images_and_order(key):
    before,after=functions(key,{'_text':lambda b:b.get('text','') if isinstance(b,dict) else str(b)})
    parts=[{'type':'text','text':'First line.\nSecond line!', 'cache_control':{'type':'ephemeral'}},
           {'type':'image','source':{'type':'base64','media_type':'image/png','data':'EXACT-BYTES'}},
           {'type':'text','text':'The final request.'}]
    old,new=before(parts),after(parts)
    assert old == [{k:v for k,v in p.items() if k!='prompt_cache_breakpoint'} for p in new]
    assert new[0]['prompt_cache_breakpoint']['ttl']=='30m'
    assert parts[0]['cache_control']=={'type':'ephemeral'}
