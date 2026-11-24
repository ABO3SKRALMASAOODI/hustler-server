from types import SimpleNamespace
import film_long
import cited_page


def test_failed_capture_keeps_the_same_checked_selection_on_the_plate(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(film_long.F, "_engines", lambda: None)
    def capture(fact, path):
        calls.append((fact["quote"],fact.get("author")))
        raise RuntimeError("offline")
    monkeypatch.setattr(cited_page,"capture",capture)
    def quote(text, who, path, **kw):
        calls.append((text,who))
        return dict(path=path)
    monkeypatch.setattr(film_long.design,"quote",quote)
    dos=dict(facts=[dict(id="r",url="release",quote="Employees could sell shares.",author="Issuer",date="2024")])
    sc=dict(segments=[dict(design=dict(kind="quote",fact="r",who="Host",text="The host expects a listing."))])
    result=film_long._designs(SimpleNamespace(log=lambda _:None),sc,dos,None,str(tmp_path))
    assert calls == [("Employees could sell shares.","Issuer")]*2
    assert result["0"]["source_url"]=="release"
