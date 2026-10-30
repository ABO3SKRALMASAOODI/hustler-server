"""Executors and the dispatcher read the next store's keys from the sealed
app_kv row when their env has none (storage_creds.py).

The worker images have no ``cryptography``: the row opens through yt-dlp's
verified pure-Python AES-GCM. VECTOR below was sealed by the backend's
storage_creds.seal (cryptography) with the test secrets here, so a yt-dlp
bump that breaks the fallback fails this file, not production.
"""

import json
import pathlib

import pytest

import sidecars
import storage
import storage_creds as creds
import storage_routing as routing
from s3_fake import FakeS3

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _pristine_storage(monkeypatch):
    """Script-style suites (test_index_lanes, test_round95_image_senses)
    replace storage.download_to & co. at import, for the whole run; bind the
    real functions back, as test_storage_routing does."""
    from test_storage_routing import _REAL
    for name, fn in _REAL.items():
        monkeypatch.setattr(storage, name, fn)

LEGACY_SECRET = "legacy-r2-secret-0123456789abcdef"
EXEC_SECRET = "executor-bearer-secret-fedcba9876543210"
VALUES = {
    "S3_NEXT_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
    "S3_NEXT_REGION": "us-west-004",
    "S3_NEXT_BUCKET": "valmera-media-usw",
    "S3_NEXT_ACCESS_KEY_ID": "004b2keyid000000000000001",
    "S3_NEXT_SECRET_ACCESS_KEY": "K004appKeySecretValueThatMustNeverShow",
}
SECRET_TEXTS = (VALUES["S3_NEXT_SECRET_ACCESS_KEY"],
                VALUES["S3_NEXT_ACCESS_KEY_ID"], LEGACY_SECRET, EXEC_SECRET)
VECTOR = json.dumps({
    "alg": "AES-256-GCM",
    "also": [{
        "ct": "LkBuxtdeOZZalign4IIJHZB9bfzghrgO2jkVZoeMojPSSxEZAyeERrO7a5Lpv"
              "jmFJDmCMzhYZBbbE6agsZmDgtE0+yTEwpi1urQwRA+PHaUEIOxyDzdf01lA9"
              "+pVarM9CATm0c2wB3ZyS7iMzQQevIOaS9CKJV6huvTazbrK1vbQsTDkWG0k+"
              "H4Zx+8nZOBfxwfWtiMThaPeeBmnWpSRBjIXg3HG50hhWaqDVraHO3Tcf69zo"
              "I0K1ssr9J+yfF6Y5GYxgz3zDWHK4hOrdiMvyIBasCahtlJU4xUE3aZXFJCHc"
              "e5NXTFhMyo6L6sqjveQj0KzEnQ8x2T9GDjxmhngUcpDKWtqXetKEqQ=",
        "kek": "REMOTE_EXECUTOR_SECRET", "kid": "8a35b13f140bd526",
        "nonce": "AgICAgICAgICAgIC"}],
    "at": "2026-10-30T00:00:00Z",
    "ct": "bXJ3sTgg6IUj9OK+bRKjIBsgNmY5VCAvMi7ynE5t+zyFdxRU9af4jNKXs4wTAr5d"
          "mGxYSu0+OG6UklysXlD3rW+Nban1IP7d2P/xJfdLJkGk5+B1i3zquTN8dW+WWWq4"
          "p4UFp689ua6kFKqQnpfmxikTPs/PLjVfAWWbd/RWLHp9Htrq9wSvvij2KOCcPtAV"
          "UmBYG/UIajI4kOVt8ZcVarm0aSwSzuuAYpCWK4UbiZF2Imz9ahmGy+j3SZrIfkVS"
          "FvuuO5Iemg3GGWmaJMRIbZGAJCXqR5usfSGjjcUm0HqROFekAPnFBz+2ztK5I82e"
          "atc+JizSs+spDwLtgWEAracJdJakDAXiNmU=",
    "fields": 5, "info": "valmera-storage-next-v1", "kdf": "HKDF-SHA256",
    "kek": "S3_SECRET_ACCESS_KEY", "kid": "e55a5dfaedecc47a",
    "nonce": "AQEBAQEBAQEBAQEB", "v": 1})


def _no_secret_in(text):
    for s in SECRET_TEXTS:
        assert s not in text


def _flip(raw, field="ct", index=0):
    doc = json.loads(raw)
    data = bytearray(creds._unb64(doc[field]))
    data[index] ^= 1
    doc[field] = creds._b64(bytes(data))
    return json.dumps(doc)


