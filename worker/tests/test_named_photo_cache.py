from PIL import Image
import broll_source as source


def test_exact_named_photo_checks_reuse_bytes_brief_and_credit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_INTERNAL_RUN", "test-named-photo-cache")
    hits = [dict(id=x, title=x, description="exhibit", credit=x, page_url=x, _thumb=x) for x in ("one", "two")]
    colors = {"one":"black", "two":"red"}
    def thumb(url, path, **kw):
        Image.new("RGB",(20,20),colors[url]).save(path)
    seen = []
    def ask(system, content, *a, **kw):
        import json
        ids = [int(part["text"].split()[1][:-1]) for part in content[1:] if part["type"]=="text"]
        seen.append(ids)
        return json.dumps({"fit":[dict(n=i,ok=True,grade=4,shows="server") for i in ids]}), .1
    monkeypatch.setattr(source.net_fetch,"download",thumb)
    monkeypatch.setattr(source,"_ask",ask)
    subject = dict(name="Servers",must_show="Early web hardware")
    for change in ("cold","warm","pixels","credit","brief"):
        if change=="pixels": colors["two"]="blue"
        if change=="credit": hits[0]["credit"]="corrected"
        if change=="brief": subject["must_show"]="Visible log entries"
        got, _, cost = source.check_named(subject,hits,tmp_path)
        assert len(got)==2
        if change=="warm": assert cost==0
    assert seen == [[0,1],[1],[0],[0,1]]
