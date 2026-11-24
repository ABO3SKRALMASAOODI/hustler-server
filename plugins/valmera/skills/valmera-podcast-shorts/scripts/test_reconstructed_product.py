import hashlib
import json

from PIL import Image

import wf1_context
import wf1_documents


def test_linked_figure_reaches_an_unillustrated_spoken_product(tmp_path):
    # Saved m48 inventory included YouTube at word 47. Only the connection
    # windows had placements, so the existing figure shelf never saw this page.
    words = [dict(w=str(i), start=i/4, end=(i+1)/4) for i in range(53)]
    words[47]["w"] = "YouTube"
    prior = [dict(first=30,last=32), dict(first=33,last=40)]
    entity = dict(name="YouTube", word_ids=[47], must_show="Early watch page", **{"is":"product"})
    page, asset = "https://blog.youtube/news-and-events/va-museum-youtube-first-video/", "https://example.org/watch.png"
    credit = "V&A / YouTube · December 2006 interface"
    reference = dict(windows=[], figure_cue=dict(first=44,last=48,entity_word=47,source=page,credit=credit),
                     sources=[dict(url=page,figure_url=asset,figure_title="Me at the zoo",publisher="YouTube")])
    pack = dict(coverage=dict(words=words,entities=[entity],windows=list(prior)), pictures={},
                referent_research=dict(references=[reference]))
    image = tmp_path / "watch.png"
    Image.new("RGB",(16,12)).save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    manifest = tmp_path / "document-sources" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps([dict(url=asset,path=str(image),sha256=digest)]))
    pack["coverage"].update(named=[entity],speaker_claims=[dict(first=41,last=52,stance="characterization")])
    assert wf1_context.unplaced_products(pack["coverage"]) == [entity]
    assert wf1_documents.linked_examples(pack,tmp_path) == 1
    assert wf1_documents.linked_examples(pack,tmp_path) == 0
    *kept, added = pack["coverage"]["windows"]
    assert kept == prior and reference["windows"] == [2]
    assert (added["first"],added["last"]) == (44,48)
    assert added["example"] == dict(source=page,figure_url=asset,statement="Me at the zoo",credit=credit)
    assert added["reuse_excerpt"]["sha256"] == digest
    assert wf1_context.unplaced_products(pack["coverage"]) == []
