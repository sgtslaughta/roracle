import uuid
from datetime import UTC, datetime

import pytest
from elasticsearch import Elasticsearch
from sqlalchemy import select

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.ingest.chunking import Chunk
from roracle.models import Document, DocumentShare, DocumentVersion, User
from roracle.search_acl import acl_filter
from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name


def _p(groups=(), admin=False, uid=None):
    return Principal(user_id=uid or uuid.uuid4(), sub="s", username="u", groups=tuple(groups), is_admin=admin)


def test_admin_matches_all():
    assert acl_filter(_p(admin=True)) == {"match_all": {}}


def test_groupless_shape_has_no_share_groups_clause():
    p = _p()
    f = acl_filter(p)
    assert f["bool"]["minimum_should_match"] == 1
    assert f["bool"]["should"] == [
        {"term": {"visibility": "everyone"}},
        {"term": {"owner_id": str(p.user_id)}},
        {"term": {"share_users": str(p.user_id)}},
        {"bool": {"must": [{"term": {"visibility": "org_unit"}}, {"terms": {"org_unit": ["/"]}}]}},
    ]


def test_groups_add_share_groups_and_ancestor_terms():
    f = acl_filter(_p(["/A/B"]))
    should = f["bool"]["should"]
    assert {"terms": {"share_groups": ["/A/B"]}} in should
    assert should[-1]["bool"]["must"][1] == {"terms": {"org_unit": ["/", "/A", "/A/B"]}}


# ---- parity (real ES + Postgres) ----
@pytest.fixture
def es():
    return Elasticsearch("http://localhost:9200")


@pytest.fixture
def model(es):
    m = f"Test Model:{uuid.uuid4().hex[:8]}"
    yield m
    es.indices.delete(index=index_name(m), ignore_unavailable=True)


def test_sql_es_parity(es, model, db_session, blob):
    ana, eve, bob, carol, dave = (
        User(id=uuid.uuid4(), sub=n, username=n, email=f"{n}@x.org")
        for n in ["ana", "eve", "bob", "carol", "dave"]
    )
    db_session.add_all([ana, eve, bob, carol, dave])
    db_session.flush()
    # name -> (owner, visibility, org_unit, shares)
    specs = {
        "everyone": (eve, "everyone", "/", []),
        "private_owner": (ana, "private", "/", []),
        "private_other": (eve, "private", "/", []),
        "shared_user": (eve, "shared", "/", [("user", str(bob.id))]),
        "shared_group": (eve, "shared", "/", [("group", "/Physics")]),
        "shared_other_group": (eve, "shared", "/", [("group", "/Chemistry")]),
        "org_equal": (eve, "org_unit", "/Mathematics/Spectral Theory", []),
        "org_ancestor": (eve, "org_unit", "/Mathematics", []),
        "org_sibling": (eve, "org_unit", "/Math", []),
        "org_root": (eve, "org_unit", "/", []),
        "org_underscore": (eve, "org_unit", "/Chem_Lab", []),
        "org_lookalike": (eve, "org_unit", "/ChemXLab", []),
        "deleted": (eve, "everyone", "/", []),
    }
    docs: dict[str, Document] = {}
    for name, (owner, vis, ou, shares) in specs.items():
        d = Document(owner_id=owner.id, title=name, visibility=vis, org_unit=ou)
        if name == "deleted":
            d.deleted_at = datetime.now(UTC)
        db_session.add(d)
        db_session.flush()
        db_session.add_all(DocumentShare(document_id=d.id, principal_kind=k, principal=v) for k, v in shares)
        docs[name] = d
    db_session.flush()

    ensure_index(es, model, 4)
    for name, d in docs.items():
        if d.deleted_at:
            continue  # Ruling R2: deletion removes chunks from ES, so none is indexed for it
        v = DocumentVersion(
            id=uuid.uuid4(), document_id=d.id, version=1, blob_sha256=blob.sha256, filename="a",
            content_type="x", size=1, is_latest=True,
        )  # fmt: skip
        db_session.add(v)
        db_session.flush()
        index_chunks(
            es, v, d, [Chunk(0, "t", "c", ["H"], [1], [{"page": 1}])], [[0.1] * 4],
            acl_fields(db_session, d), model, title=name,
        )  # fmt: skip

    principals = {
        "admin": _p(admin=True),
        "ana": _p(uid=ana.id),
        "bob": _p(["/Physics", "/Chem_Lab"], uid=bob.id),
        "carol": _p(uid=carol.id),
        "dave": _p(["/Mathematics/Spectral Theory"], uid=dave.id),
    }
    for who, p in principals.items():
        sql = {str(i) for i in db_session.scalars(select(Document.id).where(visible_documents(p)))}
        hits = es.search(
            index=index_name(model),
            query={"bool": {"filter": [acl_filter(p), {"term": {"is_latest": True}}]}},
            size=100,
        )["hits"]["hits"]
        assert {h["_source"]["doc_id"] for h in hits} == sql, who
        assert str(docs["deleted"].id) not in sql
