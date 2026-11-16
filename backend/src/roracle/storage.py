import hashlib
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.config import Config

log = logging.getLogger(__name__)


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


PART = 8 << 20  # S3 multipart part size (min 5 MiB except the last)


class TmpUpload:
    """Streamed upload to `tmp/<uuid>`: hashes as it goes, buffers up to PART, then S3 multipart.

    `feed` does no I/O (safe in a parser callback); call `flush` (blocking) while `ready`."""

    def __init__(self, store: "BlobStore", content_type: str, max_bytes: int):
        self.store, self.content_type, self.max_bytes = store, content_type, max_bytes
        self.key = f"tmp/{uuid.uuid4()}"
        self.sha = hashlib.sha256()
        self.size = 0
        self._buf = bytearray()
        self._mpu: str | None = None
        self._parts: list[dict] = []

    @property
    def ready(self) -> bool:
        return len(self._buf) >= PART

    @property
    def sha256(self) -> str:
        return self.sha.hexdigest()

    def feed(self, data: bytes) -> None:
        self.size += len(data)
        if self.size > self.max_bytes:
            raise TooLarge(f"upload exceeds {self.max_bytes} bytes")
        self.sha.update(data)
        self._buf += data

    def flush(self) -> None:
        c, b = self.store.client, self.store.bucket
        if self._mpu is None:
            self._mpu = c.create_multipart_upload(Bucket=b, Key=self.key, ContentType=self.content_type)[
                "UploadId"
            ]
        n = len(self._parts) + 1
        r = c.upload_part(
            Bucket=b, Key=self.key, UploadId=self._mpu, PartNumber=n, Body=bytes(self._buf[:PART])
        )
        self._parts.append({"ETag": r["ETag"], "PartNumber": n})
        del self._buf[:PART]

    def finish(self) -> None:
        if self.size == 0:
            raise EmptyFile("empty upload")
        c, b = self.store.client, self.store.bucket
        if self._mpu is None:
            c.put_object(Bucket=b, Key=self.key, Body=bytes(self._buf), ContentType=self.content_type)
            return
        while self._buf:
            self.flush()
        c.complete_multipart_upload(
            Bucket=b, Key=self.key, UploadId=self._mpu, MultipartUpload={"Parts": self._parts}
        )
        self._mpu = None

    def abort(self) -> None:
        """Abort an open multipart upload and delete tmp (idempotent, never raises)."""
        c, b = self.store.client, self.store.bucket
        try:
            if self._mpu is not None:
                c.abort_multipart_upload(Bucket=b, Key=self.key, UploadId=self._mpu)
                self._mpu = None
            c.delete_object(Bucket=b, Key=self.key)
        except Exception:  # noqa: BLE001 - cleanup must not mask the original error; lifecycle rule reaps leftovers
            log.warning("tmp upload cleanup failed for %s", self.key)


@dataclass
class StagedBlob:
    """A streamed upload awaiting `BlobStore.promote`; same sha256/size contract as StagedFile."""

    upload: TmpUpload
    sha256: str
    size: int
    filename: str
    content_type: str


class BlobStore:
    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    def key(self, sha256: str) -> str:
        return f"blobs/{sha256[:2]}/{sha256}"

    def put(self, staged: StagedFile, content_type: str) -> None:
        # ponytail: always PUT; costs bandwidth on duplicates, accepted to avoid an existence oracle (timing).
        self.client.upload_file(
            str(staged.path),
            self.bucket,
            self.key(staged.sha256),
            ExtraArgs={"ContentType": content_type, "Metadata": {"sha256": staged.sha256}},
        )

    def promote(self, staged: StagedBlob, content_type: str) -> None:
        """Server-side copy tmp -> content key unless it exists (dedup), then delete tmp."""
        key, c = self.key(staged.sha256), self.client
        try:
            c.head_object(Bucket=self.bucket, Key=key)
        except c.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                raise
            c.copy(
                {"Bucket": self.bucket, "Key": staged.upload.key},
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"sha256": staged.sha256},
                    "MetadataDirective": "REPLACE",
                },
            )
        staged.upload.abort()

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def open_key(self, key: str) -> tuple[BinaryIO, int]:
        r = self.client.get_object(Bucket=self.bucket, Key=key)
        return r["Body"], r["ContentLength"]

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
        verify=settings.s3_ca_bundle or None,
    )
    return BlobStore(client, settings.s3_bucket)
