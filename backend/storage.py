"""
S3-compatible object storage access (default: Cloudflare R2).

Media bytes NEVER pass through this API server: browsers upload directly to
the bucket via presigned URLs minted here, and download via presigned GETs.
Key layout:
    originals/{project_id}/{uuid}.{ext}
    proxies/{project_id}/{sha}.mp4
    audio/{project_id}/{sha}.wav
    thumbs/{project_id}/{sha}/shot_{n}.jpg
    sheets/{project_id}/{sha}/sheet_{n}.jpg
    renders/{project_id}/{preview|final}_v{version}.mp4   (legacy, pre-2026-07-18)
    media/{project_id}/{job_id}-{rand}.mp4                (renders; opaque + unique
                                                           per render — see
                                                           worker/renderer.py)
"""

import os
import uuid

import boto3
from botocore.config import Config

PRESIGN_EXPIRY = 900          # 15 min for UPLOAD presigns
# Playback GETs live much longer: the studio fetches ONE url per asset and the
# user watches/chats across a long session — at 15 min the player's src went
# dead mid-session and every later play/seek was a silent black frame. The
# url is still minted per-request behind the user's own auth; 6h only bounds
# how long a leaked link would work, not who can mint one.
PRESIGN_GET_EXPIRY = int(os.getenv("PRESIGN_GET_EXPIRY", "21600"))
PART_SIZE = 64 * 1024 * 1024  # 64 MB multipart parts (min 5 MB on S3/R2)
SINGLE_PUT_LIMIT = 64 * 1024 * 1024

# Only these get presigned for upload. Everything else is rejected before a
# URL is ever minted.
ALLOWED_VIDEO_EXT = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}
ALLOWED_MUSIC_EXT = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}
ALLOWED_IMAGE_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# Chat attachments are small; only the main video gets the multi-GB budget.
MUSIC_MAX_BYTES = 50 * 1024 * 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024
CLIP_MAX_BYTES = 500 * 1024 * 1024   # clips spliced into the edit


def is_configured():
    return all(os.getenv(k) for k in (
        "S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET"))


def bucket():
    return os.environ["S3_BUCKET"]


