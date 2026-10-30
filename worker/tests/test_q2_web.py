"""Q2 (30 Oct 2026): website demos, the #1 first request, cut as BEATS.

The tour records beats (the hero, each section's own heading, one thing
pointed at per stop, the page's closing call to action once at the end); the
First cut gives each beat one eased push-in that frames it whole and lets go
before the page moves on. What the judge marked on every website run, and
what these tests hold:
  - push-ins glided SIDEWAYS from one card to the next (showcase_demo's
    travelling zoom): one push per beat, released between beats;
  - the hook title sat over the site's own headline: the hero is blurred
    under it and sharp again by its end;
  - the endings sat in the footer / repeated the closing block: the closing
    call to action is kept for the end, framed, live;
  - "jumps back to NEW ARRIVALS three times": a stop points at what is on
    screen, never re-finds a repeated label by its text;
  - long briefs (60 s, named pages) were only partly met: the length sets
    the number of sections and the request's named pages open first.
"""
import agent_tools
import first_cut
import webrecord

# the linear.app tour as the recorder wrote it (local capture, 30 Oct)
LINEAR = [
    {"t": 3.77, "kind": "nav", "label": "/pricing"},
    {"t": 5.19, "x": 0.2582, "y": 0.2609, "box": [0.0639, 0.2234, 0.4524, 0.2984],
     "kind": "look", "label": "Pricing"},
    {"t": 6.94, "kind": "scroll"},
    {"t": 8.19, "x": 0.5, "y": 0.8146, "box": [0.0667, 0.7833, 0.9333, 0.8458],
     "kind": "hover", "label": "Get started"},
    {"t": 9.87, "kind": "nav", "label": "/customers"},
    {"t": 11.4, "x": 0.3775, "y": 0.2609, "box": [0.0639, 0.2234, 0.6911, 0.2984],
     "kind": "look", "label": "Customers"},
    {"t": 13.2, "kind": "scroll"},
    {"t": 14.4, "x": 0.25, "y": 0.5, "box": [0.03, 0.35, 0.49, 0.66],
     "kind": "hover", "label": "Ring"},
    {"t": 16.52, "kind": "scroll"},
    {"t": 18.82, "x": 0.2838, "y": 0.5209, "box": [0.1075, 0.4865, 0.46, 0.5552],
     "kind": "finale", "label": "Get started"},
]


def _visible(push):
    """The output window [x0, y0, x1, y1] (frame fractions) a push shows at
    full strength, as the renderer draws it (the pin holds its place)."""
    err, st, cx, cy, _rr = agent_tools._solve_zoom_rect(push["rect"],
                                                         push["strength"])
    assert not err
    z = 1.0 + st
    return ((1 - 1 / z) * cx, (1 - 1 / z) * cy,
            (1 - 1 / z) * cx + 1 / z, (1 - 1 / z) * cy + 1 / z)


def test_every_beat_gets_one_eased_push_that_frames_it_whole():
    pushes = first_cut.demo_pushes(LINEAR, 21.1)
    beats = [e for e in LINEAR if e["kind"] in ("look", "hover", "finale")]
    assert len(pushes) == len(beats)
    for push, beat in zip(pushes, beats):
        # never a path from one card to the next
        assert push["mode"] in ("ease", "punch") and "path" not in push
        x0, y0, x1, y1 = _visible(push)
        bx0, by0, bx1, by1 = beat["box"]
        # the words the push is about are never cut at the frame edge
        assert x0 <= bx0 + 1e-3 and bx1 <= x1 + 1e-3, (beat["label"], x0, x1)
        assert y0 <= by0 + 1e-3 and by1 <= y1 + 1e-3, beat["label"]
        assert first_cut.DEMO_PUSH_MIN <= push["strength"] <= \
            first_cut.DEMO_PUSH_MAX[beat["kind"]]


