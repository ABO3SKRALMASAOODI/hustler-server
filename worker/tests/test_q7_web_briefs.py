"""Q7 (30 Oct 2026): real users' website briefs, met as asked or with the
closest honest equivalent.

Measured on the trial branch (QB, Opus judge, rubric v2) the three real briefs
sat at 6.1 (phmath), 6.9 (osai.ie) and 7.5 (klosmo). Every judgement named the
same gaps:
  * phmath: "show all six tools, each with a real interaction (a calculation
    typed in, its result)", "Diagnóstico and Aprender appear only as blurred
    title cards", "ends on an idle form, not a call to action";
  * osai.ie: no Muscat-to-Ireland opening, no floating browser windows, the
    closing phrase fades out before the end, 50 s for a 60 s ask, and a reply
    that says nothing about what was or was not done;
  * klosmo: the brands section never opens, the product page is one held
    hoodie shot, the announcement ticker reads as garbled words.
Only the tour step (engine accounts' First cut) and the First cut's URL chain
change; tests/test_q2_web.py and tests/test_qb_brief_tour.py hold the rest.
"""
import time

import brief_tour
import first_cut
import webrecord

PHMATH = ("Acesse https://phmath.vercel.app/index.html e produza um vídeo "
          "promocional elegante e profissional, em formato vertical 9:16, com "
          "aproximadamente 40 segundos. Explore as seções Início, Diagnóstico "
          "e Aprender. Em seguida, apresente as seis ferramentas matemáticas, "
          "utilizando gravações reais das funcionalidades. Mostre gráficos "
          "somente na ferramenta de gráficos e sólidos tridimensionais na "
          "ferramenta de planificação. Utilize transições suaves, textos "
          "curtos em português e música instrumental sem narração. Preserve "
          "a identidade visual azul do PhMath.")
OSAI = ("Create a premium 60-second cinematic promotional video for "
        "https://osai.ie.\n\nUse the actual website as the main visual source. "
        "Automatically navigate through its pages, capturing beautiful "
        "scrolling animations, cursor movements, smooth zooms and "
        "transitions.\n\nShow the homepage, student guides, events, support "
        "resources and other available features.\n\nCreate an elegant opening "
        "inspired by an Omani student's journey from Muscat to Ireland.\n\n"
        "Use dark green, burgundy and gold, matching the OSAI brand.\n\nMake "
        "the video look like a professionally produced technology launch "
        "advertisement, with floating browser windows and cinematic motion "
        "graphics.\n\nDo not generate a voice-over. I will record my own "
        "Arabic narration after the video is complete.\n\nUse background "
        "music and subtle sound effects, and leave enough time in each scene "
        "for narration.\n\nEnd with the OSAI logo, the phrase \"قصتك تنتمي "
        "إلى هنا\" and OSAI.ie.\n\nExport as a 16:9 MP4, preferably with a "
        "separate 9:16 version.")
KLOSMO = ("https://klosmo-store.com/ Create a premium fashion e-commerce "
          "promotional video using https://klosmo-store.com. Navigate through "
          "the homepage, open the designer brands section, explore selected "
          "fashion collections, scroll smoothly through product grids, and "
          "open a product page to highlight product imagery and details. Use "
          "elegant cursor movements, cinematic zooms, refined transitions and "
          "sophisticated editorial pacing. Keep the actual website design and "
          "product imagery unchanged. Aim for a 15–20 second luxury fashion "
          "social media video in vertical 9:16 format, with minimal text "
          "overlays and a final “Discover KLOSMO” call to action.")


# ---- the brief's words: counts, journeys, windows, other asks --------------

def test_a_count_of_one_pages_items_is_read_only_before_that_page():
    assert brief_tour.count_ask(PHMATH, "Ferramentas") == 6
    # the count belongs to the tools page, never to another page
    assert brief_tour.count_ask(PHMATH, "Diagnóstico") == 0
    assert brief_tour.count_ask(PHMATH, "Aprender") == 0
    assert brief_tour.count_ask("show all the tools it has", "Tools") == \
        brief_tour.ALL_ITEMS
    assert brief_tour.count_ask("show all the tools it has", "Pricing") == 0
    # a length or a shape is not a count of anything
    assert brief_tour.count_ask(OSAI, "Stories") == 0
    assert brief_tour.count_ask(KLOSMO, "COLLECTIONS") == 0


