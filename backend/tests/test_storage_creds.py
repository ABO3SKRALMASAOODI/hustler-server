"""The next store's keys from one sealed app_kv row (storage_creds.py).

Pins: the env wins; the row round-trips under either sealing secret; a wrong
key, a damaged row or no row leave the next store "not configured" without
raising; nothing secret is ever logged or reported; the 5-minute cache,
the quicker retry after a failed decrypt and the re-read on an S3
authentication refusal; and the backend storage module using the row.
"""

import json
import pathlib

import pytest

import storage
import storage_creds as creds
import storage_routing as routing

ROOT = pathlib.Path(__file__).resolve().parents[2]

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


def _sealed(**kw):
    secrets = kw.pop("secrets", {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET,
                                 "REMOTE_EXECUTOR_SECRET": EXEC_SECRET})
    return creds.seal(kw.pop("values", VALUES), secrets,
                      now="2026-10-30T00:00:00Z")


def _no_secret_in(text):
    for s in SECRET_TEXTS:
        assert s not in text


def test_backend_and_worker_copies_are_identical():
    other = ROOT / "worker" / "storage_creds.py"
    if not other.exists():
        pytest.skip("worker/storage_creds.py ships with the next executor "
                    "release")
    assert (ROOT / "backend" / "storage_creds.py").read_bytes() == \
        other.read_bytes(), "edit backend/storage_creds.py, copy to worker/"


def test_hkdf_matches_rfc5869_case_1():
    out = creds.hkdf_sha256(bytes.fromhex("0b" * 22),
                            bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"), 42,
                            bytes.fromhex("000102030405060708090a0b0c"))
    assert out.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
        "34007208d5b887185865")


def test_round_trip_under_either_sealing_secret():
    row = _sealed()
    doc = json.loads(row)
    assert (doc["v"], doc["alg"], doc["kdf"], doc["info"]) == (
        1, "AES-256-GCM", "HKDF-SHA256", "valmera-storage-next-v1")
    assert doc["kek"] == "S3_SECRET_ACCESS_KEY" and doc["fields"] == 5
    assert [e["kek"] for e in doc["also"]] == ["REMOTE_EXECUTOR_SECRET"]
    _no_secret_in(row)
    values, kek = creds.open_row(row, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET})
    assert values == VALUES and kek == "S3_SECRET_ACCESS_KEY"
    # The web service has no executor secret; executors may hold another
    # legacy secret than the sealer: each opens its own entry.
    values, kek = creds.open_row(row, {"S3_SECRET_ACCESS_KEY": "other",
                                       "REMOTE_EXECUTOR_SECRET": EXEC_SECRET})
    assert values == VALUES and kek == "REMOTE_EXECUTOR_SECRET"
    # Whitespace around a secret (an env file's line end) does not matter.
    assert creds.open_row(row, {"S3_SECRET_ACCESS_KEY":
                                f" {LEGACY_SECRET}\n"})[0] == VALUES
    # Two seals of the same values never share a nonce or ciphertext.
    assert json.loads(_sealed())["ct"] != doc["ct"]


def test_seal_refuses_incomplete_values_and_unknown_secrets():
    partial = dict(VALUES)
    partial.pop("S3_NEXT_BUCKET")
    with pytest.raises(ValueError) as e:
        creds.seal(partial, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET})
    assert "S3_NEXT_BUCKET" in str(e.value)
    with pytest.raises(ValueError):
        creds.seal(VALUES, {"DATABASE_URL": "postgresql://x"})
    with pytest.raises(ValueError):
        creds.seal(VALUES, {})
    # Only S3_NEXT_* names are ever sealed (never S3_SETUP_*).
    row = creds.seal({**VALUES, "S3_SETUP_SECRET_ACCESS_KEY": "setup"},
                     {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET})
    values, _ = creds.open_row(row, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET})
    assert "S3_SETUP_SECRET_ACCESS_KEY" not in values


