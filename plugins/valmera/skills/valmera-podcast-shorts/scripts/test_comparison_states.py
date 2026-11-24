from wf1_display import comparison_pages


def test_one_case_changes_on_each_spoken_action_and_holds_its_application():
    words = [dict(w="word")] * 84
    case = dict(first=31, last=83, premise="Missing practice and arriving late", reveal=47,
                consequence="Off the team", steps=[dict(at=33, label="Misses practice"),
                dict(at=36, label="Late to the game")], application=dict(at=59,
                speaker="Jason Calacanis", label="He expects people to show up on time"))
    pages = comparison_pages([], case, words)
    assert [(p["first"], p["last"], p["analogy_step"]["label"]) for p in pages] == [
        (33,35,"Misses practice"), (36,46,"Late to the game"), (47,58,"Off the team"),
        (59,83,"He expects people to show up on time")]
    assert pages[1]["comparison_labels"] == ["Misses practice", "Late to the game"]
    assert pages[-1]["heading"] == "Jason Calacanis’s view"


def test_retained_combined_premise_uses_one_seat_and_keeps_spoken_outcome():
    premise = "If you miss practice and arrive late to the game"
    outcome = "You can’t stay on the team"
    case = dict(first=31,last=83,premise=premise,consequence=outcome,reveal=52,
                steps=[dict(at=33,label=premise),dict(at=47,label=outcome)])
    pages = comparison_pages([], case, [dict(w="word")]*84)
    assert all(len(p["comparison_labels"]) == len(set(p["comparison_labels"])) for p in pages)
    assert pages[-1]["analogy_step"]["label"] == outcome
