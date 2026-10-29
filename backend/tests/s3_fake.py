"""An in-memory S3 bucket speaking the boto3 client calls the product makes.

Presigned URLs are ``fake://<store>/<key>?op=<op>&...`` and ``FakeHttp``
serves them, so code that hands a URL to a browser or ffmpeg can be tested
end to end without a network.
"""

import datetime as dt
import hashlib
import io
import itertools
from urllib.parse import parse_qs, quote, unquote, urlparse

_ids = itertools.count(1)


class ClientError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"{code}: {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


class _Body(io.BytesIO):
    def iter_chunks(self, chunk_size=1024):
        while True:
            b = self.read(chunk_size)
            if not b:
                return
            yield b


class FakeS3:
    def __init__(self, name="s3", objects=None, now=None):
        self.name = name
        self.objects = {}           # key -> dict(data, ct, meta, mtime, ...)
        self.uploads = {}           # upload_id -> dict(key, parts, ct, meta)
        self.calls = []
        self.cors = None
        self.fail = {}              # op -> exception to raise once/always
        self.corrupt_writes = False
        self.clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
        for k, v in (objects or {}).items():
            self._store(k, v)

    # -- helpers ------------------------------------------------------------
    def _store(self, key, data, ct="application/octet-stream", meta=None,
               extra=None):
        if self.corrupt_writes and data:
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        self.objects[key] = {"data": bytes(data), "ct": ct,
                             "meta": dict(meta or {}),
                             "mtime": self.clock(),
                             "etag": hashlib.md5(data).hexdigest(),  # noqa
                             "extra": dict(extra or {})}

    def _call(self, op, **kw):
        self.calls.append((op, kw.get("Key")))
        f = self.fail.get(op)
        if f is not None:
            raise f

    def _get(self, key):
        o = self.objects.get(key)
        if o is None:
            raise ClientError("NoSuchKey", key)
        return o

    # -- objects ------------------------------------------------------------
    def head_object(self, Bucket, Key):
        self._call("head_object", Key=Key)
        o = self.objects.get(Key)
        if o is None:
            raise ClientError("404", "Not Found")
        out = {"ContentLength": len(o["data"]), "ContentType": o["ct"],
               "ETag": f'"{o["etag"]}"', "LastModified": o["mtime"],
               "Metadata": dict(o["meta"])}
        out.update(o["extra"])
        return out

    def get_object(self, Bucket, Key, Range=None):
        self._call("get_object", Key=Key)
        o = self._get(Key)
        data = o["data"]
        if Range:
            a, b = Range.split("=", 1)[1].split("-")
            data = data[int(a):int(b) + 1]
        out = {"Body": _Body(data), "ContentLength": len(data),
               "ContentType": o["ct"], "ETag": f'"{o["etag"]}"',
               "Metadata": dict(o["meta"]), "LastModified": o["mtime"]}
        out.update(o["extra"])
        return out

    def put_object(self, Bucket, Key, Body=b"", ContentType=None,
                   Metadata=None, **extra):
        self._call("put_object", Key=Key)
        data = Body.read() if hasattr(Body, "read") else bytes(Body)
        self._store(Key, data, ContentType or "application/octet-stream",
                    Metadata, {k: v for k, v in extra.items()
                               if k in ("ContentEncoding",
                                        "ContentDisposition",
                                        "CacheControl")})
        return {"ETag": f'"{self.objects[Key]["etag"]}"'}

    def delete_object(self, Bucket, Key):
        self._call("delete_object", Key=Key)
        self.objects.pop(Key, None)
        return {}

    def delete_objects(self, Bucket, Delete):
        self._call("delete_objects")
        keys = [o["Key"] for o in Delete["Objects"]]
        for k in keys:
            self.objects.pop(k, None)
        return {"Deleted": [{"Key": k} for k in keys], "Errors": []}

    def copy_object(self, Bucket, Key, CopySource, **kw):
        self._call("copy_object", Key=Key)
        o = self._get(CopySource["Key"])
        self._store(Key, o["data"], o["ct"], o["meta"])
        return {}

    def copy(self, CopySource, Bucket, Key, Config=None, **kw):
        self._call("copy", Key=Key)
        o = self._get(CopySource["Key"])
        self._store(Key, o["data"], o["ct"], o["meta"])

    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=1000,
                        ContinuationToken=None, **kw):
        self._call("list_objects_v2")
        keys = sorted(k for k in self.objects if k.startswith(Prefix or ""))
        start = int(ContinuationToken or 0)
        page = keys[start:start + MaxKeys]
        more = start + MaxKeys < len(keys)
        out = {"Contents": [{"Key": k, "Size": len(self.objects[k]["data"]),
                             "ETag": f'"{self.objects[k]["etag"]}"',
                             "LastModified": self.objects[k]["mtime"]}
                            for k in page],
               "IsTruncated": more}
        if more:
            out["NextContinuationToken"] = str(start + MaxKeys)
        return out

    # -- managed transfers ---------------------------------------------------
    def upload_file(self, path, Bucket, Key, ExtraArgs=None, Config=None):
        self._call("upload_file", Key=Key)
        with open(path, "rb") as f:
            self._store(Key, f.read(),
                        (ExtraArgs or {}).get("ContentType")
                        or "application/octet-stream")

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None,
                       Config=None):
        self._call("upload_fileobj", Key=Key)
        self._store(Key, Fileobj.read(),
                    (ExtraArgs or {}).get("ContentType")
                    or "application/octet-stream")

    def download_file(self, Bucket, Key, Filename, Config=None):
        self._call("download_file", Key=Key)
        o = self.objects.get(Key)
        if o is None:
            raise ClientError("404", "Not Found")
        with open(Filename, "wb") as f:
            f.write(o["data"])

    # -- multipart -----------------------------------------------------------
    def create_multipart_upload(self, Bucket, Key, ContentType=None,
                                Metadata=None, **extra):
        self._call("create_multipart_upload", Key=Key)
        uid = f"{self.name}-up-{next(_ids)}"
        self.uploads[uid] = {"key": Key, "parts": {}, "ct": ContentType,
                             "meta": Metadata or {}, "extra": extra,
                             "initiated": self.clock()}
        return {"UploadId": uid}

    def _upload(self, key, uid):
        up = self.uploads.get(uid)
        if up is None or up["key"] != key:
            raise ClientError("NoSuchUpload", uid)
        return up

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        self._call("upload_part", Key=Key)
        up = self._upload(Key, UploadId)
        data = Body.read() if hasattr(Body, "read") else bytes(Body)
        etag = hashlib.md5(data).hexdigest()  # noqa: S324
        up["parts"][int(PartNumber)] = (data, etag)
        return {"ETag": f'"{etag}"'}

    def list_parts(self, Bucket, Key, UploadId, MaxParts=1000,
                   PartNumberMarker=None):
        self._call("list_parts", Key=Key)
        up = self._upload(Key, UploadId)
        return {"Parts": [{"PartNumber": n, "ETag": f'"{e}"',
                           "Size": len(d)}
                          for n, (d, e) in sorted(up["parts"].items())],
                "IsTruncated": False}

    def list_multipart_uploads(self, Bucket, Prefix=""):
        self._call("list_multipart_uploads")
        return {"Uploads": [{"Key": u["key"], "UploadId": uid,
                             "Initiated": u["initiated"]}
                            for uid, u in self.uploads.items()
                            if u["key"].startswith(Prefix)]}

    def complete_multipart_upload(self, Bucket, Key, UploadId,
                                  MultipartUpload):
        self._call("complete_multipart_upload", Key=Key)
        up = self._upload(Key, UploadId)
        data = b""
        for p in MultipartUpload["Parts"]:
            got = up["parts"].get(int(p["PartNumber"]))
            if got is None or got[1] != str(p["ETag"]).strip('"'):
                raise ClientError("InvalidPart", str(p["PartNumber"]))
            data += got[0]
        del self.uploads[UploadId]
        self._store(Key, data, up["ct"] or "application/octet-stream",
                    up["meta"], {k: v for k, v in up["extra"].items()
                                 if k in ("ContentEncoding",
                                          "ContentDisposition",
                                          "CacheControl")})
        return {}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self._call("abort_multipart_upload", Key=Key)
        self.uploads.pop(UploadId, None)
        return {}

    # -- bucket settings and presigning ---------------------------------------
    def put_bucket_cors(self, Bucket, CORSConfiguration):
        self._call("put_bucket_cors")
        self.cors = CORSConfiguration

    def get_bucket_cors(self, Bucket):
        return dict(self.cors or {})

    def generate_presigned_url(self, op, Params=None, ExpiresIn=3600):
        self.calls.append(("presign:" + op, (Params or {}).get("Key")))
        p = dict(Params or {})
        q = "&".join(f"{k}={quote(str(v))}" for k, v in sorted(p.items())
                     if k not in ("Bucket", "Key"))
        return f"fake://{self.name}/{quote(p.get('Key', ''))}?op={op}&{q}"