@pytest.mark.parametrize("env,reason", [
    ({"S3_SECRET_ACCESS_KEY": "not-the-sealing-secret"}, "wrong_key"),
    ({"REMOTE_EXECUTOR_SECRET": "nope", "S3_SECRET_ACCESS_KEY": "nope"},
     "wrong_key"),
    ({}, "no_secret"),
    ({"DATABASE_URL": "postgresql://reader"}, "no_secret"),
])
def test_wrong_or_missing_key_is_refused(env, reason):
    with pytest.raises(creds.CredentialsError) as e:
        creds.open_row(_sealed(), env)
    assert e.value.reason == reason
    _no_secret_in(str(e.value))


def test_damaged_unknown_and_missing_rows_are_refused():
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    doc = json.loads(_sealed(secrets={"S3_SECRET_ACCESS_KEY":
                                      LEGACY_SECRET}))
    ct = bytearray(creds._unb64(doc["ct"]))
    ct[0] ^= 1
    tampered = dict(doc, ct=creds._b64(bytes(ct)))
    cases = [
        (None, "missing"), ("{not json", "malformed"), ("[1]", "malformed"),
        (json.dumps(dict(doc, v=2)), "unsupported"),
        (json.dumps(dict(doc, alg="AES-128-CBC")), "unsupported"),
        (json.dumps(dict(doc, info="something-else")), "unsupported"),
        (json.dumps(tampered), "damaged"),
        (json.dumps(dict(doc, nonce="!!")), "damaged"),
        (json.dumps(dict(doc, kek="DATABASE_URL")), "no_secret"),
    ]
    for raw, reason in cases:
        with pytest.raises(creds.CredentialsError) as e:
            creds.open_row(raw, env)
        assert e.value.reason == reason, raw


def test_yt_dlp_fallback_opens_a_cryptography_sealed_row():
    pytest.importorskip("yt_dlp.aes")
    row = _sealed()
    values, _ = creds.open_row(row, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET},
                               impl="yt_dlp")
    assert values == VALUES
    with pytest.raises(creds.CredentialsError) as e:
        doc = json.loads(row)
        ct = bytearray(creds._unb64(doc["ct"]))
        ct[-1] ^= 1
        creds.open_row(json.dumps(dict(doc, ct=creds._b64(bytes(ct)))),
                       {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET},
                       impl="yt_dlp")
    assert e.value.reason == "damaged"


def test_child_decrypt_without_yt_dlp_is_quietly_no_aes():
    row = _sealed()
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    try:
        import yt_dlp.aes  # noqa: F401
    except ImportError:
        with pytest.raises(creds.CredentialsError) as e:
            creds.open_row(row, env, impl="child")
        assert e.value.reason == "no_aes"
    else:
        assert creds.open_row(row, env, impl="child")[0] == VALUES


def test_an_unchanged_row_is_decrypted_once(monkeypatch):
    calls = []
    real = creds._decrypt
    monkeypatch.setattr(creds, "_decrypt",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    now = [0.0]
    rows = Rows(_sealed())
    c = _cache(rows, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}, now)
    for t in (0, 300, 600, 900):
        now[0] = t
        assert c.values() == VALUES
    assert rows.calls == 4 and len(calls) == 1
    rows.value = _sealed(secrets={"S3_SECRET_ACCESS_KEY": "other"})
    for t in (1200, 1230, 1260):                 # wrong key: one attempt
        now[0] = t
        assert c.values() is None
    assert rows.calls == 7 and len(calls) == 1   # kid mismatch: no decrypt
    rows.value = _sealed()
    now[0] = 1290
    assert c.values() == VALUES and len(calls) == 2


# ── the cache ───────────────────────────────────────────────────────────────

class Rows:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def _cache(rows, env, now, logs=None):
    return creds.Credentials(fetch=rows, env=env, clock=lambda: now[0],
                             log=(logs.append if logs is not None
                                  else (lambda m: None)), background=False)


def test_env_wins_and_the_row_is_never_read():
    rows = Rows(_sealed())
    base = {**VALUES, "S3_NEXT_SECRET_ACCESS_KEY": "env-secret",
            "S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    view = creds.Env(base, _cache(rows, base, [0.0]))
    cfg = routing.store_config("next", view)
    assert cfg["secret_access_key"] == "env-secret"
    assert view.source() == "env" and rows.calls == 0


def test_row_fills_an_env_without_the_next_store():
    rows = Rows(_sealed())
    base = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET,
            "S3_NEXT_REGION": "ignored-partial-env"}
    view = creds.Env(base, _cache(rows, base, [0.0]))
    cfg = routing.store_config("next", view)
    assert cfg["bucket"] == "valmera-media-usw"
    assert cfg["region"] == "us-west-004"         # the row's set, whole
    assert cfg["access_key_id"] == VALUES["S3_NEXT_ACCESS_KEY_ID"]
    assert view.source() == "app_kv"
    assert view.get("S3_NEXT_PUBLIC_ENDPOINT") is None
    assert view.get("S3_SECRET_ACCESS_KEY") == LEGACY_SECRET


