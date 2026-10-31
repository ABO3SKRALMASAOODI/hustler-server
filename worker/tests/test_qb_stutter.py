"""QB (30 Oct 2026): a word said twice in a row is cut once, for engine
accounts: the 10-minute talk's captions printed "is is deep" and "work and
And" and both judgements of it marked them (a grandfathered payer's First cut
is unchanged)."""
import agent_tools
import first_cut


def _words(spec, step=0.4):
    out, t = [], 10.0
    for w in spec.split():
        out.append({"w": w, "t0": round(t, 2), "t1": round(t + step - 0.05, 2)})
        t += step
    return out


def test_only_short_function_words_said_twice_are_stutters():
    words = _words("it is is deep and very very important and And the work")
    spans = first_cut.stutter_spans(words, [[9.0, 20.0]])
    got = sorted(round(a, 2) for a, _b in spans)
    # "is is" (the first is) and "and And" (the first and); "very very" is
    # an emphasis the speaker means
    firsts = {words[k]["t0"] for k in (1, 8)}
    assert set(got) == firsts


def test_a_gap_or_a_word_outside_the_program_is_no_stutter():
    words = _words("the the")
    words[1]["t0"] = words[0]["t1"] + 0.6          # a real pause between them
    assert first_cut.stutter_spans(words, [[9.0, 20.0]]) == []
    assert first_cut.stutter_spans(_words("the the"), [[30.0, 40.0]]) == []
    assert first_cut.stutter_spans(_words("the cat the dog"), [[9.0, 20.0]]) == []


class _Ctx:
    def __init__(self, words, keep):
        self.index = {"words": words}
        self._keep = keep

    def latest_edl(self):
        return {"version": 3, "json": {"keep": self._keep}}


def test_each_stutter_is_cut_out_of_the_kept_program_in_one_write(monkeypatch):
    seen = []

    def execute(ctx, name, args):
        seen.append((name, args))
        return "EDL v3 -> v4: keep_segments"
    monkeypatch.setattr(agent_tools, "execute", execute)
    words = _words("it is is deep and And the work")
    ctx = _Ctx(words, [[9.0, 20.0]])
    import time
    steps = []
    res = first_cut.drop_stutters(ctx, steps, time.monotonic())
    assert first_cut._ok(res)
    (name, args), = seen
    assert name == "keep_segments" and args["snap_to_words"] is False
    segs = args["segments"]
    assert len(segs) == 3
    # the first "is" and the first "and" are gone, nothing else
    assert segs[0][1] == words[1]["t0"] and segs[1][0] == round(words[2]["t0"] - 0.01, 3)
    assert segs[-1][1] == 20.0
    # nothing to cut: no write
    seen.clear()
    assert first_cut.drop_stutters(_Ctx(_words("a clean sentence"), [[9.0, 20.0]]),
                                   [], time.monotonic()) is None
    assert not seen


# ---- who gets it (D1) --------------------------------------------------------

class _TalkCtx:
    def __init__(self, q1):
        spec = ("so it is is deep and the work we do here matters because "
                "people are watching closely today")
        # RS_ENGINE (31 Oct): a TALK, i.e. speech over most of the source
        # (18 words in 8 s of a 60 s source is sparse speech, which the
        # First cut now cuts by the footage: first_cut.sparse_speech)
        self.index = {"video": {"duration": 20.0, "width": 1920, "height": 1080},
                      "words": _words(spec), "sentences": [
                          {"id": "s0", "t0": 10.0, "t1": 18.0,
                           "text": spec.capitalize() + "."}]}
        self.duration = 20.0
        self.first_cut_q1 = q1
        self.versions_written, self.web_recordings = [], []
        self.last_preview = None

    def latest_edl(self):
        return {"version": 2, "json": {"keep": [[0.0, 20.0]]}}


def test_only_an_engine_account_has_its_stutters_cut(monkeypatch):
    import time
    calls = []

    def execute(ctx, name, args):
        calls.append((name, args))
        ctx.versions_written.append(len(ctx.versions_written) + 2)
        return f"EDL v{len(ctx.versions_written) + 1} ok"
    monkeypatch.setattr(agent_tools, "execute", execute)
    first_cut.video_chain(_TalkCtx(True), None, time.monotonic(), {"usd": 0.0})
    cut = [a for n, a in calls if n == "keep_segments"
           and a.get("snap_to_words") is False and len(a["segments"]) > 1]
    assert cut, [n for n, _a in calls]
    calls.clear()
    first_cut.video_chain(_TalkCtx(False), None, time.monotonic(), {"usd": 0.0})
    assert not [a for n, a in calls if n == "keep_segments"
                and a.get("snap_to_words") is False and len(a["segments"]) > 1]
