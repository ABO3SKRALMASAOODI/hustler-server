#!/usr/bin/env python3
"""One-time bucket CORS setup so browsers can PUT/GET directly via presigned
URLs. Run with the same S3_* env vars the backend uses:

    S3_ENDPOINT=... S3_ACCESS_KEY_ID=... S3_SECRET_ACCESS_KEY=... \
    S3_BUCKET=... python scripts/setup_bucket_cors.py

ExposeHeaders MUST include ETag — the browser reads it to complete
multipart uploads.
"""

import os

import boto3
from botocore.config import Config

ALLOWED_ORIGINS = [
    "https://valmera.io",
    "https://www.valmera.io",
    "http://localhost:3000",
]

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
    region_name=os.getenv("S3_REGION", "auto"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)

s3.put_bucket_cors(
    Bucket=os.environ["S3_BUCKET"],
    CORSConfiguration={
        "CORSRules": [{
            "AllowedOrigins": ALLOWED_ORIGINS,
            "AllowedMethods": ["GET", "PUT", "HEAD"],
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": 3600,
        }]
    },
)
print(f"CORS set on {os.environ['S3_BUCKET']} for: {', '.join(ALLOWED_ORIGINS)}")
