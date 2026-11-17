"""The reply names an added sound effect by its kind, never by the library file's prefix, number
or content hash (17 Nov 2026: a first cut's reply read "a lib whoosh 6 5b0c8ef2f7 at 1.48 s")."""
import edit_provenance as ep


def _sfx(i, at, key=None, **kw):
    item = {"id": f"snd{i}", "at": at}
    if key:
        item["storage_key"] = key
    item.update(kw)
    return item


def test_library_file_names_become_the_sound_kind():
    after = {"sfx": [_sfx(1, 1.48, "sfx/lib-whoosh_6-5b0c8ef2f7.flac"),
                     _sfx(2, 5.69, "sfx/lib-click_6-210740866a.flac"),
                     _sfx(3, 23.05, "sfx/lib-impact_9-f2cd801f9c.flac")]}
    text = ep.sounds_added({}, after)
    assert text == "3 sound effects added: a whoosh at 1.48 s, a click at 5.69 s and an impact at 23.05 s"
    assert "lib" not in text and "5b0c8ef2f7" not in text


def test_a_label_with_ids_is_cleaned_and_a_plain_label_is_kept():
    assert ep._sound_name({"name": "lib braam 1 df2c66c301"}) == "braam"
    assert ep._sound_name({"label": "door slam"}) == "door slam"
    assert ep._sound_name({"storage_key": "sfx/5b0c8ef2f7.flac"}) == "sound effect"


def test_nothing_added_says_nothing():
    same = {"sfx": [_sfx(1, 1.0, "sfx/lib-whoosh_6-5b0c8ef2f7.flac")]}
    assert ep.sounds_added(same, same) == ""