def client(public=False):
    """public=True mints URLs the BROWSER will hit. On R2 the two endpoints
    are the same; in docker-compose dev the browser reaches MinIO on
    localhost while containers use the internal hostname, so presigning uses
    S3_PUBLIC_ENDPOINT when set."""
    endpoint = os.environ["S3_ENDPOINT"]
    if public:
        endpoint = os.getenv("S3_PUBLIC_ENDPOINT") or endpoint
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region_name=os.getenv("S3_REGION", "auto"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


# THE MAIN VIDEO'S CAP IS AN ABUSE GUARD, NOT THE PRODUCT LIMIT.
#
# It was 2 GB, which contradicted the 3-hour duration limit it shipped beside:
# 2 GiB over 3 hours is 1.6 Mbps, and even ONE hour only fit under 4.7 Mbps.
# So the advertised "up to 2GB or 3 hours" could not both be true, and an
# ordinary 1-hour 1080p recording (~8-20 Mbps) was refused by a platform whose
# own copy said it took 3-hour videos. What actually costs us is DURATION —
# it drives index time and render time — and that is bounded separately by
# MAX_DURATION_S. Bytes only cost storage and the user's own upload time, so
# this number just has to be big enough to stop being the thing that says no.
#
# 16 GB covers 1 hour of 4K at 35 Mbps, 10 minutes of 4K at 200 Mbps, and an
# hour of 1080p at any bitrate a camera actually produces.
MAX_UPLOAD_GB = float(os.getenv("MAX_UPLOAD_GB", "16"))

# Mirrors worker/config.MAX_DURATION_S. Enforced for real in the indexer (it
# needs a probe); carried here so the API and the studio can refuse a 5-hour
# file BEFORE the user spends 40 minutes uploading it, which is the only point
# at which refusing is a kindness.
MAX_DURATION_S = float(os.getenv("MAX_DURATION_S", str(3 * 3600)))


def max_upload_bytes():
    return int(MAX_UPLOAD_GB * 1024 ** 3)


def _size_label(nbytes):
    gb = nbytes / 1024 ** 3
    return f"{gb:.0f} GB" if gb >= 1 else f"{nbytes / 1024 ** 2:.0f} MB"


def upload_limits():
    """The caps, as the API reports them. ONE source of truth: the studio used
    to carry its own hardcoded 2 GiB literal, so raising the server's cap would
    have changed nothing a user could see."""
    return {
        "max_bytes": max_upload_bytes(),
        "max_bytes_label": _size_label(max_upload_bytes()),
        "max_duration_s": MAX_DURATION_S,
        "clip_max_bytes": CLIP_MAX_BYTES,
        "music_max_bytes": MUSIC_MAX_BYTES,
        "image_max_bytes": IMAGE_MAX_BYTES,
        "video_ext": sorted(ALLOWED_VIDEO_EXT),
        "music_ext": sorted(ALLOWED_MUSIC_EXT),
        "image_ext": sorted(ALLOWED_IMAGE_EXT),
    }


def validate_upload(filename, nbytes, kind):
    """Returns (ext, content_type) or raises ValueError with a user-facing reason."""
    ext = os.path.splitext(filename or "")[1].lower()
    allowed, cap = {
        "music": (ALLOWED_MUSIC_EXT, MUSIC_MAX_BYTES),
        "image": (ALLOWED_IMAGE_EXT, IMAGE_MAX_BYTES),
        "clip": (ALLOWED_VIDEO_EXT, CLIP_MAX_BYTES),
    }.get(kind, (ALLOWED_VIDEO_EXT, max_upload_bytes()))
    if ext not in allowed:
        raise ValueError(f"File type {ext or '(none)'} not supported. "
                         f"Allowed: {', '.join(sorted(allowed))}")
    if not isinstance(nbytes, int) or nbytes <= 0:
        raise ValueError("File size missing or invalid")
    if nbytes > cap:
        raise ValueError(f"File is larger than the {_size_label(cap)} limit "
                         f"for {kind or 'video'} uploads")
    return ext, allowed[ext]


KEY_PREFIX = {"original": "originals", "music": "music", "image": "images",
              "clip": "clips"}


def new_original_key(project_id, ext, kind="original"):
    prefix = KEY_PREFIX.get(kind, "originals")
    return f"{prefix}/{project_id}/{uuid.uuid4().hex[:12]}{ext}"


def presign_upload(key, nbytes, content_type):
    """Single presigned PUT for small files, presigned multipart for large.

    Returns a dict the browser can act on directly.
    """
    c = client(public=True)
    if nbytes <= SINGLE_PUT_LIMIT:
        url = c.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket(), "Key": key, "ContentType": content_type},
            ExpiresIn=PRESIGN_EXPIRY,
        )
        return {"mode": "single", "storage_key": key, "url": url,
                "content_type": content_type}

    mpu = c.create_multipart_upload(
        Bucket=bucket(), Key=key, ContentType=content_type)
    upload_id = mpu["UploadId"]
    n_parts = (nbytes + PART_SIZE - 1) // PART_SIZE
    urls = [
        {
            "part_number": i,
            "url": c.generate_presigned_url(
                "upload_part",
                Params={"Bucket": bucket(), "Key": key,
                        "UploadId": upload_id, "PartNumber": i},
                ExpiresIn=PRESIGN_EXPIRY,
            ),
        }
        for i in range(1, n_parts + 1)
    ]
    return {"mode": "multipart", "storage_key": key, "upload_id": upload_id,
            "part_size": PART_SIZE, "part_urls": urls,
            "content_type": content_type}


def complete_multipart(key, upload_id, parts):
    """parts: [{"part_number": int, "etag": str}] from the browser."""
    ordered = sorted(parts, key=lambda p: int(p["part_number"]))
    client().complete_multipart_upload(
        Bucket=bucket(), Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": [
            {"PartNumber": int(p["part_number"]), "ETag": p["etag"]}
            for p in ordered
        ]},
    )


