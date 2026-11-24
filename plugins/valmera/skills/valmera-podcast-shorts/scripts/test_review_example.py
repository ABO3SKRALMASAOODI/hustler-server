import wf1_short  # Load the workflow paths used by the compositor.
import wf1_display as display


def test_two_verbatim_estimates_keep_their_first_word_and_complete_group():
    words = [dict(w=t,seg=0,t0=i*.25,t1=(i+1)*.25) for i,t in enumerate(
        "Now they might be running a half dozen agents, a dozen agents.".split())]
    source = dict(url="interview",publisher="Show")
    ask = dict(dev="facts",at=dict(w=0),until=dict(w=11),args=dict(
        claim=dict(speaker="Jason Calacanis",stance="personal_estimate"),
        sequence_kind="comparison", sequence=[dict(at=7,label="a half dozen agents"),dict(at=10,label="a dozen agents")]))
    direction = display.bind(dict(asks=[ask],arc=[],caption_ends=[4,6,8,9,11]),
                             dict(sources=dict(interview=source)),words)
    page, = direction["display_facts"]
    assert [step["at"] for step in page["sequence"]] == [5,9]
    assert [step["last"] for step in page["sequence"]] == [8,11]
    assert page["facts"][0]["attribution"] == "Jason Calacanis"
    assert direction["caption_ends"] == [4,8,11]


def test_retained_product_quote_reopens_only_its_own_publisher(tmp_path, monkeypatch):
    import wf1_review_frame as frame
    seen = []
    def select(pack, window, source, *args):
        seen.append(source["url"])
        return True
    monkeypatch.setattr(frame, "selected_frame", select)
    page = "https://publisher.example/release"
    window = dict(name="App", single_action=True, reuse_excerpt=dict(path="approved.png"))
    pack = dict(coverage=dict(windows=[window],named=[{"is":"product","name":"App"}]),
                pictures={"App":[dict(path="approved.png",page=page,document=dict(words="prior quote"))]},
                referent_research=dict(references=[dict(windows=[0],sources=[
                    dict(url="https://another.example/post",publication_date="2026-10-13"),
                    dict(url=page,publication_date="2026-10-12")])]))
    assert frame.add_reviews(pack,tmp_path,None,retained_only=True) == 1
    assert seen == [page]
    window["example"] = dict(review=dict(first=12))
    assert frame.add_reviews(pack,tmp_path,None,retained_only=True) == 0
