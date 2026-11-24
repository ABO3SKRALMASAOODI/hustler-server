import wf1_coverage as coverage
import wf1_display as display


def test_captioned_figure_is_first_on_its_own_clause(tmp_path):
    page = "https://docs.example/tutorial"
    image = "https://docs.example/published.png"
    def record(name, url):
        p = tmp_path / name
        p.write_bytes(name.encode())
        return dict(path=str(p), page=page, document=dict(url=url, sha256=name, page=1, box=[0,0,1,1]))
    other, figure = record("settings", page+"/settings.png"), record("dashboard", image)
    window = dict(t0=4.2, t1=5.7, name="Product", must_show="Published dashboard",
                  single_action=True, example=dict(source=page, figure_url=image))
    rows = coverage.proposals(dict(windows=[window]), {}, {"Product":[other,figure]})
    assert [(a,b,r["path"]) for a,b,_,_,r in rows] == [(129,174,figure["path"])]


def test_framework_hands_the_reading_seat_to_its_actions():
    setup = dict(first=54,last=91,facts=[dict(chain=dict(stance="framework",speaker="Guest"))])
    action = dict(first=71,last=91,sequence_kind="process",facts=[dict(attribution="Guest")])
    pages = display.episode_pages([setup,action],{},[])
    assert [(p["first"],p["last"]) for p in pages] == [(54,70),(71,91)]


def test_linked_examples_use_the_exact_local_crop_before_retrieval(tmp_path, monkeypatch):
    from hashlib import sha256
    import wf1_documents as documents
    url, page = "https://publisher.example/result.png", "https://publisher.example/tutorial"
    path = tmp_path / "verified.jpg"
    path.write_bytes(b"previously checked crop")
    old = dict(path=str(path), document=dict(url=url), page=page+"#result")
    window = dict(name="Outside client", example=dict(source=page,figure_url=url))
    pack = dict(coverage=dict(windows=[window]), pictures={"Earlier subject":[old]},
                referent_research=dict(references=[dict(windows=[0],sources=[dict(url=page,figure_url=url)])]))
    def unexpected_download(*args):
        raise AssertionError("new retrieval")
    monkeypatch.setattr(documents, "download", unexpected_download)
    assert documents.linked_examples(pack, tmp_path) == 1
    assert window["reuse_excerpt"] == dict(path=str(path),sha256=sha256(path.read_bytes()).hexdigest())
    assert documents.linked_examples(pack, tmp_path) == 0


def test_reference_follows_the_composed_image_and_publisher():
    fact = dict(source=dict(url="tutorial"),figure_url="published.png",
                statement="Taxi trip data feeds a dashboard.",visible_statement="These charts show taxi fares.")
    picture = dict(f=[132,170], document=dict(url="published.png"), source="tutorial#example")
    wrong = dict(picture, f=[129,174], source="another tutorial")
    assert list(display.figure_readings([129,174],fact,[wrong,picture])) == [
        ([129,132],fact["statement"]), ([132,170],fact["visible_statement"]), ([170,174],fact["statement"])]
