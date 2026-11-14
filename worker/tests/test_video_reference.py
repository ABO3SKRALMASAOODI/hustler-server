import base64
from PIL import Image
import pytest
import videogen


def test_private_reference_is_exact_image_bytes_not_unreachable_url(tmp_path):
    path = tmp_path / 'reference.png'
    Image.new('RGB', (24, 24), (30, 90, 140)).save(path)
    value = videogen.reference_image(str(path))
    assert value.startswith('data:image/png;base64,')
    assert base64.b64decode(value.split(',', 1)[1]) == path.read_bytes()


def test_non_image_cannot_be_sent_as_reference(tmp_path):
    path = tmp_path / 'bad.jpg'
    path.write_text('<html>not an image</html>')
    with pytest.raises(Exception):
        videogen.reference_image(str(path))