def test_a_journey_opening_is_the_briefs_own_two_places_or_nothing():
    assert brief_tour.journey(OSAI) == ("Muscat", "Ireland")
    assert brief_tour.journey(KLOSMO) is None
    assert brief_tour.journey(PHMATH) is None
    # a "from X to Y" outside a sentence about the opening is not a journey
    assert brief_tour.journey("Prices go from Basic to Pro on that page.") is None
    assert first_cut.opening_title_s(OSAI) == \
        first_cut.DEMO_TITLE_S + first_cut.JOURNEY_S
    assert first_cut.opening_title_s(KLOSMO) == first_cut.DEMO_TITLE_S


def test_floating_windows_music_a_logo_and_a_second_shape_are_recognised():
    assert brief_tour.wants_window(OSAI)
    assert not brief_tour.wants_window(KLOSMO)
    assert not brief_tour.wants_window(PHMATH)
    assert brief_tour.other_asks(OSAI) == {"music": True, "logo": True,
                                           "second_shape": "9:16"}
    assert brief_tour.other_asks(PHMATH) == {"music": True, "logo": False,
                                             "second_shape": None}
    assert brief_tour.other_asks(KLOSMO) == {"music": False, "logo": False,
                                             "second_shape": None}


def test_a_brands_page_is_named_but_the_brand_itself_names_nothing():
    p = first_cut.url_hints(KLOSMO)["prefer"]
    assert brief_tour.label_pos("Explore All Men’s Brands", p) is not None
    assert brief_tour.label_score("Explore All Men’s Brands", p) >= 2
    # "matching the OSAI brand" never names a page called Brand
    q = first_cut.url_hints(OSAI)["prefer"]
    assert brief_tour.label_pos("Brand", q) is None


# ---- the recorder: every counted item, each tool used with its result ------

class _Loc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    def bounding_box(self, timeout=None):
        return self.page.boxes.get(self.sel)

    def fill(self, value, timeout=None):
        self.page.calls.append(("fill", self.sel, value))


class _Mouse:
    def __init__(self, page):
        self.page = page

    def move(self, x, y):
        pass

    def click(self, x, y):
        self.page.calls.append(("click", round(x), round(y)))


class _Keys:
    def __init__(self, page):
        self.page = page

    def type(self, text, delay=None):
        self.page.calls.append(("type", text))


class _Page:
    """Just enough of a Playwright page for the tour's tool beats."""

    def __init__(self, form=None, keypad=None, result=None, cards=None):
        self.form, self.keypad, self.result = form, keypad, result
        self.cards = cards or []
        self.calls, self.sy, self.url = [], 0.0, "https://x.io/tool"
        self.boxes = {}
        self.mouse, self.keyboard = _Mouse(self), _Keys(self)

    def evaluate(self, js, *args):
        if js is webrecord._FORM_JS:
            return self.form or {"inputs": [], "button": None}
        if js is webrecord._KEYPAD_JS:
            return self.keypad
        if js is webrecord._RESULT_JS:
            return self.result
        if js is webrecord._CARDS_JS:
            return self.cards
        if js is webrecord._WATCH_JS:
            self.calls.append(("watch",))
            return True
        if isinstance(js, str) and js.startswith("window.scrollTo"):
            self.sy = float(js.split("(0,")[1].split(")")[0])
            return None
        if "scrollY" in str(js):
            return self.sy
        return None

    def locator(self, sel):
        return _Loc(self, sel)

    def wait_for_timeout(self, ms):
        pass


def _session(page):
    s = webrecord._Session.__new__(webrecord._Session)
    s.page = page
    s.deadline = time.monotonic() + 60
    s.W, s.H = 360, 640
    s.t_zero = time.time()
    s.events, s.problems = [], []
    s.mx, s.my = 180.0, 270.0
    s.lost_s = 0.0
    s._toggled, s._chapters = set(), []
    s.sleep = lambda seconds: None      # no real waiting in a unit test
    return s


