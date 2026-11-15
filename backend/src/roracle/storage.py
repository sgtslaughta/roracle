import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class TooLarge(Exception):
    pass


class EmptyFile(Exception):
    pass


@dataclass
class StagedFile:
    path: Path
    sha256: str
    size: int


def stage(stream: BinaryIO, max_bytes: int, chunk: int = 1 << 20) -> StagedFile:
    """Copy stream to a temp file while hashing; caller owns (deletes) the result."""
    h = hashlib.sha256()
    size = 0
    fd, name = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "wb") as out:
            while data := stream.read(chunk):
                size += len(data)
                if size > max_bytes:
                    raise TooLarge(f"upload exceeds {max_bytes} bytes")
                h.update(data)
                out.write(data)
        if size == 0:
            raise EmptyFile("empty upload")
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return StagedFile(Path(name), h.hexdigest(), size)


class BlobStore:
    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    def key(self, sha256: str) -> str:
        return f"blobs/{sha256[:2]}/{sha256}"

    def put_if_absent(self, staged: StagedFile, content_type: str) -> bool:
        key = self.key(staged.sha256)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return False
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                raise
        self.client.upload_file(
            str(staged.path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type, "Metadata": {"sha256": staged.sha256}},
        )
        return True

    def open(self, sha256: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=self.key(sha256))["Body"]


def blob_store_from(settings) -> BlobStore:
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    return BlobStore(client, settings.s3_bucket)
