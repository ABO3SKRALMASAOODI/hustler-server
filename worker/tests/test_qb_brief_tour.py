"""QB (30 Oct 2026): a website brief's own words guide the tour.

Measured on the trial branch (Opus judge, two judgements each) the three real
briefs scored 4.75 (phmath), 5.5 (osai.ie) and 6.0 (klosmo):
  * phmath: "Início, Diagnóstico e Aprender. Em seguida, as seis ferramentas ...
    gráficos ... planificação": the tour spent its time on the first two pages
    and never opened Ferramentas ("the six tools never appear", a hard fault);
  * osai.ie: "student guides, events, support resources": no link of the site
    reads as one of those words exactly, so the tour never left the home page
    and scrolled it for 44 s;
  * klosmo: the brands page (an A-Z text list) ate the whole 20 s; no
    collection, no product page.
Only a tour step with ``brief: true`` changes; a tile or a bare URL keeps the
tour as it was (tests/test_q2_web.py holds that).
"""
import brief_tour
import claude_trial
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
        "Automatically navigate through its pages.\n\nShow the homepage, "
        "student guides, events, support resources and other available "
        "features.\n\nCreate an elegant opening inspired by an Omani "
        "student's journey from Muscat to Ireland.")


class _Page:
    def __init__(self, info=None, url="https://x.io/", cards=None):
        self.info, self.url, self.cards = info or {}, url, cards or []

    def evaluate(self, js, *args):
        if js is webrecord._CARDS_JS:
            return self.cards
        return self.info


def _session(page):
    s = webrecord._Session.__new__(webrecord._Session)
    s.page = page
    s.W, s.H = 360, 640
    s.lost_s = 0.0
    s.events, s.problems = [], []
    return s


def _prefer(text):
    return first_cut.url_hints(text)["prefer"]


# ---- the brief's words ----------------------------------------------------

def test_a_label_is_named_by_word_stem_not_only_as_a_whole_phrase():
    p = _prefer(OSAI)
    assert brief_tour.label_pos("Halal Guide", p) is not None      # "guides"
    assert brief_tour.label_pos("New to Ireland?", p) is not None  # "Ireland"
    assert brief_tour.label_pos("Committee", p) is None
    q = _prefer(PHMATH)
    assert brief_tour.label_pos("Ferramentas", q) is not None
    assert brief_tour.label_pos("Diagnóstico", q) < \
        brief_tour.label_pos("Aprender", q) < \
        brief_tour.label_pos("Ferramentas", q)                     # its order


def test_the_words_that_name_the_request_never_name_a_page():
    p = _prefer(OSAI)
    for generic in ("Video", "Website", "Premium", "Cinematic", "Promotional"):
        assert brief_tour.label_pos(generic, p) is None, generic


def test_a_card_is_named_when_the_brief_is_about_it():
    q = _prefer(PHMATH)
    skip = brief_tour.label_stems("Ferramentas")
    # "gráficos" twice, "planificação" and "sólidos": named
    assert brief_tour.label_score("Gerador de Gráficos", q, skip) >= 2
    assert brief_tour.label_score("Planificação de Sólidos", q, skip) >= 2
    # "matemáticas" once, a domain word of a maths site: not named
    assert brief_tour.label_score("Resolvedor Matemático", q, skip) < 2
    assert brief_tour.label_score("Calculadora Científica", q, skip) == 0


def test_the_time_is_shared_and_a_page_with_cards_may_buy_no_stops():
    assert brief_tour.section_slot(30, 3) == 10
    assert brief_tour.section_slot(-4, 2) == 0
    assert brief_tour.stops_for(14, least=0) == 3
    assert brief_tour.stops_for(4.0, least=0) == 0
    assert brief_tour.stops_for(4.0) == 1
    assert brief_tour.leads_to_products("COLLECTIONS")
    assert not brief_tour.leads_to_products("BRANDS")


