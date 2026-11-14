from contextvars import ContextVar
from threading import Event, get_ident

import pytest
import renderer


def test_picture_overlaps_sound_and_both_finish_in_original_context():
    context = ContextVar('media-check-context', default=None)
    token = context.set('same-job')
    picture_started, sound_finished = Event(), Event()
    completed = []
    caller = get_ident()
    def picture():
        assert context.get() == 'same-job'
        assert get_ident() != caller
        picture_started.set()
        assert sound_finished.wait(2), 'sound must run beside picture'
        completed.append('picture')
    def sound():
        assert get_ident() == caller
        assert picture_started.wait(2)
        completed.append('sound')
        sound_finished.set()
    try:
        renderer._joined_media_checks(picture, sound)
        assert completed == ['sound', 'picture']
    finally:
        context.reset(token)


@pytest.mark.parametrize('failed_lane', ['picture', 'sound'])
def test_reader_failure_is_raised_only_after_both_readers_join(failed_lane):
    joined, started = Event(), Event()
    def picture():
        started.set()
        assert joined.wait(2)
        if failed_lane == 'picture':
            raise RuntimeError('picture failed')
    def sound():
        assert started.wait(2)
        joined.set()
        if failed_lane == 'sound':
            raise RuntimeError('sound failed')
    with pytest.raises(RuntimeError, match=failed_lane):
        renderer._joined_media_checks(picture, sound)
    assert joined.is_set()
