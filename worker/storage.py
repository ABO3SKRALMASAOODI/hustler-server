"""Worker-side object storage: stream downloads to disk, upload artifacts.
Media bytes only ever live on the worker's ephemeral disk during a job."""

import boto3
from botocore.config import Config

import config


def client():
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.S3_ACCESS_KEY_ID,
        aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
        region_name=config.S3_REGION,
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": "path"},
                      retries={"max_attempts": 3}),
    )


def download_to(key, path):
    client().download_file(config.S3_BUCKET, key, path)


def upload_file(path, key, content_type):
    client().upload_file(path, config.S3_BUCKET, key,
                         ExtraArgs={"ContentType": content_type})


def copy_object(src_key, dst_key):
    client().copy_object(Bucket=config.S3_BUCKET,
                         CopySource={"Bucket": config.S3_BUCKET, "Key": src_key},
                         Key=dst_key)


def exists(key):
    try:
        client().head_object(Bucket=config.S3_BUCKET, Key=key)
        return True
    except Exception:
        return False
