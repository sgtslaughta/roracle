import hashlib
import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("_blob_store_override")]


def test_20mib_roundtrip_through_real_minio(client, as_ana, s3, s3_client, test_bucket):
    data = os.urandom(20 * 1024 * 1024)
    sha = hashlib.sha256(data).hexdigest()
    r = client.post("/documents", headers=as_ana, files={"file": ("big.bin", data, "application/x-test")})
    assert r.status_code == 201 and r.json()["sha256"] == sha
    key = f"blobs/{sha[:2]}/{sha}"
    head = s3_client.head_object(Bucket=test_bucket, Key=key)
    assert head["ContentLength"] == len(data) and head["ContentType"] == "application/x-test"
    assert head["Metadata"] == {"sha256": sha}
    got = s3_client.get_object(Bucket=test_bucket, Key=key)["Body"].read()
    assert hashlib.sha256(got).hexdigest() == sha
    assert not [k for k in s3.keys() if k.startswith("tmp/")]  # noqa: SIM118
    assert s3_client.list_multipart_uploads(Bucket=test_bucket).get("Uploads", []) == []
