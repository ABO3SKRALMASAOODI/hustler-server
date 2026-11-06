import music_offer


def test_a_real_music_choice_is_waiting_for_the_customer():
    assert music_offer.choice_outcome('fulfilled', 'Pick one of these three tracks.') == 'awaiting_user'


def test_a_turn_that_wrote_nothing_waits_on_the_choice_too():
    assert music_offer.choice_outcome('blocked', 'Pick a track.') == 'awaiting_user'


def test_the_offer_never_hides_a_failure_and_no_offer_changes_nothing():
    assert music_offer.choice_outcome('partial', 'Pick a track.') == 'partial'
    assert music_offer.choice_outcome('internal_error', 'Pick a track.') == 'internal_error'
    assert music_offer.choice_outcome('blocked', '') == 'blocked'
    assert music_offer.choice_outcome('fulfilled', '') == 'fulfilled'


def test_localized_music_asks_get_a_choice_without_breaking_negations():
    for ask in ('Füge passende Musik hinzu.', 'Ajoute de la musique.', 'Adicione música.'):
        assert music_offer.wants_music(ask)
    for ask in ('Keine Musik.', 'Sans musique.', 'Sem música.'):
        assert not music_offer.wants_music(ask)