def test_pushes_let_go_before_the_page_moves_and_never_touch():
    pushes = first_cut.demo_pushes(LINEAR, 21.1)
    moves = [e["t"] for e in LINEAR if e["kind"] in ("scroll", "nav", "click")]
    for a, b in zip(pushes, pushes[1:]):
        assert b["start"] - a["end"] >= 0.3     # released in between
    for p in pushes:
        assert p["start"] >= first_cut.DEMO_TITLE_S - 0.15   # the title owns the opening
        if p["mode"] == "ease":
            # no scroll or navigation happens while a push holds
            assert not [m for m in moves if p["start"] < m < p["end"]]
            assert p["ramp_s"] <= first_cut.DEMO_PUSH_RAMP_S
    final = pushes[-1]
    # the finale eases in and holds to the end, never a slow drift
    assert final["mode"] == "punch" and final["end"] == 21.1
    assert final["ramp_s"] >= 0.4
    # the closing row is framed about the centre: both buttons stay in
    x0, _y0, x1, _y1 = _visible(final)
    assert x0 <= 0.1075 and x1 >= 1 - 0.1075


def test_a_full_width_block_gets_a_gentle_push_or_none():
    ev = [{"t": 4.0, "kind": "hover", "x": 0.5, "y": 0.5,
           "box": [0.01, 0.4, 0.99, 0.6]},
          {"t": 8.0, "kind": "scroll"},
          {"t": 9.0, "kind": "hover", "x": 0.5, "y": 0.5,
           "box": [0.0667, 0.78, 0.9333, 0.85]},
          {"t": 12.0, "kind": "scroll"}]
    pushes = first_cut.demo_pushes(ev, 20.0)
    assert len(pushes) == 1 and pushes[0]["strength"] <= 0.12
    x0, _y0, x1, _y1 = _visible(pushes[0])
    assert x0 <= 0.0667 and x1 >= 0.9333


def test_short_beats_and_the_title_span_get_no_push():
    ev = [{"t": 1.5, "kind": "hover", "x": 0.5, "y": 0.6, "box": [.3, .55, .7, .65]},
          {"t": 4.0, "kind": "look", "x": 0.3, "y": 0.3, "box": [.05, .25, .5, .32]},
          {"t": 4.6, "kind": "scroll"}]
    assert first_cut.demo_pushes(ev, 12.0) == []


def test_the_site_name_not_the_page_name_titles_the_demo():
    assert first_cut._brand({"page_title": "Pricing | Cal.com"},
                            "https://cal.com/pricing") == "Cal.com"
    assert first_cut._brand({"page_title": "Sparkle & Loop - Premium Artificial "
                                           "Jewellery Store"},
                            "https://www.sparklenloop.com/") == "Sparkle & Loop"
    assert first_cut._brand({"site_name": "Linear", "page_title": "x | y"},
                            "https://linear.app") == "Linear"
    assert first_cut._brand(None, "https://valmera.io/") == "Valmera"
    # a page the request points at names the page in the serif
    assert first_cut._site_hook({"page_title": "Pricing | Cal.com"},
                                "https://cal.com/pricing") == "Cal.com / *Pricing*"
    assert first_cut._site_hook({}, "https://phmath.vercel.app/index.html") == \
        "Meet / *Phmath*"


def test_the_length_asked_for_sets_how_many_sections_open():
    assert [first_cut.tour_sections(s) for s in (12, 20, 30, 40, 60)] == \
        [2, 2, 3, 4, 5]
    brief = ("Acesse https://phmath.vercel.app/index.html e produza um vídeo "
             "promocional em formato vertical 9:16, com aproximadamente 40 "
             "segundos. Explore as seções Início, Diagnóstico e Aprender.")
    tour = first_cut.tour_steps(first_cut.url_hints(brief))[1]
    assert tour["seconds"] == 40 and tour["by"] == 4
    assert "diagnóstico" in tour["prefer"].lower()


# ---- the recorder ---------------------------------------------------------

class _Page:
    def __init__(self, tour=None, url="https://acme.io/"):
        self.tour, self.url = tour or {}, url

    def evaluate(self, js, *args):
        return self.tour


def _session(page=None):
    s = webrecord._Session.__new__(webrecord._Session)
    s.page = page or _Page()
    s.W, s.H = 360, 640
    s.lost_s = 0.0
    s.events, s.problems = [], []
    return s