def test_worker_and_backend_copies_are_identical():
    assert (ROOT / "worker" / "storage_creds.py").read_bytes() == \
        (ROOT / "backend" / "storage_creds.py").read_bytes(), \
        "edit backend/storage_creds.py and copy it to worker/"


def test_the_backend_sealed_vector_opens_here_under_either_secret():
    values, kek = creds.open_row(VECTOR, {"S3_SECRET_ACCESS_KEY":
                                          LEGACY_SECRET})
    assert values == VALUES and kek == "S3_SECRET_ACCESS_KEY"
    values, kek = creds.open_row(VECTOR, {"S3_SECRET_ACCESS_KEY": "other",
                                          "REMOTE_EXECUTOR_SECRET":
                                          EXEC_SECRET})
    assert values == VALUES and kek == "REMOTE_EXECUTOR_SECRET"
    assert creds.open_row(VECTOR, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET},
                          impl="yt_dlp")[0] == VALUES


def test_the_child_decrypt_opens_the_vector_and_refuses_tampering():
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    assert creds.open_row(VECTOR, env, impl="child")[0] == VALUES
    with pytest.raises(creds.CredentialsError) as e:
        creds.open_row(_flip(VECTOR), env, impl="child")
    assert e.value.reason == "damaged"


def test_an_executor_never_keeps_yt_dlp_resident_for_this():
    """A fresh worker process opens the row without importing yt-dlp
    itself (the ~30 MB stays in a short-lived child)."""
    import subprocess
    import sys
    code = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "import storage_creds as c\n"
        "v, k = c.open_row(sys.stdin.read(), "
        "{'S3_SECRET_ACCESS_KEY': %r})\n"
        "print(json.dumps({'fields': len(v), 'kek': k, "
        "'yt_dlp': 'yt_dlp' in sys.modules, "
        "'crypto': c._aesgcm() is not None}))\n"
    ) % (str(ROOT / "worker"), LEGACY_SECRET)
    r = subprocess.run([sys.executable, "-c", code], input=VECTOR,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    out = json.loads(r.stdout)
    assert out["fields"] == 5 and out["kek"] == "S3_SECRET_ACCESS_KEY"
    if not out["crypto"]:
        assert out["yt_dlp"] is False
    _no_secret_in(r.stdout + r.stderr)


def test_round_trip_when_cryptography_is_installed():
    pytest.importorskip("cryptography")
    row = creds.seal(VALUES, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET})
    assert creds.open_row(row, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET},
                          impl="yt_dlp")[0] == VALUES


@pytest.mark.parametrize("raw,env,reason", [
    (VECTOR, {"S3_SECRET_ACCESS_KEY": "rotated"}, "wrong_key"),
    (VECTOR, {}, "no_secret"),
    (VECTOR, {"DATABASE_URL": "postgresql://reader"}, "no_secret"),
    (None, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}, "missing"),
    ("{nope", {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}, "malformed"),
    (_flip(VECTOR), {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}, "damaged"),
    (_flip(VECTOR, index=-1), {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET},
     "damaged"),
    (json.dumps(dict(json.loads(VECTOR), v=9)),
     {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}, "unsupported"),
])
def test_refusals_are_named_and_quiet(raw, env, reason):
    with pytest.raises(creds.CredentialsError) as e:
        creds.open_row(raw, env, impl="yt_dlp")
    assert e.value.reason == reason
    _no_secret_in(str(e.value))


class Rows:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_env_precedence_and_cache_refresh():
    now = [0.0]
    rows = Rows(VECTOR)
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    c = creds.Credentials(fetch=rows, env=env, clock=lambda: now[0],
                          log=lambda m: None, background=False)
    view = creds.Env(env, c)
    assert routing.store_config("next", view)["bucket"] == \
        "valmera-media-usw"
    assert view.source() == "app_kv" and rows.calls == 1
    now[0] = 299
    view.get("S3_NEXT_BUCKET")
    assert rows.calls == 1
    now[0] = 300
    view.get("S3_NEXT_BUCKET")
    assert rows.calls == 2
    rows.value = _flip(VECTOR)                       # does not decrypt
    now[0] = 600
    assert routing.store_config("next", view) is None
    now[0] = 630                                     # retried in 30 s
    rows.value = VECTOR
    assert routing.store_config("next", view) is not None
    assert c.invalidate() is True                    # an auth refusal
    view.get("S3_NEXT_BUCKET")
    assert rows.calls == 5
    # The env wins: the row is no longer consulted at all.
    full = {**env, **VALUES, "S3_NEXT_BUCKET": "from-env"}
    rows2 = Rows(VECTOR)
    view2 = creds.Env(full, creds.Credentials(fetch=rows2, env=full))
    assert routing.store_config("next", view2)["bucket"] == "from-env"
    assert view2.source() == "env" and rows2.calls == 0