def test_a_tool_is_used_its_example_typed_again_and_its_result_held():
    page = _Page(
        form={"inputs": [{"sel": "[i0]", "value": "x^2 - 4*x + 3",
                          "ph": "Ex.: x^2 - 4*x + 3", "type": "text",
                          "top": 644.0}],
              "button": {"sel": "[b]", "t": "Gerar gráfico", "top": 711.0}},
        result={"top": 880.0, "left": 24, "right": 336, "h": 350, "w": 312})
    page.boxes = {"[i0]": {"x": 20, "y": 300, "width": 314, "height": 56},
                  "[b]": {"x": 20, "y": 366, "width": 314, "height": 52}}
    s = _session(page)
    assert s._use_tool(s.now() + 20)
    kinds = [c[0] for c in page.calls]
    # the field is cleared, the example typed so the viewer sees the input,
    # then the button is pressed
    assert ("fill", "[i0]", "") in page.calls
    assert ("type", "x^2 - 4*x + 3") in page.calls
    assert kinds.index("type") < max(i for i, k in enumerate(kinds) if k == "click")
    # the result is a beat with its own box (the edit pushes in on it)
    res = [e for e in s.events if e.get("result")]
    assert len(res) == 1 and res[0]["kind"] == "look" and res[0].get("box")
    # the move to the input and the transit to the result are cut
    assert sum(1 for e in s.events if e["kind"] == "skip") >= 1
    assert s.lost_s > 0


def test_a_placeholder_example_loses_its_prefix_and_a_far_field_is_left():
    page = _Page(
        form={"inputs": [{"sel": "[i0]", "value": "", "ph": "Ex.: 3x + 7 = 22",
                          "type": "text", "top": 348.0}],
              "button": {"sel": "[b]", "t": "Resolver", "top": 414.0}},
        result={"top": 900.0, "left": 33, "right": 327, "h": 978, "w": 294})
    page.boxes = {"[i0]": {"x": 30, "y": 300, "width": 294, "height": 54},
                  "[b]": {"x": 30, "y": 366, "width": 294, "height": 52}}
    s = _session(page)
    assert s._use_tool(s.now() + 20)
    assert ("type", "3x + 7 = 22") in page.calls        # never "Ex.: ..."
    assert not any(c[0] == "fill" for c in page.calls)  # it was empty
    # a solid's size a screen above its 3D view: only the button is pressed
    far = _Page(
        form={"inputs": [{"sel": "[i0]", "value": "4", "ph": "", "type": "number",
                          "top": 607.0}],
              "button": {"sel": "[b]", "t": "▶ Abrir automaticamente",
                         "top": 1294.0}},
        result={"top": 864.0, "left": 30, "right": 330, "h": 298, "w": 300})
    far.boxes = {"[b]": {"x": 46, "y": 384, "width": 268, "height": 50}}
    s2 = _session(far)
    assert s2._use_tool(s2.now() + 20)
    assert not any(c[0] == "type" for c in far.calls)
    assert any(e.get("result") for e in s2.events)


def test_a_calculator_is_pressed_one_two_times_eight_equals():
    keys = {k: {"sel": f"[k{k}]", "top": 700.0} for k in
            ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "=", "×")}
    page = _Page(keypad=keys,
                 result={"top": 357.0, "left": 27, "right": 333, "h": 124, "w": 306})
    page.boxes = {v["sel"]: {"x": 20 + 30 * i, "y": 380, "width": 60, "height": 50}
                  for i, v in enumerate(keys.values())}
    s = _session(page)
    assert s._use_tool(s.now() + 20)
    pressed = [e["label"] for e in s.events if e["kind"] == "click"]
    assert pressed == ["1", "2", "×", "8", "="]
    assert any(e.get("result") for e in s.events)


def test_a_page_with_no_tool_is_not_pretended_to_be_used():
    s = _session(_Page())
    assert s._use_tool(s.now() + 20) is False
    assert not any(e.get("result") for e in s.events)


