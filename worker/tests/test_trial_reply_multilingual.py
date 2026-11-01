"""Ticket #77 recurrence (1 Nov 2026): a German request ("... Verwende
ausschließlich dieses Video, kein Stock-B-Roll.") was read as a b-roll ask,
because the ledger knew only English negations; the request was marked
partial and the reply said "I didn't add B-roll this time". The same turn's
honest German line about the two points it did not reach was dropped as an
"invented name" ("Das Stottern": German capitalises every noun). The request
is paraphrased here (no customer copy); its structure is the production one."""
import agent_loop
import ask_ledger
import trial_reply as tr
from tests.test_q8_trial_reply import FIRST_CUT, INDEX, facts

GERMAN = """Mach daraus EIN hochwertiges Unboxing-Reel, vertikal 9:16, maximal 30 Sekunden.
Untertitel auf DEUTSCH, schlicht weiß und fett, unten.
Füge KEINE Musik hinzu, behalte den Originalton.
Verwende ausschließlich dieses Video, kein Stock-B-Roll.
Look: ruhig, hochwertig. Keine grellen Effekte."""


def test_negated_asks_in_other_languages_are_not_asks():
    assert "broll" not in ask_ledger.asks(GERMAN)
    for text in ("Bitte ohne B-Roll, nur mein Video.",
                 "Pas de b-roll, sans musique.",
                 "Sin b-roll, solo mi video.",
                 "Senza b-roll, per favore.",
                 "Geen b-roll alsjeblieft.",
                 "Mach es ohne Captions."):
        assert ask_ledger.asks(text) == [], text


def test_asks_in_other_languages_are_still_asks():
    assert "broll" in ask_ledger.asks("Füge passendes B-Roll über das Gespräch ein.")
    assert "broll" in ask_ledger.asks("Ajoute du b-roll sur toute la vidéo.")
    # an English negation still reads as one
    assert ask_ledger.asks("No b-roll please, keep it simple.") == []


def test_german_honest_line_is_not_an_invented_name():
    f = facts(user_text=GERMAN)
    line = ("Zwei Punkte habe ich nicht erreicht: Das Stottern bei 7,6 s ist "
            "noch drin, und das Ende ist kein ruhiger Hero-Shot.")
    reply, report = tr.finish(line, f)
    assert "name" not in [d["why"] for d in report["dropped"]]
    assert "Das Stottern" in reply
    # a real invented name is still caught
    _r, rep = tr.finish("The 48-second short is finished: a Sam Altman clip "
                        "about deep learning working.", facts(duration_s=47.7))
    assert [d["why"] for d in rep["dropped"]] == ["name"]


def test_german_request_without_broll_stays_fulfilled():
    _reply, report = tr.finish("Der Schnitt ist fertig: 9:16, Originalton ohne "
                               "Musik, deutsche Untertitel unten.",
                               facts(user_text=GERMAN, before=FIRST_CUT, after=FIRST_CUT))
    assert "broll" not in (report.get("unmet") or [])
    honesty = {}
    agent_loop._outcome_after_reply("fulfilled", report, honesty)
    assert "broll" not in honesty.get("unmet_asks", [])


def test_german_music_request_cannot_end_done_with_a_silent_edit():
    # Ticket 49's latest recurrence: the initial brief prohibited music,
    # but the follow-up explicitly requested Musik and muted the voice.
    request = 'Entferne die Stimme und füge passende Musik hinzu.'
    reply, report = tr.finish('Done: I muted the sound.', facts(user_text=request))
    assert report['asks']['music'] == 'not_done'
    assert report['unmet'] == ['music']
    assert "didn't add music" in reply
    assert not reply.startswith('Done')
    for request in ('Füge keine Musik hinzu.', 'Sans musique.', 'Sem música.'):
        assert 'music' not in ask_ledger.asks(request)
