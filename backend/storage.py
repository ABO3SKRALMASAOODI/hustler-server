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
    renders/{project_id}/{preview|final}_v{version}.mp4
"""

import os
import uuid

import boto3
from botocore.config import Config

PRESIGN_EXPIRY = 900          # 15 min max, per security guardrails
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


def max_upload_bytes():
    return int(float(os.getenv("MAX_UPLOAD_GB", "2")) * 1024 ** 3)


def validate_upload(filename, nbytes, kind):
    """Returns (ext, content_type) or raises ValueError with a user-facing reason."""
    ext = os.path.splitext(filename or "")[1].lower()
    allowed, cap, cap_label = {
        "music": (ALLOWED_MUSIC_EXT, MUSIC_MAX_BYTES, "50 MB"),
        "image": (ALLOWED_IMAGE_EXT, IMAGE_MAX_BYTES, "10 MB"),
        "clip": (ALLOWED_VIDEO_EXT, CLIP_MAX_BYTES, "500 MB"),
    }.get(kind, (ALLOWED_VIDEO_EXT, max_upload_bytes(),
                 f"{os.getenv('MAX_UPLOAD_GB', '2')} GB"))
    if ext not in allowed:
        raise ValueError(f"File type {ext or '(none)'} not supported. "
                         f"Allowed: {', '.join(sorted(allowed))}")
    if not isinstance(nbytes, int) or nbytes <= 0:
        raise ValueError("File size missing or invalid")
    if nbytes > cap:
        raise ValueError(f"File is larger than the {cap_label} limit "
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


def presign_get(key, expires=PRESIGN_EXPIRY, download_name=None):
    params = {"Bucket": bucket(), "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return client(public=True).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=expires)
