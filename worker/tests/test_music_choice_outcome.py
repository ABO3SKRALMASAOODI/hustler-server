import music_offer


def test_a_real_music_choice_is_waiting_for_the_customer():
    assert music_offer.choice_outcome('fulfilled', 'partial', ['music'],
                                     'Pick one of these three tracks.') == 'awaiting_user'


def test_the_offer_never_hides_another_missing_ask_or_failure():
    for before, missing, offer in (
        ('partial', ['music'], 'Pick a track.'),
        ('fulfilled', ['music', 'transitions'], 'Pick a track.'),
        ('fulfilled', ['music'], ''),
    ):
        assert music_offer.choice_outcome(before, 'partial', missing, offer) == 'partial'


def test_localized_music_asks_get_a_choice_without_breaking_negations():
    for ask in ('Füge passende Musik hinzu.', 'Ajoute de la musique.', 'Adicione música.'):
        assert music_offer.wants_music(ask)
    for ask in ('Keine Musik.', 'Sans musique.', 'Sem música.'):
        assert not music_offer.wants_music(ask)
