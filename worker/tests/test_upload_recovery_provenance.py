"""Retry an upload with only its durable asset reference, as /state does."""
import pytest

import indexer


@pytest.mark.parametrize("mode,original_exists,expected_key,copy_expected", [
    ("dedup_src", False, "originals/2/main.mp4", True),
    ("dedup_src", True, "originals/2/main.mp4", False),
    ("client_proxy_key", False, "proxies/2/browser.mp4", False),
    ("client_proxy_key", True, "originals/2/main.mp4", False),
])
def test_self_heal_recovers_source_and_keeps_existing_original_authoritative(
        monkeypatch, tmp_path, mode, original_exists, expected_key, copy_expected):
    original = "originals/2/main.mp4"
    source = ("originals/1/source.mp4" if mode == "dedup_src"
              else "proxies/2/browser.mp4")
    asset = {"id": 3, "kind": "original", "storage_key": original,
             "meta": {mode: source, "upload_state": "pending"}}
    calls, copied, downloads = [], [], []

    class Db:
        def run(self, fn, *args):
            calls.append((fn, args))
            if fn is indexer.dbx.get_asset:
                return asset
            if fn is indexer.dbx.get_project:
                return {"chat_session_id": 5}
            return True

    class DownloadReached(Exception):
        pass

    def download(key, path):
        downloads.append(key)
        raise DownloadReached()

    monkeypatch.setattr(indexer.config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(indexer.storage, "exists",
                        lambda key: key != original or original_exists or bool(copied))
    monkeypatch.setattr(indexer.storage, "copy_object",
                        lambda src, dst: copied.append((src, dst)))
    monkeypatch.setattr(indexer.storage, "download_to", download)
    job = {"id": 7, "project_id": 2, "user_id": 1,
           "payload": {"asset_id": 3, "reindex": False}}
    with pytest.raises(DownloadReached):
        indexer.run_index_job(Db(), job)
    assert downloads == [expected_key]
    assert copied == ([(source, original)] if copy_expected else [])
    assert any(fn is indexer.dbx.asset_upload_ready for fn, _ in calls) == (
        mode == "dedup_src")