def test_the_closing_call_to_action_is_a_button_above_the_footer():
    s = _session()
    info = {"H": 640, "total": 6000, "bottom": 5360, "foot": None, "ctas": [
        {"t": "Get started", "top": 300, "h": 40, "btn": True},       # hero
        {"t": "Get started", "top": 4700, "h": 40, "btn": True},
        {"t": "Contact sales", "top": 4700, "h": 40, "btn": True},
        {"t": "Get a demo", "top": 5600, "h": 18, "btn": False}]}     # footer link
    c = s._closing(info)
    assert c["top"] == 4700 and c["t"] == "Get started"
    # the middle of the tour never shows the closing block or the footer
    info["foot"] = 5200
    lim = s._limit(info, c)
    assert lim + 640 < 4700 - 0.15 * 640
    assert lim <= 5200 - 0.8 * 640


def test_stops_only_go_down_the_page_and_stay_above_the_limit():
    s = _session()
    info = {"heads": [{"top": t} for t in (200, 900, 1000, 2400, 5000)]}
    stops = s._positions(info, 0.0, 3000.0)
    assert stops == sorted(stops) and all(0 < p <= 3001 for p in stops)
    assert all(b - a > 0.4 * 640 for a, b in zip(stops, stops[1:]))


def test_a_stop_points_at_what_is_on_screen_never_a_pinned_header():
    s = _session()
    items = [{"t": "Sign up", "y": 0.04, "x": .8, "area": .01, "button": True,
              "fixed": True},
             {"t": "Get started", "y": 0.5, "x": .3, "area": .01, "button": True},
             {"t": "Ring", "y": 0.45, "x": .3, "area": .2}]
    assert s._feature(items)["t"] == "Get started"
    assert s._feature(items, skip={"Get started"})["t"] == "Ring"


def test_the_pages_a_brief_names_open_first_in_its_order():
    start = "https://phmath.vercel.app/index.html"
    links = [{"t": "Início", "href": "https://phmath.vercel.app/index.html"},
             {"t": "Pricing", "href": "https://phmath.vercel.app/pricing"},
             {"t": "Aprender", "href": "https://phmath.vercel.app/aprender.html"},
             {"t": "Diagnóstico", "href": "https://phmath.vercel.app/diag.html"},
             {"t": "Entrar", "href": "https://phmath.vercel.app/login"},
             {"t": "Get started", "href": "https://phmath.vercel.app/start"},
             {"t": "Blog", "href": "https://other.site/blog"}]
    s = _session(_Page({"links": links}, start))
    got = s._candidates([], "Explore as seções Início, Diagnóstico e Aprender",
                        start)
    assert [c["t"] for c in got] == ["Diagnóstico", "Aprender", "Pricing"]
    # a hidden phone-layout link is opened by its address, not a menu
    assert all(c["item"] is None for c in got)


def test_only_the_tour_films_the_narrower_phone(monkeypatch):
    """D1: a capture scripted step by step (any lane, any subscriber) keeps
    its viewport; only the tour (the First cut / trial recipe) films the
    360-wide phone layout."""
    seen = []

    def fake(url, workdir, orientation, wall_s, cursor, body, css=None):
        seen.append(css)
        return {}
    monkeypatch.setattr(webrecord, "_record", fake)
    webrecord.record_demo("https://acme.io", "/tmp", [{"do": "scroll"}],
                          orientation="portrait")
    webrecord.record_demo("https://acme.io", "/tmp", [{"do": "tour", "by": 2}],
                          orientation="portrait")
    # QI (D1): a scripted capture outside the engine films main's viewport
    assert seen == [webrecord._DESKTOP_VIEWPORTS, webrecord._TOUR_VIEWPORTS]
    w, h, dpr = webrecord._TOUR_VIEWPORTS["portrait"]
    assert (round(w * dpr), round(h * dpr)) == (1080, 1920)
    w, h, dpr = webrecord._TOUR_VIEWPORTS["landscape"]
    assert (round(w * dpr), round(h * dpr)) == (1920, 1080)
    assert webrecord._CSS_VIEWPORTS["portrait"] == (432, 768, 2.5)