def test_the_navigation_fallback_skips_switches_sign_in_and_calls_to_action():
    links = [{"t": "AR", "href": "/ar"}, {"t": "About", "href": "/about"},
             {"t": "Join Us", "href": "/join"}, {"t": "Sign in", "href": "/x"},
             {"t": "Committee", "href": "/committee"},
             {"t": "About", "href": "/about"}, {"t": "Stories", "href": "/s"}]
    got = brief_tour.nav_fallback(links, 3, webrecord.TOUR_AVOID,
                                  webrecord.TOUR_CTA)
    assert [c["t"] for c in got] == ["About", "Committee", "Stories"]


# ---- which pages the tour opens -------------------------------------------

OSAI_LINKS = [{"t": t, "href": "https://osai.ie/" + h} for t, h in (
    ("About", "about"), ("Committee", "committee"),
    ("New to Ireland?", "new-to-ireland"), ("Halal Guide", "halal"),
    ("Stories", "stories"), ("Gallery", "gallery"), ("Coming soon", "soon"),
    ("Contact", "contact"), ("Join Us", "contact#join"), ("AR", "index-ar"))]


def test_a_brief_that_names_few_pages_still_leaves_the_home_page():
    start = "https://osai.ie/"
    s = _session(_Page({"links": OSAI_LINKS}, start))
    got = s._candidates([], _prefer(OSAI), start, brief=True, want=5)
    names = [c["t"] for c in got]
    # the pages the brief names first (its order), then the site's own order
    assert names[:2] == ["New to Ireland?", "Halal Guide"] or \
        names[:2] == ["Halal Guide", "New to Ireland?"]
    assert len(names) == 5 and "Join Us" not in names and "AR" not in names
    # without the brief flag the old tour sees nothing it can match
    old = s._candidates([], _prefer(OSAI), start)
    assert old == []


def test_a_brief_naming_three_pages_gets_exactly_those():
    start = "https://phmath.vercel.app/index.html"
    links = [{"t": t, "href": "https://phmath.vercel.app/" + h} for t, h in (
        ("Ajuda", "ajuda.html"), ("Sobre", "sobre.html"),
        ("Diagnóstico", "diagnostico.html"), ("Aprender", "aprender.html"),
        ("Ferramentas", "ferramentas.html"))]
    s = _session(_Page({"links": links}, start))
    got = s._candidates([], _prefer(PHMATH), start, brief=True, want=4)
    assert [c["t"] for c in got] == ["Diagnóstico", "Aprender", "Ferramentas"]


def test_the_named_cards_of_a_section_page_open_in_the_briefs_order():
    cards = [{"head": h, "t": "Acessar ferramenta",
              "href": f"https://phmath.vercel.app/{k}.html"} for h, k in (
        ("Calculadora Científica", "calc"), ("Gerador de Gráficos", "graf"),
        ("Resolvedor Matemático", "res"), ("Planificação de Sólidos", "plan"),
        ("", "none"))]
    start = "https://phmath.vercel.app/index.html"
    s = _session(_Page({}, "https://phmath.vercel.app/ferramentas.html",
                       cards=cards))
    got = s._named_cards(_prefer(PHMATH), start,
                         brief_tour.label_stems("Ferramentas"), set())
    assert [c["t"] for c in got] == ["Gerador de Gráficos",
                                     "Planificação de Sólidos"]
    # a card already shown is not opened twice
    seen = {s._norm_url("https://phmath.vercel.app/graf.html")}
    got = s._named_cards(_prefer(PHMATH), start, frozenset(), seen)
    assert "Gerador de Gráficos" not in [c["t"] for c in got]


# ---- the step the First cut writes ------------------------------------------

def test_only_a_brief_flags_its_tour_and_keeps_its_long_words():
    steps = first_cut.tour_steps(first_cut.url_hints(PHMATH), brief=True)
    tour = steps[-1]
    assert tour["brief"] is True and tour["by"] == 4
    long = " ".join(["palavra"] * 400)
    plan = webrecord._norm_steps([dict(steps[-1], prefer=long)])
    assert plan[-1]["brief"] is True and len(plan[-1]["prefer"]) == 1600
    # a tile or a bare URL: no flag, the old 600 characters
    plain = first_cut.tour_steps(first_cut.url_hints(PHMATH))
    assert "brief" not in plain[-1]
    assert len(webrecord._norm_steps(
        [dict(plain[-1], prefer=long)])[-1]["prefer"]) == 600


