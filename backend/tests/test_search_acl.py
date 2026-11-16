import uuid
from datetime import UTC, datetime

import pytest
from elasticsearch import Elasticsearch
from sqlalchemy import select

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.ingest.chunking import Chunk
from roracle.models import Document, DocumentCoauthor, DocumentShare, DocumentVersion, User
from roracle.search_acl import acl_filter
from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name


def _p(groups=(), admin=False, uid=None):
    return Principal(user_id=uid or uuid.uuid4(), sub="s", username="u", groups=tuple(groups), is_admin=admin)


def _shared(uid, *groups):
    grants = [{"term": {"share_users": uid}}]
    if groups:
        grants.append({"terms": {"share_groups": list(groups)}})
    return {
        "bool": {
            "must": [{"term": {"visibility": "shared"}}],
            "filter": [{"bool": {"should": grants, "minimum_should_match": 1}}],
        }
    }


def test_admin_matches_all():
    assert acl_filter(_p(admin=True)) == {"match_all": {}}


def test_groupless_shape_has_no_share_groups_clause():
    p = _p()
    f = acl_filter(p)
    assert f["bool"]["minimum_should_match"] == 1
    assert f["bool"]["should"] == [
        {"term": {"visibility": "everyone"}},
        {"term": {"owner_id": str(p.user_id)}},
        {"term": {"coauthor_users": str(p.user_id)}},
        _shared(str(p.user_id)),
        {"bool": {"must": [{"term": {"visibility": "org_unit"}}, {"terms": {"org_unit": ["/"]}}]}},
    ]


def test_groups_add_share_groups_and_ancestor_terms():
    p = _p(["/A/B"])
    f = acl_filter(p)
    should = f["bool"]["should"]
    assert _shared(str(p.user_id), "/A/B") in should
    assert should[-1]["bool"]["must"][1] == {"terms": {"org_unit": ["/", "/A", "/A/B"]}}


# ---- parity (real ES + Postgres) ----
@pytest.fixture
def es(settings):
    c = Elasticsearch(settings.elastic_url)
    yield c
    c.close()


@pytest.fixture
def model(es):
    m = f"Test Model:{uuid.uuid4().hex[:8]}"
    yield m
    es.indices.delete(index=index_name(m), ignore_unavailable=True)


@pytest.mark.integration
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
        "org_percent": (eve, "org_unit", "/Chem%Lab", []),
        "private_stale_user": (eve, "private", "/", [("user", str(bob.id))]),
        "private_stale_group": (eve, "private", "/", [("group", "/Physics")]),
        "org_stale_share": (eve, "org_unit", "/Zoology", [("user", str(carol.id)), ("group", "/Physics")]),
        "everyone_with_shares": (eve, "everyone", "/", [("user", str(carol.id))]),
        "deleted": (eve, "everyone", "/", []),
        "co_private": (eve, "private", "/", []),
        "co_org_elsewhere": (eve, "org_unit", "/Zoology", []),
        "co_shared_other": (eve, "shared", "/", [("user", str(carol.id))]),
    }
    coauthors = {"co_private": [bob], "co_org_elsewhere": [bob, dave], "co_shared_other": [dave]}
    docs: dict[str, Document] = {}
    for name, (owner, vis, ou, shares) in specs.items():
        d = Document(owner_id=owner.id, title=name, visibility=vis, org_unit=ou)
        if name == "deleted":
            d.deleted_at = datetime.now(UTC)
        db_session.add(d)
        db_session.flush()
        db_session.add_all(DocumentShare(document_id=d.id, principal_kind=k, principal=v) for k, v in shares)
        docs[name] = d
    db_session.add_all(
        DocumentCoauthor(document_id=docs[n].id, user_id=u.id) for n, us in coauthors.items() for u in us
    )
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
        "slash": _p(["/Mathematics/"], uid=carol.id),
        "root_group": _p(["/"], uid=carol.id),
        "percent": _p(["/Chem%Lab"], uid=carol.id),
    }
    for who, p in principals.items():
        sql = {str(i) for i in db_session.scalars(select(Document.id).where(visible_documents(p)))}
        hits = es.search(
            index=index_name(model),
            query={"bool": {"filter": [acl_filter(p), {"term": {"is_latest": True}}]}},
            size=100,
        )["hits"]["hits"]
        assert {h["_source"]["doc_id"] for h in hits} == sql, who
    # soft-deleted doc: hidden in SQL for everyone (and never indexed in ES)
    deleted_id = str(docs["deleted"].id)
    for p in principals.values():
        assert deleted_id not in {
            str(i) for i in db_session.scalars(select(Document.id).where(visible_documents(p)))
        }


def test_share_groups_normalised_like_sql():
    p = _p(["Physics", "/Chem/", "/", ""])
    assert _shared(str(p.user_id), "/Physics", "/Chem") in acl_filter(p)["bool"]["should"]