def test_a_locale_redirect_is_still_the_home_page():
    assert not webrecord._deep_page("https://stripe.com/ae")
    assert not webrecord._deep_page("https://stripe.com/en-us/")
    assert not webrecord._deep_page("https://phmath.vercel.app/index.html")
    assert not webrecord._deep_page("https://linear.app/")
    assert webrecord._deep_page("https://cal.com/pricing")
    assert webrecord._deep_page("https://acme.io/en/pricing")


def test_a_long_site_name_from_the_title_beats_the_bare_host():
    assert first_cut._brand({"page_title": "Decentralization Research Center"},
                            "https://thedrcenter.org/") == \
        "Decentralization Research Center"
    assert first_cut._brand({"page_title": "Page & Pour | Your Space. Your "
                                           "Pace. Any Hour."},
                            "https://pageandpour.framer.website/") == "Page & Pour"


def test_a_brief_that_asks_for_a_product_page_opens_one():
    assert webrecord._ITEM_ASK.search("scroll smoothly through product grids, "
                                      "and open a product page to highlight")
    assert not webrecord._ITEM_ASK.search("record a demo of https://stripe.com")
    assert webrecord._ITEM_PATH.search("/products/silk-dress")
    assert webrecord._ITEM_PATH.search("/collections/new/products/ring")
    assert not webrecord._ITEM_PATH.search("/collections/new")
    # a grid's "Add to cart" is never the page's closing call to action
    assert "add to cart" in webrecord.TOUR_CTA
    assert "add to cart" not in webrecord.CLOSING_CTA


def test_sections_open_on_their_own_name_never_over_the_title():
    ev = [{"t": 1.0, "kind": "section", "label": "Hero"},
          {"t": 4.2, "kind": "nav", "label": "/pricing"},
          {"t": 5.0, "kind": "section", "label": "Pricing"},
          {"t": 5.95, "kind": "look", "x": .3, "y": .3, "box": [.05, .25, .5, .32]},
          {"t": 9.0, "kind": "section", "label": "Customers"},
          {"t": 9.9, "kind": "look", "x": .3, "y": .3, "box": [.05, .25, .5, .32]}]
    ch = first_cut.demo_chapters(ev, 20.0)
    assert [(c[2], c[3]) for c in ch] == [("Pricing", 1), ("Customers", 2)]
    # each ends where its heading's beat begins
    assert ch[0][:2] == (5.0, 5.95) and ch[1][1] == 9.9
    assert first_cut.chapter_title("Pricing") == "*Pricing*"
    assert first_cut.chapter_title("Stripe for platforms") == "Stripe for / *platforms*"
    assert first_cut.chapter_title("A/B *tests*") == "A B / *tests*"


def test_a_toggle_is_a_beat_but_not_a_move():
    ev = [{"t": 4.0, "kind": "scroll"},
          {"t": 5.0, "kind": "toggle", "x": .5, "y": .4, "box": [.3, .37, .7, .43]},
          {"t": 7.6, "kind": "scroll"}]
    pushes = first_cut.demo_pushes(ev, 12.0)
    assert len(pushes) == 1 and pushes[0]["start"] < 5.0 < pushes[0]["end"] < 7.6


def test_a_brief_s_own_quoted_closing_line_ends_the_demo():
    klosmo = ("Aim for a 15–20 second luxury fashion social media video in "
              "vertical 9:16 format, with minimal text overlays and a final "
              "“Discover KLOSMO” call to action.")
    assert first_cut.closing_line(klosmo) == "Discover KLOSMO"
    assert first_cut.closing_title("Discover KLOSMO") == "Discover / *KLOSMO*"
    osai = ('Use background music.\n\nEnd with the OSAI logo, the phrase '
            '"قصتك تنتمي إلى هنا" and OSAI.ie.\n\nExport as a 16:9 MP4.')
    assert first_cut.closing_line(osai) == "قصتك تنتمي إلى هنا"
    # found, but a right-to-left line is not set in the animated title
    assert first_cut.closing_title("قصتك تنتمي إلى هنا", "osai.ie") is None
    # a quote that is not about the ending is never taken as one
    assert first_cut.closing_line('Open the "Pricing" page and the docs.') is None
    assert first_cut.closing_line(None) is None