class FakeHttp:
    """Serves fake:// presigned URLs for one or more FakeS3 stores."""

    def __init__(self, *stores, cors_origins=None):
        self.stores = {s.name: s for s in stores}

    def __call__(self, method, url, data=None, headers=None, timeout=60):
        u = urlparse(url)
        s3 = self.stores[u.netloc]
        key = unquote(u.path.lstrip("/"))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        op = q.get("op")
        hdrs = {}
        origin = (headers or {}).get("Origin")
        rules = ((s3.cors or {}).get("CORSRules") or [])
        rule = next((r for r in rules if origin in r["AllowedOrigins"]),
                    None) if origin else None
        if rule:
            hdrs["Access-Control-Allow-Origin"] = origin
            hdrs["Access-Control-Expose-Headers"] = ", ".join(
                rule.get("ExposeHeaders") or [])
        if method == "OPTIONS":
            return (200 if rule else 403), hdrs, b""
        try:
            if op == "get_object" and method == "GET":
                rng = (headers or {}).get("Range")
                o = s3.get_object(Bucket="b", Key=key, Range=rng)
                if q.get("ResponseContentDisposition"):
                    hdrs["Content-Disposition"] = q[
                        "ResponseContentDisposition"]
                return (206 if rng else 200), hdrs, o["Body"].read()
            if op == "put_object" and method == "PUT":
                r = s3.put_object(Bucket="b", Key=key, Body=data,
                                  ContentType=(headers or {}).get(
                                      "Content-Type"))
                hdrs["ETag"] = r["ETag"]
                return 200, hdrs, b""
            if op == "upload_part" and method == "PUT":
                r = s3.upload_part(Bucket="b", Key=key,
                                   UploadId=q["UploadId"],
                                   PartNumber=int(q["PartNumber"]),
                                   Body=data)
                hdrs["ETag"] = r["ETag"]
                return 200, hdrs, b""
        except ClientError as e:
            return 404, hdrs, str(e).encode()
        return 400, hdrs, b"unsupported"
