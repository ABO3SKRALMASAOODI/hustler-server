"""A website demo asked for on a blank canvas runs the engine's URL First cut
for every account (owner's own test, 30 Oct 2026: project 5246's tile request
went to the agent, which recorded a plain scroll)."""

import agent_loop
import first_cut
import website_engine

TILE = ("Record my website https://valmera.io/ as a 20-second vertical (9:16) "
        "demo: scroll the page from top to bottom at a steady pace, then make "
        "it a polished showcase video.")
NEW_TILE = ("Record my website https://valmera.io/ as a 20-second vertical "
            "(9:16) demo: tour its sections like a product demo, then make it "
            "a polished showcase video.")


class _DB:
    """worker_db.run(fn, *args) by the helper's name."""

    def __init__(self, original=None, edl=None):
        self.original, self.edl = original, edl

    def run(self, fn, *args, **kw):
        name = getattr(fn, "__name__", "")
        if name == "latest_asset":
            return self.original
        if name == "latest_edl":
            return self.edl
        raise AssertionError(f"unexpected db call {name}")


def _job(**extra):
    return {"id": 7, "project_id": 5246, "user_id": 60,
            "payload": dict({"message_id": 155113}, **extra)}


def _msg(text, role="user", mid=155113):
    return {"id": mid, "role": role, "content": text}


def test_the_tile_and_typed_requests_name_their_site():
    for text in (TILE, NEW_TILE, "record a demo of https://valmera.io",
                 "make a 9:16 demo video of the pricing page https://cal.com/pricing",
                 "make a vertical showcase video of the portfolio https://brittanychiang.com",
                 "Create a premium 60-second cinematic promotional video for https://osai.ie.",
                 "Acesse https://phmath.vercel.app/index.html e produza um vídeo "
                 "promocional elegante",
                 "https://stoepandsite.co.za/",
                 "www.stripe.com",
                 "video of my website www.example.org please"):
        assert website_engine.request_url(text), text
    assert website_engine.request_url("www.stripe.com") == "https://www.stripe.com"
    assert website_engine.request_url(TILE) == "https://valmera.io/"


def test_footage_songs_files_and_other_asks_are_not_website_demos():
    for text in ("use this clip https://www.youtube.com/watch?v=hI9HQfCAw64",
                 "record a demo with https://youtu.be/abc",
                 "add this song https://soundcloud.com/a/b as music",
                 "make a demo from https://drive.google.com/file/d/x",
                 "record https://cdn.example.com/clip.mp4 into a demo",
                 "https://x.com/someone/status/1",
                 "make the captions bigger",
                 "summarise the news in https://example.com/article for a "
                 "voiceover about inflation and rent prices this year",
                 ""):
        assert website_engine.request_url(text) is None, text
    # a host that merely ends in "x.com" is a website (netflix.com, box.com's
    # look-alikes): the media list matches whole host labels
    assert website_engine.request_url("record a demo of https://netflix.com")


def test_route_only_on_a_blank_canvas_for_a_studio_request():
    blank = {"json": {"keep": [], "inserts": [], "overlays": [], "texts": []}}
    url = website_engine.route_url(_DB(None, blank), _job(), _msg(TILE))
    assert url == "https://valmera.io/"
    assert website_engine.route_url(_DB(None, None), _job(), _msg(TILE))
    # a main video, a clip on the timeline or a title: the agent's
    assert website_engine.route_url(_DB({"id": 1}, blank), _job(), _msg(TILE)) is None
    placed = {"json": {"keep": [], "inserts": [{"duration_s": 20.0}]}}
    assert website_engine.route_url(_DB(None, placed), _job(), _msg(TILE)) is None
    titled = {"json": {"keep": [], "inserts": [], "texts": [{"text": "Hi"}]}}
    assert website_engine.route_url(_DB(None, titled), _job(), _msg(TILE)) is None
    # never the trial's own lane, a continuation, a repair or a follow-up
    for key in ("trial_lane", "first_cut", "logical_turn_continuation",
                "operator_repair", "after_first_cut", "root_agent_job_id"):
        assert website_engine.route_url(
            _DB(None, blank), _job(**{key: True}), _msg(TILE)) is None, key
    # the request must be this job's own user message
    assert website_engine.route_url(
        _DB(None, blank), _job(), _msg(TILE, mid=9)) is None
    assert website_engine.route_url(
        _DB(None, blank), _job(), _msg(TILE, role="assistant")) is None
    assert website_engine.route_url(
        _DB(None, blank), _job(), _msg("make it punchier")) is None


def test_route_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(website_engine, "ON", False)
    assert website_engine.route_url(_DB(None, None), _job(), _msg(TILE)) is None


def test_the_engine_job_carries_the_url_and_keeps_the_message():
    job = website_engine.engine_job(_job(), "https://valmera.io/")
    assert job["payload"]["url"] == "https://valmera.io/"
    assert job["payload"]["message_id"] == 155113
    assert job["payload"]["website_engine"] is True
    assert "url" not in _job()["payload"]


def test_the_tile_template_is_a_section_tour_not_a_scroll():
    for text in (TILE, NEW_TILE):
        hints = first_cut.url_hints(text)
        assert "mode" not in hints and hints["seconds"] == 20
        tour = first_cut.tour_steps(hints)[1]
        assert tour["do"] == "tour" and "mode" not in tour and tour["by"] == 2
        assert not first_cut.is_brief(text)


def test_run_agent_job_hands_a_blank_canvas_website_request_to_the_first_cut(
        monkeypatch):
    seen = {}

    class DB:
        def run(self, fn, *args, **kw):
            name = getattr(fn, "__name__", "")
            if name == "get_project":
                return {"id": 5246, "chat_session_id": 5351}
            if name == "latest_asset":
                return None
            if name == "_get_msg":
                return _msg(TILE)
            raise AssertionError(f"unexpected db call {name}")

    monkeypatch.setattr(website_engine, "route_url",
                        lambda db, job, msg: "https://valmera.io/")

    def fake_run(db, job, project, index_row):
        seen.update(job=job, project=project, index_row=index_row)
        return {"status": "first_cut", "outcome": "fulfilled",
                "billable": False, "credits_charged": 0.0}
    monkeypatch.setattr(first_cut, "run", fake_run)
    out = agent_loop.run_agent_job(DB(), _job())
    assert out["status"] == "first_cut" and out["billable"] is False
    assert seen["job"]["payload"]["url"] == "https://valmera.io/"
    assert seen["job"]["payload"]["message_id"] == 155113
    assert seen["index_row"] is None and seen["project"]["id"] == 5246


def test_a_url_first_cut_runs_the_engine_for_every_account():
    """first_cut.run turns the engine on for a URL First cut whatever the
    account (a grandfathered payer's engine_on is False)."""
    src = open(first_cut.__file__, encoding="utf-8").read()
    at = src.index("ctx.first_cut_q1 = bool(worker_db.run(")
    tail = src[at:at + 900]
    assert "if url:" in tail and "ctx.first_cut_q1 = True" in tail