def test_missing_row_or_wrong_key_means_not_configured():
    for raw, env, reason in [
            (None, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}, "missing"),
            (_sealed(), {"S3_SECRET_ACCESS_KEY": "rotated"}, "wrong_key"),
            ("garbage", {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET},
             "malformed")]:
        c = _cache(Rows(raw), env, [0.0])
        view = creds.Env(env, c)
        assert routing.store_config("next", view) is None
        assert view.source() is None and c.status()["row"] == reason
        r = routing.Router(fetch=lambda: "next", env=view,
                           log=lambda m: None)
        assert r.mode() == "legacy" and r.status()["next"] is False


def test_no_sealing_secret_never_reads_the_database():
    rows = Rows(_sealed())
    c = _cache(rows, {}, [0.0])
    assert c.values() is None and rows.calls == 0
    assert c.status()["row"] == "no_secret"


def test_cache_ttl_retry_and_last_good_keys():
    now = [0.0]
    rows = Rows(_sealed())
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    c = _cache(rows, env, now)
    assert c.values() == VALUES and rows.calls == 1
    now[0] = 299
    assert c.values() == VALUES and rows.calls == 1      # cached 5 minutes
    now[0] = 300
    assert c.values() == VALUES and rows.calls == 2
    # A database blip keeps the last good keys and retries in 30 s.
    rows.value = RuntimeError("db down")
    now[0] = 600
    assert c.values() == VALUES and rows.calls == 3
    now[0] = 629
    assert c.values() == VALUES and rows.calls == 3
    now[0] = 630
    assert c.values() == VALUES and rows.calls == 4
    # A row that no longer decrypts: not configured, retried in 30 s.
    rows.value = _sealed(secrets={"S3_SECRET_ACCESS_KEY": "a-new-secret"})
    now[0] = 660
    assert c.values() is None and c.status()["row"] == "wrong_key"
    now[0] = 689
    assert c.values() is None and rows.calls == 5
    rows.value = _sealed()                       # fixed: picked up quickly
    now[0] = 690
    assert c.values() == VALUES and rows.calls == 6
    # The row deleted: not configured (the operator's revoke).
    rows.value = None
    now[0] = 990
    assert c.values() is None and c.status()["row"] == "missing"


def test_only_the_first_read_waits_for_the_database():
    import threading
    now = [0.0]
    gate = threading.Event()
    rows = Rows(_sealed())

    def fetch():
        if rows.calls:                      # every refresh after the first
            gate.wait(5)
        return rows()
    c = creds.Credentials(fetch=fetch, env={"S3_SECRET_ACCESS_KEY":
                                            LEGACY_SECRET},
                          clock=lambda: now[0], log=lambda m: None)
    assert c.values() == VALUES and rows.calls == 1
    now[0] = 301
    # The refresh is stuck on the database; calls keep the cached keys.
    assert c.values() == VALUES
    assert c.values() == VALUES
    gate.set()
    for _ in range(200):
        if rows.calls == 2 and not c._lock.locked():
            break
        threading.Event().wait(0.01)
    assert rows.calls == 2                  # exactly one refresh ran
    assert c.values() == VALUES and rows.calls == 2


def test_an_auth_refusal_rereads_the_row_at_most_every_30_s():
    now = [0.0]
    rows = Rows(_sealed())
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    c = _cache(rows, env, now)
    assert c.values() == VALUES and rows.calls == 1
    assert c.invalidate() is True
    assert c.values() == VALUES and rows.calls == 2
    now[0] = 10
    assert c.invalidate() is False                  # rate limited
    assert c.values() == VALUES and rows.calls == 2
    now[0] = 31
    assert c.invalidate() is True
    c.values()
    assert rows.calls == 3


