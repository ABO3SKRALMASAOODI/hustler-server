from PIL import Image

from score import brief, scout


def test_all_six_retained_regions_reach_the_reading_board(monkeypatch):
    colours = [(200, 20, 20), (20, 200, 20), (20, 20, 200),
               (200, 200, 20), (200, 20, 200), (20, 200, 200)]
    calls = []

    def grab(src, at, width):
        calls.append(at)
        return Image.new('RGB', (1280, 720), colours[int(at)])

    monkeypatch.setattr(brief.boards, 'grab', grab)
    per = {'W': 1280, 'text': [
        {'box': [.2, .25, .32, .35], 't': [i, i]}
        for i in range(6)]}
    board = brief.printed_text_board('source.mp4', per)
    assert calls == list(range(6))
    assert board.size == (brief.BOARD_W, brief.BOARD_H)
    for i, colour in enumerate(colours):
        assert board.getpixel(((i % 2) * 784 + 30, 30 + (i // 2) * 283 + 50)) == colour


def test_unavailable_frame_is_not_replaced_with_another_time(monkeypatch):
    monkeypatch.setattr(brief.boards, 'grab', lambda *args, **kwargs: None)
    per = {'W': 1280, 'text': [{'box': [.1, .2, .3, .4], 't': [3, 5]}]}
    board = brief.printed_text_board('source.mp4', per)
    assert board.getpixel((400, 400)) == brief.boards.BG


def test_scout_uses_full_text_board_without_an_extra_image(tmp_path, monkeypatch):
    out = tmp_path / 'brief'
    out.mkdir()
    for name in ('board_1.jpg', 'board_4.jpg', 'printed_text.jpg'):
        (out / name).touch()
    monkeypatch.setattr(scout, 'user_text', lambda *args: 'Transcript')
    monkeypatch.setattr(brief, 'image_block', lambda p: {'image': p})
    content = scout.content(str(tmp_path), {}, {}, {})
    assert len(content) == 3
    assert content[1]['image'].endswith('board_1.jpg')
    assert content[2]['image'].endswith('printed_text.jpg')


def test_scout_can_read_a_preserved_older_brief(tmp_path, monkeypatch):
    out = tmp_path / 'brief'
    out.mkdir()
    (out / 'board_4.jpg').touch()
    monkeypatch.setattr(scout, 'user_text', lambda *args: 'Transcript')
    monkeypatch.setattr(brief, 'image_block', lambda p: {'image': p})
    assert scout.content(str(tmp_path), {}, {}, {})[1]['image'].endswith('board_4.jpg')