def test_a_counted_page_opens_the_named_items_first_then_its_others():
    cards = [{"head": h, "t": "Acessar ferramenta",
              "href": f"https://phmath.vercel.app/{k}.html"} for h, k in (
        ("Calculadora Científica", "calc"), ("Gerador de Gráficos", "graf"),
        ("Decompositor Numérico", "dec"), ("Resolvedor Matemático", "res"),
        ("Planificação de Sólidos", "plan"),
        ("Laboratório de Estatística", "est"), ("", "none"))]
    page = _Page(cards=cards)
    page.url = "https://phmath.vercel.app/ferramentas.html"
    s = _session(page)
    prefer = first_cut.url_hints(PHMATH)["prefer"]
    skip = brief_tour.label_stems("Ferramentas")
    got = s._named_cards(prefer, "https://phmath.vercel.app/index.html", skip,
                         set(), fill_to=6)
    names = [c["t"] for c in got]
    assert names[:2] == ["Gerador de Gráficos", "Planificação de Sólidos"]
    assert len(names) == 6 and set(names[2:]) == {
        "Calculadora Científica", "Decompositor Numérico",
        "Resolvedor Matemático", "Laboratório de Estatística"}
    # without a count, only the named ones (QB's behaviour, unchanged)
    assert [c["t"] for c in s._named_cards(prefer, "https://phmath.vercel.app/",
                                           skip, set())] == names[:2]


def test_a_dense_capture_keeps_its_tail_instead_of_dropping_it(tmp_path):
    import base64
    cast = webrecord._Screencast.__new__(webrecord._Screencast)
    cast.dir, cast.frames, cast.dropped, cast._n = str(tmp_path), [], 0, 0
    cast._stopped = False
    cast.cdp = type("C", (), {"send": lambda self, *a, **k: None})()
    data = base64.b64encode(b"\xff\xd8jpeg").decode()
    # 100 frames a second for 2 s: the file is 30 fps, so ~30/s are kept and
    # the last picture of each burst is the one kept
    for k in range(200):
        cast._on_frame({"data": data, "metadata": {"timestamp": 1000.0 + k / 100}})
    assert 45 <= len(cast.frames) <= 70 and cast.dropped == 0
    gaps = [b[1] - a[1] for a, b in zip(cast.frames, cast.frames[1:])]
    assert min(gaps) >= cast.MIN_GAP_S - 1e-9


# ---- the First cut: the tour step, the cut spans, the closing, the reply ---

def test_the_tour_carries_the_titles_length_and_cuts_the_transit():
    steps = first_cut.tour_steps(first_cut.url_hints(OSAI), brief=True,
                                 title_s=first_cut.opening_title_s(OSAI))
    assert steps[-1]["title_s"] == round(first_cut.DEMO_TITLE_S
                                          + first_cut.JOURNEY_S, 2)
    plan = webrecord._norm_steps(steps)
    assert plan[-1]["title_s"] == steps[-1]["title_s"]
    # no title length asked: none written (the recorder's default holds)
    assert "title_s" not in first_cut.tour_steps({"seconds": 20})[-1]
    assert first_cut.title_blur(first_cut.DEMO_TITLE_S) == first_cut.DEMO_TITLE_BLUR
    spans = first_cut.cut_spans([[1.0, 1.4]], [
        {"t": 5.0, "kind": "skip", "end": 6.1},
        {"t": 8.0, "kind": "skip", "end": 8.2},        # too short to cut
        {"t": 9.0, "kind": "stale", "end": 10.5}])
    assert spans == [[1.0, 1.4], [5.0, 6.1], [9.0, 10.5]]


def test_a_brief_with_no_closing_line_ends_on_the_sites_name_and_address():
    rec = {"site_name": "PhMath"}
    hints = first_cut.url_hints(PHMATH)
    asked, title = first_cut.closing_for(
        PHMATH, hints, [{"kind": "finale"}], True, rec,
        "https://phmath.vercel.app/index.html")
    assert asked is None and title == "PhMath / *phmath.vercel.app*"
    # a brief's own quoted line wins
    asked, title = first_cut.closing_for(KLOSMO, first_cut.url_hints(KLOSMO),
                                         [], True, {"site_name": "KLOSMO"},
                                         "https://klosmo-store.com/")
    assert asked == "Discover KLOSMO" and "Discover" in title
    # a tour that ends on a real call to action needs no sign-off
    plain = "record a demo of https://stripe.com"
    assert first_cut.closing_for(plain, first_cut.url_hints(plain),
                                 [{"kind": "finale", "cta": True}], False,
                                 {"site_name": "Stripe"},
                                 "https://stripe.com") == (None, None)
    # one that could not (a portfolio's last card) ends on the name
    _a, title = first_cut.closing_for(plain, first_cut.url_hints(plain),
                                      [{"kind": "finale"}], False,
                                      {"site_name": "Brittany Chiang"},
                                      "https://brittanychiang.com")
    assert title == "Brittany Chiang / *brittanychiang.com*"