def test_botocore_auth_errors_reach_the_cache(monkeypatch):
    import boto3
    from botocore.stub import Stubber
    now = [0.0]
    rows = Rows(_sealed())
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}
    c = _cache(rows, env, now)
    monkeypatch.setattr(creds, "CREDENTIALS", c)
    monkeypatch.setattr(creds, "ENV", creds.Env(env, c))
    assert c.values() == VALUES and rows.calls == 1
    client = creds.watch_client(boto3.client(
        "s3", endpoint_url="https://s3.us-west-004.backblazeb2.com",
        aws_access_key_id="x", aws_secret_access_key="y",
        region_name="us-west-004"))
    stub = Stubber(client)
    stub.add_client_error("head_object", service_error_code="NoSuchKey",
                          http_status_code=404)
    stub.add_client_error("head_object",
                          service_error_code="InvalidAccessKeyId",
                          http_status_code=403)
    stub.activate()
    with pytest.raises(Exception):
        client.head_object(Bucket="b", Key="k")
    c.values()
    assert rows.calls == 1                 # a miss is not an auth problem
    with pytest.raises(Exception):
        client.head_object(Bucket="b", Key="k")
    c.values()
    assert rows.calls == 2                 # the refusal re-read the row


def test_nothing_secret_is_ever_logged_or_reported(capsys):
    logs = []
    now = [0.0]
    env = {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET,
           "REMOTE_EXECUTOR_SECRET": EXEC_SECRET}
    rows = Rows(_sealed())
    c = _cache(rows, env, now, logs)
    view = creds.Env(env, c)
    r = routing.Router(fetch=lambda: "copy", env=view, log=logs.append)
    seen = [json.dumps(r.status()), json.dumps(c.status())]
    for value in (RuntimeError("password=" + LEGACY_SECRET), "garbage",
                  _sealed(secrets={"S3_SECRET_ACCESS_KEY": "x"}), None,
                  _sealed()):
        rows.value = value
        now[0] += 400
        c.values()
        seen.append(json.dumps(c.status()))
    assert logs, "the transitions are reported"
    out = capsys.readouterr()
    _no_secret_in("\n".join(logs + seen) + out.out + out.err)


# ── the backend storage module ──────────────────────────────────────────────

@pytest.fixture
def row_only(monkeypatch):
    """This process: legacy keys in env, no S3_NEXT_*, the sealed row."""
    for k in creds.FIELDS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", LEGACY_SECRET)
    monkeypatch.setenv("S3_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "legacy-id")
    monkeypatch.setenv("S3_BUCKET", "valmera-media")
    rows = Rows(_sealed())
    original = creds.CREDENTIALS._fetch
    creds.install(rows)
    monkeypatch.setattr(storage, "_router", routing.Router(
        fetch=lambda: "copy", env=creds.ENV, log=lambda m: None))
    yield rows
    creds.install(original)


def test_backend_storage_uses_the_row(row_only):
    cfg = storage.next_config()
    assert cfg["bucket"] == "valmera-media-usw"
    assert storage.store_bucket("next") == "valmera-media-usw"
    client = storage._next_client()
    got = client._request_signer._credentials
    assert got.access_key == VALUES["S3_NEXT_ACCESS_KEY_ID"]
    assert client.meta.region_name == "us-west-004"
    st = storage.routing_status()
    assert st["mode"] == "copy" and st["next"] is True
    assert st["next_source"] == "app_kv"
    assert st["next_credentials"]["row"] == "ok"
    assert st["next_credentials"]["kek"] == "S3_SECRET_ACCESS_KEY"
    assert st["next_host"] == "s3.us-west-004.backblazeb2.com"
    _no_secret_in(json.dumps(st))
    assert not any("secret" in k for k in st)


def test_backend_storage_wrong_key_is_quietly_not_configured(row_only,
                                                             monkeypatch):
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "rotated-legacy-secret")
    creds.install(row_only)
    assert storage.next_config() is None
    st = storage.routing_status()
    assert st["next"] is False and st["mode"] == "legacy"
    assert st["requested"] == "legacy" and st["next_source"] is None
    assert st["next_credentials"]["row"] == "wrong_key"
    with pytest.raises(RuntimeError):
        storage._next_client()