def test_a_brief_s_sections_all_get_a_chapter_name():
    events = [{"t": 3.0 + 4 * k, "kind": "section", "label": f"Page {k}"}
              for k in range(6)]
    assert len(first_cut.demo_chapters(events, 40.0)) == first_cut.DEMO_CHAPTERS
    assert len(first_cut.demo_chapters(events, 40.0, most=7)) == 6


# ---- the trial turn's note --------------------------------------------------

def test_the_trial_turn_records_a_named_page_and_never_asks_for_music():
    note = claude_trial.FIRST_RESULT_NOTE
    assert "never offer to or ask which" in note
    assert "never pick a track and never ask the user to choose one" in note
    assert "does not pick music" in note


# ---- the closing line, the product page, the preview quality ----------------

def test_a_right_to_left_closing_line_is_a_big_card_in_the_briefs_colours(
        monkeypatch):
    import agent_tools
    import schemas
    brief = OSAI + ("\n\nUse dark green, burgundy and gold, matching the OSAI "
                    "brand. End with the OSAI logo, the phrase \"قصتك تنتمي "
                    "إلى هنا\" and OSAI.ie.")
    assert first_cut.closing_line(brief) == "قصتك تنتمي إلى هنا"
    assert first_cut.closing_title("قصتك تنتمي إلى هنا", "osai.ie") is None
    bg, fg, accent = first_cut.brand_colors(brief)
    assert bg == "#0E2A1E" and accent == "#D4A94F" and fg == "#F3E9D2"
    assert first_cut.brand_colors("a plain brief")[0] == first_cut.BEAT_CARD
    seen = []
    monkeypatch.setattr(agent_tools, "execute",
                        lambda c, name, args: seen.append((name, args)) or "EDL v1 ok")
    monkeypatch.setattr(schemas, "program_duration", lambda e: 58.0)
    ctx = type("C", (), {"latest_edl": lambda self: {"json": {}}})()
    first_cut.rtl_closing_card(ctx, [], __import__("time").monotonic(),
                               "قصتك تنتمي إلى هنا", "osai.ie", brief)
    (name, args), (name2, line) = seen
    assert name == "add_title_card" and args["size_scale"] >= 1.2
    assert "subtitle" not in args and args["at_output_s"] == 57.99
    assert args["bg_color"] == "#0E2A1E" and args["accent_color"] == "#D4A94F"
    assert 2.0 <= args["duration_s"] <= 3.0 and args["exit"] == "none"
    # the address is its own line, clear of the phrase (never the card's
    # subtitle slot, which burned it through a large Arabic line)
    assert name2 == "add_text" and line["text"] == "osai.ie"
    assert line["y"] >= 0.62 and line["start"] > 57.99 and line["end"] <= 60.6


def test_a_brief_that_opens_a_product_page_ends_on_it_not_the_footer_form():
    class P(_Page):
        def evaluate(self, js, *a):
            return {"sy": 0, "bottom": 3000, "H": 640, "total": 3640,
                    "ctas": [{"t": "Join Us", "top": 2900, "h": 40, "btn": True,
                              "x": .5, "x0": .1, "x1": .9}], "heads": []}
    s = _session(P({}, "https://shop.io/products/hat"))
    s.mx, s.my = 100.0, 100.0
    s.t_zero = __import__("time").time()
    noted = []
    s.note = lambda kind, *a, **k: noted.append(kind)
    s._tour_items = lambda: ([{"t": "Add to cart", "x": .5, "y": .8,
                               "area": .02, "button": True}], [])
    s.move_to = lambda *a, **k: None
    s._live_hold = lambda *a, **k: None
    s._glide_scroll = lambda *a, **k: noted.append("scrolled")
    s.sleep = lambda *a, **k: None
    s._finale(20.0, here_only=True)
    assert "scrolled" not in noted and noted == ["finale"]
    # the usual finale would have gone to that footer form's button
    assert s._closing(s._page())["t"] == "Join Us"