def test_the_finale_push_keeps_the_closing_heading_whole():
    ev = [{"t": 15.0, "kind": "scroll"},
          {"t": 16.4, "kind": "finale", "x": .28, "y": .52, "cta": True,
           "box": [.1075, .4865, .46, .5552], "head_box": [.05, .30, .95, .40]}]
    p = first_cut.demo_pushes(ev, 19.0)[-1]
    x0, y0, x1, y1 = _visible(p)
    assert x0 <= .05 and x1 >= .95 and y0 <= .30 and y1 >= .5552


def test_a_scripted_capture_runs_exactly_as_before(monkeypatch):
    """D1: everything above lives in the tour step (the First cut / trial
    recipe). A capture scripted step by step, by any lane, still ends on its
    trailing live drift, on the old viewport, with nothing hidden."""
    calls = []

    class S:
        def sleep(self, s):
            calls.append(("sleep", s))

        def run(self, plan):
            calls.append(("run", [p["do"] for p in plan]))

        def drift(self, s):
            calls.append(("drift", s))

    def fake(url, workdir, orientation, wall_s, cursor, body, css=None):
        body(S())
        calls.append(("css", css))
        return {}
    monkeypatch.setattr(webrecord, "_record", fake)
    monkeypatch.setattr(webrecord, "_hide_consent",
                        lambda page: calls.append(("hide",)))
    webrecord.record_demo("https://acme.io", "/tmp",
                          [{"do": "click", "text": "Pricing"}],
                          orientation="portrait")
    assert calls == [("sleep", 0.9), ("run", ["click"]), ("sleep", 0.3),
                     ("drift", 0.9), ("css", webrecord._DESKTOP_VIEWPORTS)]


def test_a_real_call_to_action_beats_a_softer_link_below_it():
    s = _session()
    info = {"H": 640, "total": 6000, "bottom": 5360, "foot": 5500, "ctas": [
        {"t": "Start now", "top": 4300, "h": 40, "btn": True},
        {"t": "Contact sales", "top": 4300, "h": 40, "btn": True},
        {"t": "Explore payments", "top": 4900, "h": 30, "btn": True}]}
    assert s._closing(info)["t"] == "Start now"


def test_the_title_speaks_the_request_s_language_and_stays_poster_sized():
    pt = ("Acesse https://phmath.vercel.app/index.html e produza um vídeo "
          "promocional elegante e profissional, em formato vertical 9:16, com "
          "aproximadamente 40 segundos.")
    assert first_cut.request_lang(pt) == "pt"
    assert first_cut.request_lang("record a demo of https://stripe.com") is None
    assert first_cut.request_lang("Record my website https://x.io as a 20-second "
                                  "vertical demo: scroll the page from top to "
                                  "bottom at a steady pace") is None
    assert first_cut._site_hook({}, "https://phmath.vercel.app/index.html",
                                "pt") == "Conheça / *Phmath*"
    hook = first_cut._site_hook({"page_title": "Decentralization Research Center"},
                                "https://thedrcenter.org/")
    assert hook == "Meet / *Decentralization* / *Research Center*"
    # a right-to-left closing line is never set in the animated title
    assert first_cut.closing_title("قصتك تنتمي إلى هنا", "osai.ie") is None


def test_a_chapter_names_the_section_the_stop_landed_on(monkeypatch):
    s = _session()
    s._chapters, s._chapter_names, s._paged = [], set(), False
    s.now = lambda: 10.0
    s.sleep = lambda x: None
    info = {"sy": 1000, "heads": [
        {"t": "ABOUT", "top": 400, "vtop": 24, "lvl": 2},       # stuck, being left
        {"t": "EXPERIENCE", "top": 1096, "vtop": 96, "lvl": 2}]}
    s._page = lambda: info
    s._chapter()
    assert [e["label"] for e in s.events if e["kind"] == "section"] == ["EXPERIENCE"]
