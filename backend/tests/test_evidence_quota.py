"""F3: evidence files count against ROR_UPLOAD_QUOTA_BYTES like document uploads."""

import pytest
from test_admin_settings import adm, put  # noqa: F401  (fixture + helper)
from test_evidence import note, post  # noqa: F401  (fixture + helper)

from roracle import quota
from roracle.models import Evidence

pytestmark = pytest.mark.usefixtures("_blob_store_override")


def _doc(client, h, data):
    return client.post("/documents", headers=h, files={"file": ("a.pdf", data, "application/pdf")})


def test_evidence_counts_against_the_upload_quota(client, adm, as_ana, ana, note, db_session):  # noqa: F811
    put(
        client, adm, {"upload_quota_bytes": quota.used_bytes(db_session, ana) + 10}
    )  # the seeded paper counts
    assert post(client, as_ana, note, data=b"123456").status_code == 201
    r = post(client, as_ana, note, data=b"abcde")
    assert r.status_code == 413 and r.json()["detail"] == "upload quota exceeded"
    assert db_session.query(Evidence).count() == 1
    r = _doc(client, as_ana, b"abcde")  # documents see the evidence bytes too
    assert r.status_code == 413 and r.json()["detail"] == "upload quota exceeded"
    assert post(client, as_ana, note, data=b"abcd").status_code == 201  # exactly at quota


def test_uri_evidence_and_unlimited_quota_are_not_limited(client, adm, as_ana, note):  # noqa: F811
    put(client, adm, {"upload_quota_bytes": 1})
    assert (
        post(client, as_ana, note, data=None, uri="https://example.org/r", uri_sha256="c" * 64).status_code
        == 201
    )
    put(client, adm, {"upload_quota_bytes": 0})
    assert post(client, as_ana, note, data=b"x" * 64).status_code == 201
