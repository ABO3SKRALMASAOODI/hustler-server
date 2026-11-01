import first_cut


def test_hook_cannot_translate_or_invent_a_claim():
    index = {'words': [{'w': w} for w in 'Это заколки для волос они очень удобные'.split()]}
    assert first_cut.clean_hook('I HAVE A GRUDGE', index) is None
    assert first_cut.clean_hook('Very convenient hair clips', index) is None
    assert first_cut.clean_hook('заколки для *волос*', index) == 'заколки для *волос*'


def test_hook_words_cannot_be_reordered_into_a_different_claim():
    index = {'words': [{'w': w} for w in 'I do not love wasting money but I love saving time'.split()]}
    assert first_cut.clean_hook('I love wasting money', index) is None
    assert first_cut.clean_hook('I love saving *time*', index) == 'I love saving *time*'


def test_unicode_punctuation_and_accents_are_preserved():
    index = {'words': [{'w': w} for w in 'Découvrez les cheveux bouclés !'.split()]}
    assert first_cut.clean_hook('les cheveux *bouclés*', index) == 'les cheveux *bouclés*'
