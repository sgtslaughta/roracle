"""S3 source: lists `prefix` and streams objects. Endpoints are admin-chosen (not SSRF-filtered, R4)."""

import mimetypes
from collections.abc import Iterator

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from roracle.connectors import Item


def client(config: dict, timeout: int):
    keyed = bool(config.get("access_key") and config.get("secret_key"))
    return boto3.client(
        "s3",
        endpoint_url=config["endpoint"],
        aws_access_key_id=config.get("access_key") if keyed else None,
        aws_secret_access_key=config.get("secret_key") if keyed else None,
        region_name=config.get("region") or "us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 2},
            **({} if keyed else {"signature_version": UNSIGNED}),
        ),
    )


def items(config: dict, timeout: int) -> Iterator[Item]:
    return _list(client(config, timeout), config["bucket"], config.get("prefix", ""))


def _list(c, bucket: str, prefix: str) -> Iterator[Item]:
    for page in c.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            key = o["Key"]
            if key.endswith("/"):
                continue
            name = key.rsplit("/", 1)[-1]
            yield Item(
                key=key,
                etag=o.get("ETag", "").strip('"'),
                filename=name,
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                size=o.get("Size"),
                open=lambda k=key: c.get_object(Bucket=bucket, Key=k)["Body"],
            )
