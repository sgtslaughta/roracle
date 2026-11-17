"""Live-ES check of chat/summarize.load_chunks (reviewer scratch; fixer: add as backend/tests/test_summarize_es.py).

Covers: reading order by ordinal (2 < 10 numerically, not lexically), latest version only, other docs excluded,
chunk vs unit kinds, gate (a) exclude_llm label filtered in ES, block_agents hidden from PATs, invisible doc empty.
"""

import dataclasses

import pytest
from api_seed import seed
from sqlalchemy import select
from test_labels_gates import admin, label, mk  # noqa: F401
from test_search_es import es, wired  # noqa: F401

from roracle.auth import Principal
from roracle.chat.summarize import load_chunks
from roracle.ingest.chunking import Chunk
from roracle.ingest.raptor import Unit
from roracle.labels.policy import Policy
from roracle.models import DocumentVersion
from roracle.search_index import acl_fields, index_chunks, index_name, index_units

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("_blob_store_override")]
BB = [{"page": 1, "l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}]


def put(es, cfg, db, doc, ver, texts, latest=None, units=()):  # noqa: F811
    cs = [Chunk(i, t, t, ["H"], [i + 1], BB) for i, t in enumerate(texts)]
    index_chunks(es, ver, doc, cs, [[1.0, 0.0, 0.0, 0.1]] * len(cs), acl_fields(db, doc), cfg.embed_model,
                 title=doc.title, is_latest=latest)  # fmt: skip
    if units:
        index_units(es, ver, doc, list(units), [[0.0, 1.0, 0.0, 0.1]] * len(units), acl_fields(db, doc),
                    cfg.embed_model, title=doc.title, is_latest=latest)  # fmt: skip
    es.indices.refresh(index=index_name(cfg.embed_model))


def who(uid, pat=False):
    p = Principal(user_id=uid, sub="ana", username="ana", groups=(), is_admin=False)
    return dataclasses.replace(p, via="pat", pat_id="x") if pat else p


def test_load_chunks_order_version_kind(db_session, es, wired, ana, client, as_ana):  # noqa: F811
    d, v2 = seed(db_session, ana, title="Two versions", versions=2)
    v1 = db_session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == d.id,
                                                         DocumentVersion.version == 1))  # fmt: skip
    other, ov = seed(db_session, ana, title="Other")
    db_session.commit()
    put(es, wired, db_session, d, v1, ["OLD 0", "OLD 1"], latest=False)
    texts = [f"chunk {i}" for i in range(12)]  # ordinals 0..11: "10" sorts before "2" lexically
    sec = Unit("section", "s0", "section summary", ["H"], [f"{v2.id}:3", f"{v2.id}:4"], [4, 5])
    whole = Unit("document", "d", "doc summary", [], [f"{v2.id}:{i}" for i in range(12)], [1])
    put(es, wired, db_session, d, v2, texts, units=[sec, whole])
    put(es, wired, db_session, other, ov, ["other doc"])
    pol = Policy.load(db_session)
    got = load_chunks(es, wired, who(ana), pol, v2)
    assert [c.text for c in got] == texts  # reading order, latest version only, no units, no other doc
    assert {c.version_id for c in got} == {str(v2.id)} and all(not c.unit for c in got)
    units = load_chunks(es, wired, who(ana), pol, v2, units=True)
    assert [u.text for u in units] == ["doc summary", "section summary"]  # by first source ordinal
    assert all(u.unit in ("section", "document") for u in units)
    assert load_chunks(es, wired, who(ana), pol, v1) == []  # old version: is_latest=false in ES


def test_load_chunks_label_gates_and_acl(db_session, es, wired, ana, bob, client, admin):  # noqa: F811
    noagent = mk(client, admin, "NoAgent", 3, block_agents=True, exclude_llm=False)
    nollm = mk(
        client, admin, "NoLLM", 4, block_agents=True, exclude_llm=True
    )  # gates never decrease with rank
    a, av = seed(db_session, ana, title="NoLLM doc")
    b, bv = seed(db_session, ana, title="NoAgent doc")
    c, cv = seed(db_session, bob, title="Bobs private", visibility="private")
    label(db_session, a, nollm)
    label(db_session, b, noagent)
    for d, v in ((a, av), (b, bv), (c, cv)):
        put(es, wired, db_session, d, v, ["secret text"])
    pol = Policy.load(db_session)
    assert (
        load_chunks(es, wired, who(ana), pol, av) == []
    )  # gate (a) in ES, even if Postgres check were skipped
    assert [x.text for x in load_chunks(es, wired, who(ana), pol, bv)] == ["secret text"]  # JWT sees it
    assert load_chunks(es, wired, who(ana, pat=True), pol, bv) == []  # gate (b): PAT never
    assert load_chunks(es, wired, who(ana), pol, cv) == []  # ACL: bob's private doc
