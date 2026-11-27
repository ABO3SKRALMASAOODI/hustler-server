import story


def test_reference_word_band_does_not_reject_a_complete_short_documentary():
    script = {"chapters": [{"id":"a","turn":"The laboratory closed."},
                           {"id":"b","turn":"A former rival bought their design."}],
              "segments": [{"say":"The laboratory closed.", "chapter":"a","beat":"turn"},
                           {"say":"Their rival bought the design.", "chapter":"b","beat":"turn"}]}
    report = story.check_script(script, {})
    assert report["ok"] and report["words"] == 8
