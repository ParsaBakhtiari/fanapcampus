"""OBS access through the S3-compatible API (works with Pardis/Huawei OBS and MinIO)."""
from functools import lru_cache

import boto3
from botocore.config import Config

from .config import get_settings


@lru_cache
def obs_client():
    s = get_settings()
    if not (s.obs_endpoint and s.obs_access_key and s.obs_secret_key):
        raise RuntimeError("OBS is not configured (OBS_ENDPOINT / OBS_ACCESS_KEY / OBS_SECRET_KEY)")
    cfg = Config(
        signature_version="s3v4",
        s3={"addressing_style": s.obs_addressing_style},
        # boto3 >= 1.36 sends CRC checksums by default; many S3-compatible
        # stores reject them. Only send checksums when the API requires it.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=3,
        read_timeout=15,
    )
    return boto3.client(
        "s3",
        endpoint_url=s.obs_endpoint,
        region_name=s.obs_region,
        aws_access_key_id=s.obs_access_key,
        aws_secret_access_key=s.obs_secret_key,
        config=cfg,
    )


def put_object(bucket: str, key: str, body: bytes, content_type: str) -> None:
    extra = {"ServerSideEncryption": get_settings().obs_sse} if get_settings().obs_sse else {}
    obs_client().put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type, **extra)


def get_object(bucket: str, key: str) -> bytes:
    return obs_client().get_object(Bucket=bucket, Key=key)["Body"].read()
