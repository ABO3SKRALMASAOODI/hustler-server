from hashlib import sha256
from PIL import Image

from wf1_documents import linked_examples


def test_publisher_cover_can_supply_an_existing_spoken_window(tmp_path):
    import json
    title = "One writer's book"
    page = "https://publisher.example/book"
    url = "https://publisher.example/cover.jpg"
    source = dict(title=title, author="A. Writer", publisher="Example Press",
                  publication_date="2014", url=page, figure_url=url, figure_title=title)
    folder = tmp_path / "document-sources"
    folder.mkdir()
    path = folder / "cover.jpg"
    Image.new("RGB", (120, 180), "navy").save(path)
    digest = sha256(path.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps([dict(url=url,path=str(path),sha256=digest)]))
    window = dict(first=3,last=12,name=title,medium="publisher_cover")
    pack = dict(coverage=dict(windows=[window]), pictures={}, credits=[],
                referent_research=dict(references=[dict(windows=[0],sources=[source])]))
    assert linked_examples(pack, tmp_path) == 1
    assert window["reuse_excerpt"]["sha256"] == digest
    rec, = pack["pictures"][title]
    assert rec["page"] == page and rec["document"]["url"] == url
    assert rec["publication"]["author"] == "A. Writer"
    assert not [w for w in pack["coverage"]["windows"] if not w.get("reuse_excerpt")]