def abort_multipart(key, upload_id):
    try:
        client().abort_multipart_upload(
            Bucket=bucket(), Key=key, UploadId=upload_id)
    except Exception:
        pass


def head_bytes(key):
    """Size of an object, or None if it doesn't exist."""
    try:
        return client().head_object(Bucket=bucket(), Key=key)["ContentLength"]
    except Exception:
        return None


def get_range(key, nbytes=64):
    """First nbytes of an object (for magic-byte sniffing). None on failure."""
    try:
        obj = client().get_object(
            Bucket=bucket(), Key=key,
            Range=f"bytes=0-{max(0, int(nbytes) - 1)}")
        return obj["Body"].read()
    except Exception:
        return None


def content_matches_kind(head, kind):
    """Best-effort magic-byte check on the first bytes of an upload. Returns
    True when the bytes match the declared kind, False on a CLEAR mismatch
    (e.g. a JPEG renamed to .mp4), and None when undetermined — callers must
    treat None as 'allow', never blocking on ambiguity."""
    b = head or b""
    if len(b) < 12:
        return None
    is_iso = b[4:8] == b"ftyp"                    # mp4/mov/m4a container
    is_riff = b[0:4] == b"RIFF"
    if kind in ("clip", "original", "video"):
        if is_iso or b[0:4] == b"\x1aE\xdf\xa3":  # iso-bmff / matroska-webm
            return True
        if is_riff and b[8:12] == b"AVI ":
            return True
        if b[0:3] == b"FLV" or b[0] == 0x47:      # flv / mpeg-ts
            return True
        # A still image or plain text renamed to a video extension: reject.
        if (b[0:3] == b"\xff\xd8\xff" or b[0:4] == b"\x89PNG"
                or b[0:3] == b"GIF"):
            return False
        return None
    if kind == "image":
        if (b[0:3] == b"\xff\xd8\xff" or b[0:4] == b"\x89PNG"
                or b[0:3] == b"GIF" or (is_riff and b[8:12] == b"WEBP")):
            return True
        return False
    if kind == "music":
        if b[0:3] == b"ID3" or b[0:2] in (
                b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xf1",
                b"\xff\xf9"):                     # mp3 / aac frame sync
            return True
        if (is_riff and b[8:12] == b"WAVE") or b[0:4] == b"OggS" \
                or b[0:4] == b"fLaC" or is_iso:   # wav / ogg / flac / m4a
            return True
        return None                              # audio formats vary — allow
    return None


# Every top-level key prefix a project's objects can live under. Used to wipe
# a project (GDPR/account deletion, storage reclaim) — keys are laid out
# {prefix}/{project_id}/..., so a full wipe iterates each prefix.
# "renders" stays for objects written before 2026-07-18; "media" is where every
# render lands now. A prefix missing here silently orphans bytes on delete.
DELETE_PREFIXES = ("originals", "proxies", "audio", "thumbs", "sheets",
                   "renders", "media", "music", "images", "clips", "generated",
                   "generated_sfx", "generated_video", "fetched")


def delete_project_objects(project_id):
    """Delete every stored object belonging to a project across all prefixes.
    Returns the number of objects deleted. Idempotent (missing keys are a
    no-op) so it is safe to re-run."""
    c = client()
    b = bucket()
    total = 0
    for prefix in DELETE_PREFIXES:
        token = None
        while True:
            kw = {"Bucket": b, "Prefix": f"{prefix}/{project_id}/"}
            if token:
                kw["ContinuationToken"] = token
            resp = c.list_objects_v2(**kw)
            objs = resp.get("Contents") or []
            if objs:
                c.delete_objects(Bucket=b, Delete={
                    "Objects": [{"Key": o["Key"]} for o in objs]})
                total += len(objs)
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
    return total


def presign_get(key, expires=None, download_name=None):
    expires = expires or PRESIGN_GET_EXPIRY
    params = {"Bucket": bucket(), "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return client(public=True).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=expires)
