"""PATCH /documents sync to a real Elasticsearch: effects visible in /search immediately."""

import pytest
from api_seed import seed
from test_search_es import es, index, wired  # noqa: F401  (fixtures + indexing helper)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("_blob_store_override")]


def found(client, h, q="quokka"):
    return [r["title"] for r in client.get("/search", headers=h, params={"q": q}).json()["results"]]


@pytest.fixture
def doc(client, as_ana, ana, bob, db_session, es, wired):  # noqa: F811
    d, v = seed(db_session, ana, title="Quokka paper", visibility="everyone")
    index(es, wired, db_session, d, v, "quokka text")
    return d


def patch(client, h, d, **body):
    r = client.patch(f"/documents/{d.id}", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r


def test_private_hides_immediately_then_user_share(client, as_ana, as_bob, bob, doc):
    assert found(client, as_bob) == ["Quokka paper"]
    patch(client, as_ana, doc, visibility="private")
    assert found(client, as_bob) == []
    patch(client, as_ana, doc, visibility="shared", share_users=[str(bob)])
    assert found(client, as_bob) == ["Quokka paper"]
    patch(client, as_ana, doc, share_users=[])
    assert found(client, as_bob) == []
    assert found(client, as_ana) == ["Quokka paper"]


def test_group_share(client, as_ana, as_bob, doc):
    patch(client, as_ana, doc, visibility="shared")
    assert found(client, as_bob) == []
    patch(client, as_ana, doc, share_groups=["/Physics/"])  # bob is in /Physics
    assert found(client, as_bob) == ["Quokka paper"]
    patch(client, as_ana, doc, share_groups=[])
    assert found(client, as_bob) == []


def test_org_unit_and_title(client, as_ana, as_bob, doc):
    patch(client, as_ana, doc, visibility="org_unit", org_unit="/Physics/")
    assert found(client, as_bob) == ["Quokka paper"]
    patch(client, as_ana, doc, org_unit="/Chemistry")
    assert found(client, as_bob) == []
    patch(client, as_ana, doc, visibility="everyone", title="Renamed marsupial")
    assert found(client, as_bob) == ["Renamed marsupial"]