def test_the_brand_pair_is_the_briefs_two_colours():
    assert first_cut.brand_pair(OSAI) == ("#0E2A1E", "#4A0E1E")
    a, b = first_cut.brand_pair("a navy look")
    assert a == "#0E1A33" and b != a


def _events(*sections):
    out = []
    for label, used in sections:
        out.append({"kind": "section", "label": label})
        if used:
            out.append({"kind": "look", "label": "result", "result": True})
    return out


def test_the_reply_says_what_the_cut_shows_and_nothing_more():
    ev = _events(("Diagnóstico", False), ("Aprender", False),
                 ("Ferramentas", False), ("Gerador de Gráficos", True),
                 ("Planificação de Sólidos", True),
                 ("Calculadora Científica", False))
    f = first_cut.brief_facts(PHMATH, "https://phmath.vercel.app/index.html",
                              {"site_name": "PhMath"}, ev, ["a title"], None,
                              None, "PhMath / *phmath.vercel.app*", True)
    text = first_cut.brief_reply(f, 46.2)
    assert "46-second vertical demo of phmath.vercel.app" in text
    assert "Gerador de Gráficos and Planificação de Sólidos, each with its " \
           "result on screen" in text
    # a page opened without its tool used is never claimed as used
    assert "Calculadora Científica" not in text.split("uses")[1].split("each")[0]
    assert "ends on \"phmath.vercel.app\"" in text
    # music was asked for: said plainly, never claimed, no narration invented
    assert "no music" in text and "narration" not in text
    for word in ("limit", "can't", "cannot", "unable", "not able"):
        assert word not in text.lower()


def test_the_reply_to_a_brief_with_asks_a_capture_cannot_make():
    trip = brief_tour.journey(OSAI)
    ev = _events(("Halal Guide", False), ("New to Ireland?", False),
                 ("STORIES", False))
    applied = ["a title", "the opening journey", "the closing line",
               "a floating window"]
    f = first_cut.brief_facts(OSAI, "https://osai.ie", {"site_name": "OSAI"},
                              ev, applied, trip, first_cut.closing_line(OSAI),
                              None, False)
    text = first_cut.brief_reply(f, 59.6)
    assert "60-second landscape demo of osai.ie" in text
    assert "It opens on \"From Muscat to Ireland\", then \"Meet OSAI\"" in text
    assert "Halal Guide, New to Ireland and Stories" in text
    assert "ends on \"قصتك تنتمي إلى هنا\"" in text
    assert "floating window on your dark green and burgundy" in text
    assert "send me your logo file" in text
    assert "your narration and a track you choose" in text
    assert "This is the 16:9 version; ask for the 9:16 one" in text
    # nothing it did not do: without the window or the journey, no claim
    f2 = first_cut.brief_facts(OSAI, "https://osai.ie", {"site_name": "OSAI"},
                               ev, ["a title"], trip, None, None, False)
    t2 = first_cut.brief_reply(f2, 50.0)
    assert "floating" not in t2 and "Muscat" not in t2 and "ends on" not in t2


def test_the_reply_to_a_shop_brief_names_its_product_beat():
    ev = [{"kind": "section", "label": "Featured Brands for Women"},
          {"kind": "section", "label": "COLLECTIONS"},
          {"kind": "hover", "label": "the product's picture"}]
    f = first_cut.brief_facts(KLOSMO, "https://klosmo-store.com/",
                              {"site_name": "KLOSMO"}, ev,
                              ["a title", "the closing line"], None,
                              "Discover KLOSMO", "Discover / *KLOSMO*", True)
    text = first_cut.brief_reply(f, 18.4)
    assert text.startswith("Here's your first cut: an 18-second vertical demo")
    assert "Featured Brands for Women and Collections" in text
    assert "opens a product page for its picture, name and price" in text
    assert "ends on \"Discover KLOSMO\"" in text
    assert "music" not in text and "logo" not in text