def test_the_render_refusal_never_tells_the_user_about_limits():
    import agent_loop
    ctx = type("C", (), {"trial_preview_cap": 2, "trial_previews": 2})()
    text = agent_loop._trial_render_refusal(ctx, "render_preview")
    assert "Never mention previews, limits" in text and "limit" not in \
        text.split("Never mention")[0]


def test_a_page_loading_under_its_header_is_a_blank_span(tmp_path):
    """A white body under a drawn header is a loading flash (klosmo)."""
    import subprocess
    import shutil
    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("no ffmpeg")
    out = tmp_path / "cap.mp4"
    # 1 s page, 0.6 s white body under a dark header bar, 1 s page: a ramp
    # of noise stands in for a page with content
    vf = ("color=c=white:s=270x480:d=0.6[w];"
          "[w]drawbox=x=0:y=0:w=270:h=70:color=black:t=fill[b];"
          "testsrc2=s=270x480:d=1.5[a];testsrc2=s=270x480:d=1.5[c];"
          "[a][b][c]concat=n=3:v=1:a=0[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", vf,
                    "-map", "[v]", "-r", "10", "-pix_fmt", "yuv420p",
                    str(out)], check=True)
    spans = first_cut.blank_spans(str(out))
    assert spans and 1.3 <= spans[0][0] <= 1.7 and spans[0][1] - spans[0][0] >= 0.4


def test_the_old_page_held_while_a_slow_site_loads_is_cut():
    """klosmo held the hero 13 s while the next page loaded (pacing 3)."""
    events = [{"t": 3.0, "kind": "click", "label": "COLLECTIONS"},
              {"t": 4.0, "kind": "stale", "end": 9.6},
              {"t": 12.0, "kind": "stale", "end": 12.5},      # too short: kept
              {"t": 16.0, "kind": "stale", "end": 20.0}]
    blank = [[9.4, 10.2], [30.0, 30.4]]
    got = first_cut.cut_spans(blank, events)
    assert got == [[4.0, 10.2], [16.0, 20.0], [30.0, 30.4]]
    assert first_cut.cut_spans([], []) == []


def test_a_slow_open_notes_its_stale_span_and_a_quick_one_does_not(monkeypatch):
    import time as _t

    class P(_Page):
        def __init__(self):
            super().__init__({}, "https://shop.io/")
            page = self

            class M:
                def click(self, x, y):
                    page.pending = True       # the site routes a moment later
            self.mouse = M()
            self.pending = False

        def wait_for_load_state(self, *a, **k):
            pass
    now = {"t": 0.0}
    s = _session(P())
    s.W, s.H = 360, 640
    s.mx, s.my = 10.0, 10.0
    s.deadline = _t.monotonic() + 100
    s.now = lambda: now["t"]
    s.move_to = lambda *a, **k: now.__setitem__("t", now["t"] + 0.5)
    s.note = lambda *a, **k: None
    s._painted = lambda: True
    s._settle = lambda: None
    s._chapters = []

    def sleep(sec):
        now["t"] += sec
        if s.page.pending:
            s.page.pending, s.page.url = False, "https://shop.io/shop"
    s.sleep = sleep
    import webrecord as wr
    monkeypatch.setattr(wr, "_hide_consent", lambda page: None)
    item = {"x": .5, "y": .1, "box": None}

    def load(seconds):
        def wait(*a, **k):
            now["t"] += seconds
        s.page.wait_for_load_state = wait
    load(6.0)
    s.events = []
    assert s._open({"t": "Shop", "href": "https://shop.io/shop", "item": item},
                   "https://shop.io/")
    stale = [e for e in s.events if e["kind"] == "stale"]
    assert len(stale) == 1 and stale[0]["end"] - stale[0]["t"] > 3.0
    load(0.2)
    now["t"], s.events = 0.0, []
    s.page.url = "https://shop.io/"
    assert s._open({"t": "Shop", "href": "https://shop.io/shop", "item": item},
                   "https://shop.io/")
    assert not [e for e in s.events if e["kind"] == "stale"]
