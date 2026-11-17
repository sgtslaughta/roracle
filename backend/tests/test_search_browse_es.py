# ruff: noqa: F811
"""ES browse path against a real Elasticsearch: corpus-wide facets/totals under acl_filter, PG re-check (R19)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_seed import seed
from conftest import bearer
from test_labels_text import label, mk
from test_mcp_tools import pat
from test_search_es import es, wired  # noqa: F401  (fixtures)

from roracle import search_fields
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import get_app_settings
from roracle.ingest.chunking import Chunk
from roracle.models import DocCitation, Tag, Vote
from roracle.search_index import _mapping, acl_fields, index_chunks, index_name
from roracle.search_signals import sync_signals

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("_blob_store_override")]
T0 = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def put(es, cfg, db, doc, ver, tags=()):
    for t in tags:
        db.add(Tag(document_id=doc.id, tag=t))
    db.flush()
    index_chunks(
        es, ver, doc, [Chunk(0, "t", "t", ["H"], [1], [])], [[1.0, 0.0, 0.0, 0.1]], acl_fields(db, doc),
        cfg.embed_model, title=doc.title, tags=list(tags), refresh=False,
    )  # fmt: skip


def sync(es, cfg, db, session_factory, docs):
    db.commit()
    es.indices.refresh(index=index_name(cfg.embed_model))  # update_by_query sees refreshed docs only
    sync_signals(es, session_factory, [str(d.id) for d in docs])
    es.indices.refresh(index=index_name(cfg.embed_model))


def get(client, h, path="/search", **params):
    r = client.get(path, headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_facets_count_every_visible_doc(client, as_ana, ana, bob, db_session, es, wired, session_factory):
    docs = [seed(db_session, bob, title=f"D{i}") for i in range(205)]
    hidden = seed(db_session, bob, title="Hidden", visibility="private")
    for d, v in [*docs, hidden]:
        put(es, wired, db_session, d, v, ["ml"])
    sync(es, wired, db_session, session_factory, [d for d, _ in [*docs, hidden]])
    r = get(client, as_ana)
    assert (r["total"], r["total_capped"], r["page"]) == (205, False, 1)
    assert r["facets"]["tags"] == [{"value": "ml", "count": 205}]
    assert len(r["results"]) == 20 and "Hidden" not in {x["title"] for x in r["results"]}
    assert r["next"] is not None and set(r) >= {"total", "total_capped", "page", "next", "results", "facets"}
    assert "upgrading" not in r


def test_unsynced_head_is_invisible(client, as_ana, ana, db_session, es, wired, session_factory):
    ok, vo = seed(db_session, ana, title="Ready")
    mid, vm = seed(db_session, ana, title="Mid-ingest")
    put(es, wired, db_session, ok, vo, ["x"])
    put(es, wired, db_session, mid, vm, ["x"])
    sync(es, wired, db_session, session_factory, [ok])  # `mid` never got its signals: no `searchable`
    r = get(client, as_ana)
    assert [x["title"] for x in r["results"]] == ["Ready"] and r["total"] == 1
    assert r["facets"]["tags"] == [{"value": "x", "count": 1}]


def test_revoked_share_drops_from_facets_at_once(
    client,
    as_ana,
    as_bob,
    ana,
    bob,
    db_session,
    es,
    wired,
    session_factory,
):
    d, v = seed(db_session, ana, title="Shared", visibility="shared")
    put(es, wired, db_session, d, v, ["rf1"])
    sync(es, wired, db_session, session_factory, [d])
    assert get(client, as_bob)["total"] == 0
    r = client.patch(f"/documents/{d.id}", headers=as_ana, json={"share_users": [str(bob)]})
    assert r.status_code == 200, r.text
    got = get(client, as_bob)
    assert got["total"] == 1 and got["facets"]["tags"] == [{"value": "rf1", "count": 1}]
    r = client.patch(f"/documents/{d.id}", headers=as_ana, json={"share_users": []})
    assert r.status_code == 200, r.text
    got = get(client, as_bob)
    assert (got["total"], got["results"], got["facets"]["tags"]) == (0, [], [])


def test_pat_never_counts_block_agents(client, as_ana, ana, admin, db_session, es, wired, session_factory):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    plain, vp = seed(db_session, ana, title="Plain", authors=("Zola Plain",))
    blocked, vb = seed(db_session, ana, title="Blocked", authors=("Zola Blocked",))
    label(db_session, blocked, sec)
    put(es, wired, db_session, plain, vp, ["t"])
    put(es, wired, db_session, blocked, vb, ["t"])
    sync(es, wired, db_session, session_factory, [plain, blocked])
    ui = get(client, as_ana)
    assert ui["total"] == 2 and ui["facets"]["tags"] == [{"value": "t", "count": 2}]
    assert {x["value"] for x in get(client, as_ana, "/search/facets/author", find="zola")["values"]} == {
        "Zola Plain",
        "Zola Blocked",
    }
    tok = pat(db_session, ana, ["read"])
    r = get(client, tok)
    assert r["total"] == 1 and [x["title"] for x in r["results"]] == ["Plain"]
    assert r["facets"]["tags"] == [{"value": "t", "count": 1}]
    assert sec not in {x["value"] for x in r["facets"]["label"]}
    assert get(client, tok, "/search/facets/author", find="zola")["values"] == [
        {"value": "Zola Plain", "count": 1}
    ]
    assert get(client, tok, "/search/facets/tags", find="t")["values"] == [{"value": "t", "count": 1}]


def _sorted_docs(es, cfg, db, session_factory, ana, bob):
    rows = [  # title, year, pages, venue, authors, added (hours)
        ("b", 2018, 10, "Zeta", ("Zed",), 1),
        ("A", 2024, 5, "Alpha", ("Ann",), 2),
        ("c", None, None, None, (), 3),
    ]
    docs = []
    for title, year, pages, venue, authors, h in rows:
        d, v = seed(db, ana, title=title, year=year, venue=venue, authors=authors)
        d.created_at = T0 + timedelta(hours=h)
        v.page_count = pages
        docs.append((d, v))
    db.add_all([Vote(user_id=ana, document_id=docs[0][0].id, value=1),
                Vote(user_id=bob, document_id=docs[1][0].id, value=-1)])  # fmt: skip
    for d, v in docs:
        put(es, cfg, db, d, v)
    sync(es, cfg, db, session_factory, [d for d, _ in docs])


@pytest.mark.parametrize(
    "sort,want",
    [
        ("relevance", ["c", "A", "b"]),
        ("newest", ["c", "A", "b"]),
        ("published_desc", ["A", "b", "c"]),
        ("published_asc", ["b", "A", "c"]),
        ("votes", ["b", "c", "A"]),
        ("title", ["A", "b", "c"]),
        ("first_author", ["A", "b", "c"]),
        ("venue", ["A", "b", "c"]),
        ("pages", ["A", "b", "c"]),
    ],
)
def test_sorts(client, as_ana, ana, bob, db_session, es, wired, session_factory, sort, want):
    _sorted_docs(es, wired, db_session, session_factory, ana, bob)
    r = get(client, as_ana, sort=sort)
    assert [x["title"] for x in r["results"]] == want
    h = {x["title"]: x for x in r["results"]}
    assert h["A"]["page_count"] == 5 and h["c"]["page_count"] is None
    assert h["A"]["added_at"] == (T0 + timedelta(hours=2)).isoformat()


def test_after_walks_page_order(client, as_ana, ana, db_session, es, wired, session_factory):
    docs = [seed(db_session, ana, title=f"T{i}") for i in (5, 2, 6, 0, 3, 1, 4)]
    for d, v in docs:
        put(es, wired, db_session, d, v)
    sync(es, wired, db_session, session_factory, [d for d, _ in docs])
    by_page = [
        x["title"] for p in (1, 2, 3) for x in get(client, as_ana, sort="title", size=3, page=p)["results"]
    ]
    assert by_page == [f"T{i}" for i in range(7)]
    seen, after = [], None
    for _ in range(5):
        r = get(client, as_ana, sort="title", size=3, **({"after": after} if after else {}))
        seen += [x["title"] for x in r["results"]]
        after = r["next"]
        if after is None:
            break
    assert seen == by_page
    assert client.get("/search", headers=as_ana, params={"after": "junk"}).status_code == 422
    assert client.get("/search", headers=as_ana, params={"page": 2000, "size": 10}).status_code == 422


def test_cited_window_counts_only_visible_citers(
    client,
    as_ana,
    ana,
    bob,
    db_session,
    es,
    wired,
    session_factory,
):
    a, va = seed(db_session, ana, title="A")
    b, vb = seed(db_session, ana, title="B")

    def cite(target, owner, visibility):
        _, cv = seed(db_session, owner, title="citer", visibility=visibility)
        db_session.add(
            DocCitation(src_version_id=cv.id, ref_no=1, target_document_id=target.id, confidence=1.0)
        )

    for _ in range(2):
        cite(a, ana, "everyone")
    for _ in range(3):
        cite(a, bob, "private")
        cite(b, ana, "everyone")
    put(es, wired, db_session, a, va, ["t"])
    put(es, wired, db_session, b, vb, ["t"])
    sync(es, wired, db_session, session_factory, [a, b])
    r = get(client, as_ana, sort="cited")
    assert [(x["title"], x["cited_by_count"]) for x in r["results"]] == [("B", 3), ("A", 2)]
    assert (r["total"], r["total_capped"], r["next"]) == (2, False, None)
    r = get(client, as_ana, cited_min=3)
    assert [x["title"] for x in r["results"]] == ["B"] and r["total"] == 1
    assert r["facets"]["tags"] == [{"value": "t", "count": 1}]  # never counts A via invisible citers


def test_invisible_cites_is_absent(client, as_ana, ana, bob, db_session, es, wired, session_factory):
    x, _ = seed(db_session, bob, title="X", visibility="private")
    d, v = seed(db_session, ana, title="D")
    put(es, wired, db_session, d, v)
    sync(es, wired, db_session, session_factory, [d])
    assert get(client, as_ana, cites=str(x.id)) == get(client, as_ana, cites=str(uuid.uuid4()))


def test_find_is_literal(client, as_ana, ana, db_session, es, wired, session_factory):
    d, v = seed(db_session, ana, title="Germinal", authors=("Émile Zola",))
    put(es, wired, db_session, d, v)
    sync(es, wired, db_session, session_factory, [d])
    assert get(client, as_ana, "/search/facets/author", find="zol") == {
        "values": [{"value": "Émile Zola", "count": 1}]
    }
    assert get(client, as_ana, "/search/facets/author", find=".*") == {"values": []}
    assert client.get("/search/facets/year", headers=as_ana, params={"find": "x"}).status_code == 404
    assert client.get("/search/facets/author", headers=as_ana, params={"find": ""}).status_code == 422


@pytest.fixture
def legacy_index(app, settings, es):
    model = f"Test Model:{uuid.uuid4().hex[:8]}"
    cfg = settings.model_copy(
        update={"embed_model": model, "embed_dims": 4, "rerank_url": "", "embed_index": None}
    )
    old = _mapping(4)
    old.pop("_meta")
    es.indices.create(index=index_name(model), mappings=old)
    search_fields._READY.clear()
    app.dependency_overrides[get_es] = lambda: es
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[get_app_settings] = lambda: cfg
    yield cfg
    es.indices.delete(index=index_name(model), ignore_unavailable=True)
    search_fields._READY.clear()


def test_legacy_index_upgrading(client, as_ana, legacy_index):
    assert get(client, as_ana)["upgrading"] is True
    r = client.get("/search", headers=as_ana, params={"tags_not": "x"})
    assert r.status_code == 503 and r.json() == {"detail": "search index upgrade in progress"}


def _cite(db, target, owner, visibility, n):
    for _ in range(n):
        _, cv = seed(db, owner, title="citer", visibility=visibility)
        db.add(DocCitation(src_version_id=cv.id, ref_no=1, target_document_id=target.id, confidence=1.0))


def test_cited_window_ranks_by_global_not_user_sort(
    client, as_ana, ana, db_session, es, wired, session_factory, monkeypatch
):
    monkeypatch.setattr("roracle.search_browse.MAX_DOCS", 2)
    docs = {t: seed(db_session, ana, title=t) for t in ("a", "b", "z")}
    for t, n in (("a", 1), ("b", 1), ("z", 3)):
        _cite(db_session, docs[t][0], ana, "everyone", n)
    for d, v in docs.values():
        put(es, wired, db_session, d, v)
    sync(es, wired, db_session, session_factory, [d for d, _ in docs.values()])
    r = get(client, as_ana, cited_min=1, sort="title")
    titles = [x["title"] for x in r["results"]]
    assert (
        "z" in titles and titles == sorted(titles) and len(titles) == 2
    )  # most cited sorts last, still kept
    assert r["total_capped"] is True and r["total"] == 2


@pytest.mark.parametrize("max_docs", [1, 2, 200])
def test_cited_min_never_reveals_invisible_citers(
    client, as_ana, ana, bob, db_session, es, wired, session_factory, monkeypatch, max_docs
):
    monkeypatch.setattr("roracle.search_browse.MAX_DOCS", max_docs)
    a, va = seed(db_session, ana, title="A")  # 2 visible + 3 invisible citers: global 5
    b, vb = seed(db_session, ana, title="B")  # 6 visible: global 6
    _cite(db_session, a, ana, "everyone", 2)
    _cite(db_session, a, bob, "private", 3)
    _cite(db_session, b, ana, "everyone", 6)
    put(es, wired, db_session, a, va, ["t"])
    put(es, wired, db_session, b, vb, ["t"])
    sync(es, wired, db_session, session_factory, [a, b])
    five, six = get(client, as_ana, cited_min=5), get(client, as_ana, cited_min=6)
    assert five == six  # A enters the cited_min=5 window only through invisible citers
    assert [x["title"] for x in five["results"]] == ["B"] and five["total"] == 1
    assert five["total_capped"] is (max_docs == 1) and five["facets"]["tags"] == [{"value": "t", "count": 1}]
    for s in ("cited", "title"):
        assert get(client, as_ana, cited_min=5, sort=s) == get(client, as_ana, cited_min=6, sort=s)


@pytest.mark.parametrize("sort", ["venue", "first_author", "pages", "published_desc", "published_asc"])
def test_after_walks_missing_values(client, as_ana, ana, db_session, es, wired, session_factory, sort):
    rows = [("v1", "Beta", ("Bo",), 2001, 4), ("v2", None, (), None, None), ("v3", "Alpha", ("Al",), 2003, 2),
            ("v4", None, (), None, None), ("v5", "Gamma", ("Cy",), 2002, 9)]  # fmt: skip
    docs = []
    for title, venue, authors, year, pages in rows:
        d, v = seed(db_session, ana, title=title, venue=venue, authors=authors, year=year)
        v.page_count = pages
        docs.append((d, v))
    for d, v in docs:
        put(es, wired, db_session, d, v)
    sync(es, wired, db_session, session_factory, [d for d, _ in docs])
    by_page = [
        x["title"] for p in (1, 2, 3) for x in get(client, as_ana, sort=sort, size=2, page=p)["results"]
    ]
    assert sorted(by_page) == [f"v{i}" for i in range(1, 6)] and set(by_page[-2:]) == {"v2", "v4"}
    seen, after = [], None
    for _ in range(6):
        r = get(client, as_ana, sort=sort, size=2, **({"after": after} if after else {}))
        seen += [x["title"] for x in r["results"]]
        after = r["next"]
        if after is None:
            break
    assert seen == by_page


def test_query_facets_count_only_pg_visible(client, as_ana, ana, bob, db_session, es, wired, session_factory):
    mine, vm = seed(db_session, ana, title="Deep mine")
    other, vo = seed(db_session, bob, title="Deep other")  # indexed while visible to everyone
    put(es, wired, db_session, mine, vm, ["ml"])
    put(es, wired, db_session, other, vo, ["secret", "ml"])
    sync(es, wired, db_session, session_factory, [mine, other])
    assert {x["value"] for x in get(client, as_ana, q="deep")["facets"]["tags"]} == {"ml", "secret"}
    other.visibility = "private"  # ES ACL now stale: Postgres re-check is the authority (R19)
    db_session.flush()
    for params in ({}, {"tags": "ml"}, {"tags_not": "zz", "sort": "title"}):
        r = get(client, as_ana, q="deep", **params)
        assert [x["title"] for x in r["results"]] == ["Deep mine"] and r["total"] == 1
        assert r["facets"]["tags"] == [{"value": "ml", "count": 1}], params


def test_query_path_ignores_invisible_citers(
    client, as_ana, ana, bob, db_session, es, wired, session_factory
):
    a, va = seed(db_session, ana, title="Deep a")
    b, vb = seed(db_session, ana, title="Deep b")
    _cite(db_session, a, ana, "everyone", 2)
    for d, v in ((a, va), (b, vb)):
        put(es, wired, db_session, d, v, ["t"])
    sync(es, wired, db_session, session_factory, [a, b])
    reqs = [{"sort": "cited"}, {"cited_min": 2}, {"cited_min": 3}, {"cited_min": 1, "sort": "title"}]
    before = [get(client, as_ana, q="deep", **r) for r in reqs]
    _cite(db_session, b, bob, "private", 4)  # b: global 4, visible 0
    _cite(db_session, a, bob, "private", 1)
    sync(es, wired, db_session, session_factory, [a, b])
    assert [get(client, as_ana, q="deep", **r) for r in reqs] == before
    assert [x["title"] for x in before[0]["results"]] == ["Deep a", "Deep b"]
    assert (before[1]["total"], before[2]["total"]) == (1, 0)
    assert before[1]["facets"]["tags"] == [{"value": "t", "count": 1}]
