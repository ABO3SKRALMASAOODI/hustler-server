"""Picture-review cache identity excludes only its execution concurrency.

Keep the original checker bytes as the compatibility identity. Prompts, frame
sampling, source handling and verdict interpretation all remain in the key.
This avoids spending subscription calls again for a scheduling-only change.
"""
from pathlib import Path


def checker_bytes(source=None):
    text = source if source is not None else Path(__file__).with_name('picture_check.py').read_text()
    text = text.replace('\nimport os\n', '\n')
    text = text.replace("max_workers=1 if os.environ.get('CODEX_PROXY_LOCAL')=='1' else 3", 'max_workers=3')
    return text.encode()
