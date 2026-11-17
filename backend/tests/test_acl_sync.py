import uuid

from roracle import acl_sync, search_index, search_index_acl
from roracle.models import Document, DocumentCoauthor, DocumentShare, Tag
from roracle.search_index import acl_hash, acl_payload

ACL = {
    "owner_id": "o", "visibility": "shared", "org_unit": "ou", "share_users": ["u1", "u2"],
    "share_groups": ["g"], "coauthor_users": ["c"], "label_id": "l", "tags": ["a", "b"],
}  # fmt: skip


def test_hash_is_order_insensitive_and_covers_every_synced_field():
    base = acl_hash(ACL, "T")
    assert acl_hash({**ACL, "share_users": ["u2", "u1"], "tags": ["b", "a"]}, "T") == base
    changed = {
        "owner_id": "x", "visibility": "private", "org_unit": "y", "share_users": ["u1"],
        "share_groups": ["h"], "coauthor_users": [], "label_id": "m", "tags": ["a"],
    }  # fmt: skip
    for k, v in changed.items():
        assert acl_hash({**ACL, k: v}, "T") != base, k
    assert acl_hash(ACL, "other") != base


def test_payload_without_title_or_tags_drops_the_hash():
    full = acl_payload(ACL, "T")
    assert full["fields"]["acl_hash"] == acl_hash(ACL, "T") and full["drop_hash"] is False
    no_title = acl_payload(ACL, None)
    assert (
        "title" not in no_title["fields"] and "acl_hash" not in no_title["fields"] and no_title["drop_hash"]
    )
    no_tags = acl_payload({k: v for k, v in ACL.items() if k != "tags"}, "T")
    assert "tags" not in no_tags["fields"] and no_tags["drop_hash"]


def test_script_is_noop_aware_and_compares_every_field():
    for k in search_index.ACL_KEYS:
        assert k in ACL  # keys the script iterates come from the payload, all ACL keys included
    assert "ctx.op = 'noop'" in search_index._ACL_SCRIPT


def test_read_acl_many_matches_read_acl(db_session, session_factory, user):
    docs = []
    for i, vis in enumerate(("private", "shared", "everyone")):
        d = Document(owner_id=user.id, title=None if i == 2 else f"t{i}", org_unit="/ou", visibility=vis)
        db_session.add(d)
        db_session.flush()
        db_session.add_all([Tag(document_id=d.id, tag="z"), Tag(document_id=d.id, tag="a")])
        if vis == "shared":
            db_session.add_all(
                [
                    DocumentShare(document_id=d.id, principal_kind="user", principal=str(uuid.uuid4())),
                    DocumentShare(document_id=d.id, principal_kind="group", principal="g1"),
                    DocumentCoauthor(document_id=d.id, user_id=user.id),
                ]
            )
        docs.append(d)
    db_session.commit()
    many = acl_sync.read_acl_many(db_session, [d.id for d in docs])
    assert len(many) == 3
    for d in docs:
        assert many[str(d.id)] == acl_sync.read_acl(session_factory, d.id)


class _Es:
    def __init__(self):
        self.puts, self.searches = [], []

    class indices:
        pass

    def search(self, **kw):
        self.searches.append(kw)
        return {"hits": {"hits": []}}

    def update_by_query(self, **kw):
        return {"updated": 0}


def test_mapping_guard_runs_once_per_process(monkeypatch):
    calls = []

    class Indices:
        def put_mapping(self, **kw):
            calls.append(kw)

    es = _Es()
    es.indices = Indices()
    monkeypatch.setattr(search_index_acl, "_mapping_ensured", False)
    items = {"d": (ACL, "T")}
    search_index.sync_acl_many(es, ["d"], lambda ids: items)
    search_index.sync_acl(es, "d", lambda: items["d"])
    assert len(calls) == 1
    assert calls[0]["index"] == search_index.MUTATE_ALIAS
    assert calls[0]["properties"] == {"label_id": {"type": "keyword"}, "acl_hash": {"type": "keyword"}}


def test_drift_search_is_batched():
    es = _Es()
    items = {f"d{i}": (ACL, "T") for i in range(450)}
    assert acl_sync.drifted(es, items) == set()
    assert [len(q["query"]["bool"]["should"]) for q in es.searches] == [200, 200, 50]