def test_a_slow_read_of_the_page_is_cut_and_given_back_once():
    page = _Page(form={"inputs": [], "button": None})
    s = _session(page)
    clock = iter([10.0, 11.4, 20.0, 20.2])
    s.now = lambda: next(clock)
    s._eval(webrecord._FORM_JS)          # 1.4 s: cut
    s._eval(webrecord._FORM_JS)          # 0.2 s: kept
    skips = [e for e in s.events if e["kind"] == "skip"]
    assert skips == [{"t": 10.1, "kind": "skip", "end": 11.35}]
    assert abs(s.lost_s - 1.25) < 1e-6
    # a span inside one already cut is not given back twice
    s.skip(10.5, 11.0)
    assert abs(s.lost_s - 1.25) < 1e-6
    s.skip(11.0, 12.0)                   # half of it new
    assert abs(s.lost_s - 1.9) < 1e-6


def test_a_right_to_left_closing_card_is_inside_the_length_asked(monkeypatch):
    seen = {}
    monkeypatch.setattr(first_cut, "_run",
                        lambda ctx, steps, name, args, started:
                        seen.setdefault(name, args) and "REJECTED: test")
    ctx = type("C", (), {"web_recordings": []})()
    first_cut.url_chain(ctx, None, "https://osai.ie", time.monotonic(), OSAI)
    tour = seen["record_website_demo"]["steps"][-1]
    assert tour["seconds"] == 66.0 - first_cut.RTL_CARD_S
    assert tour["title_s"] == round(first_cut.opening_title_s(OSAI), 2)
    assert seen["record_website_demo"]["orientation"] == "landscape"


def test_a_long_address_in_the_closing_line_is_set_smaller():
    assert first_cut.signoff_size("Discover / *KLOSMO*") == 1.1
    small = first_cut.signoff_size("PhMath / *phmath.vercel.app*")
    assert small < 0.85
    assert first_cut.signoff_size("Stripe / *stripe.com*", vertical=False) == 0.85


def test_nothing_is_cut_under_the_opening_title():
    s = _session(_Page())
    s.cut_from = 2.7
    s.skip(1.0, 2.4)                     # all of it under the title: kept
    s.skip(2.0, 3.5)                     # only the part after the title
    assert [e for e in s.events if e["kind"] == "skip"] == [
        {"t": 2.7, "kind": "skip", "end": 3.5}]
    assert abs(s.lost_s - 0.8) < 1e-6


def test_a_brief_ends_on_its_own_card_inside_the_length_asked(monkeypatch):
    import schemas
    seen = []
    monkeypatch.setattr(first_cut, "_run",
                        lambda ctx, steps, name, args, started:
                        seen.append((name, args)) or "EDL v1 ok")
    lengths = iter([40.0, 41.8, 41.8])
    monkeypatch.setattr(schemas, "program_duration", lambda e: next(lengths))
    ctx = type("C", (), {"latest_edl": lambda self: {"json": {}}})()
    first_cut.closing_card(ctx, [], time.monotonic(),
                           "PhMath / *phmath.vercel.app*", PHMATH, True)
    names = [n for n, _a in seen]
    assert names == ["add_color_screen", "add_motion_graphic", "add_zoom"]
    card, title, push = (a for _n, a in seen)
    assert card["color"] == "#123A8C" and card["at_output_s"] == 39.99
    assert title["end"] == 41.8 and title["params"]["exit"] == "none"
    assert title["start"] == 39.79          # it lands on the cut
    assert push["end"] == 41.8
    # the tour leaves the card room; a short ask lands inside its range
    seen.clear()
    monkeypatch.setattr(first_cut, "_run",
                        lambda ctx, steps, name, args, started:
                        seen.append((name, args)) or "REJECTED: test")
    first_cut.url_chain(type("C", (), {"web_recordings": []})(), None,
                        "https://klosmo-store.com/", time.monotonic(), KLOSMO)
    tour = seen[0][1]["steps"][-1]
    assert tour["seconds"] == round(20 * 0.9 - first_cut.BRIEF_CARD_S, 1)