def test_a_refresh_never_stalls_a_storage_call():
    import threading
    now = [0.0]
    gate = threading.Event()
    rows = Rows(VECTOR)

    def fetch():
        if rows.calls:
            gate.wait(5)
        return rows()
    c = creds.Credentials(fetch=fetch, env={"S3_SECRET_ACCESS_KEY":
                                            LEGACY_SECRET},
                          clock=lambda: now[0], log=lambda m: None)
    assert c.values() == VALUES
    now[0] = 301
    assert c.values() == VALUES and c.values() == VALUES
    gate.set()
    for _ in range(200):
        if rows.calls == 2 and not c._lock.locked():
            break
        threading.Event().wait(0.01)
    assert rows.calls == 2


# ── the worker's storage and sidecar stores ────────────────────────────────

@pytest.fixture
def row_only(monkeypatch):
    for k in creds.FIELDS:
        monkeypatch.delenv(k, raising=False)
    for k, v in {"S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
                 "S3_ACCESS_KEY_ID": "legacy-id",
                 "S3_SECRET_ACCESS_KEY": LEGACY_SECRET,
                 "S3_BUCKET": "valmera-media"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("REMOTE_EXECUTOR_SECRET", raising=False)
    rows = Rows(VECTOR)
    original = creds.CREDENTIALS._fetch
    creds.install(rows)
    monkeypatch.setattr(storage, "_router", routing.Router(
        fetch=lambda: "copy", env=creds.ENV, log=lambda m: None))
    storage._locator.clear()
    yield rows
    # Restore the module's own source without starting a read (the env is
    # still this test's until monkeypatch undoes it).
    with creds.CREDENTIALS._lock:
        creds.CREDENTIALS._fetch = original
        creds.CREDENTIALS._reset()
    storage._locator.clear()


def test_executor_storage_uses_the_row(row_only, monkeypatch, capsys):
    assert storage.next_config()["bucket"] == "valmera-media-usw"
    assert storage.store_bucket("next") == "valmera-media-usw"
    client = storage._next_client()
    assert client._request_signer._credentials.access_key == \
        VALUES["S3_NEXT_ACCESS_KEY_ID"]
    st = storage.routing_status()
    assert st["mode"] == "copy" and st["next"] is True
    assert st["next_source"] == "app_kv"
    assert st["next_credentials"]["row"] == "ok"
    assert st["next_host"] == "s3.us-west-004.backblazeb2.com"
    _no_secret_in(json.dumps(st) + capsys.readouterr().out)
    # The sidecar/clip store follows the same keys.
    store = sidecars.R2Store()
    assert store._b("next") == "valmera-media-usw"
    assert store._next_client()._request_signer._credentials.access_key == \
        VALUES["S3_NEXT_ACCESS_KEY_ID"]


def test_copy_mode_reads_through_the_row_keys(row_only, monkeypatch,
                                              tmp_path):
    legacy = FakeS3("legacy", {"proxies/1/a.mp4": b"old"})
    nxt = FakeS3("next", {"proxies/1/a.mp4": b"new"})
    monkeypatch.setattr(storage, "client", lambda: legacy)
    monkeypatch.setattr(storage, "_next_client", lambda: nxt)
    monkeypatch.setattr(storage, "check_workdir_capacity",
                        lambda *a, **k: None)
    import config
    monkeypatch.setattr(config, "S3_BUCKET", "valmera-media")
    dest = tmp_path / "a.mp4"
    storage.download_to("proxies/1/a.mp4", str(dest))
    assert dest.read_bytes() == b"new"


def test_wrong_key_keeps_the_executor_on_the_legacy_store(row_only,
                                                          monkeypatch):
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "rotated-legacy-secret")
    creds.install(row_only)
    assert storage.next_config() is None
    st = storage.routing_status()
    assert st["next"] is False and st["mode"] == "legacy"
    assert st["next_credentials"]["row"] == "wrong_key"
    with pytest.raises(RuntimeError):
        storage._next_client()
    with pytest.raises(RuntimeError):
        sidecars.R2Store()._next_client()


def test_missing_row_is_not_configured_and_never_raises(row_only):
    row_only.value = None
    creds.install(row_only)
    assert storage.next_config() is None
    assert storage.routing_status()["next_credentials"]["row"] == "missing"
    row_only.value = RuntimeError("database unreachable")
    creds.install(row_only)
    assert storage.next_config() is None
    assert storage.routing_status()["next_credentials"]["row"] == \
        "unreadable"
