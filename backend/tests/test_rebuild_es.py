"""P13 T5 against real ES: rebuild into a shadow index leaves live search alone (R7)."""

from functools import partial

import pytest
from elasticsearch import Elasticsearch
from ingest_helpers import FakeLLM, make_version
from test_embed_reuse import ingest, paras

from roracle import runtime, search_index
from roracle.acl_sync import handle_acl_sync
from roracle.ingest import rebuild
from roracle.models import Document
from roracle.queue import run_once

pytestmark = pytest.mark.integration


def _all(es, index, vid):
    es.indices.refresh(index=index)
    return es.search(index=index, query={"term": {"version_id": str(vid)}}, size=100)["hits"]["hits"]


def test_rebuild_into_shadow(db_session, session_factory, cfg, store, es, user, s3):
    cfg = cfg.model_copy(update={"context_model": "ctx", "read_alias": f"chunks-t5-{cfg.embed_model[-8:]}"})
    v = make_version(db_session, store, user, visibility="private")
    ingest(db_session, session_factory, cfg, store, es, v, paras(3))
    active = search_index.active_index(cfg)
    shadow = f"chunks-{search_index.slug(cfg.embed_model)}-shadow"
    try:
        before = _all(es, active, v.id)
        assert before and all("context" not in h["_source"] for h in before)
        alias_before = set(es.indices.get_alias(name=cfg.read_alias))
        with session_factory() as s:
            assert rebuild.start(s, es, runtime.effective(s, cfg), {"context"}, shadow) == 1
            s.commit()
        handlers = {
            "rebuild_units": partial(
                rebuild.handle_rebuild_units,
                settings=cfg,
                store=store,
                es=es,
                llm=FakeLLM(4),
                session_factory=session_factory,
            )
        }
        assert run_once(session_factory, handlers)
        got = _all(es, shadow, v.id)
        assert got and all(h["_source"]["context"].startswith("A summary") for h in got)
        assert all(h["_source"]["visibility"] == "private" for h in got)
        assert (
            set(es.indices.get_alias(name=cfg.read_alias)) == alias_before == {active}
        )  # read alias untouched
        assert all("context" not in h["_source"] for h in _all(es, active, v.id))  # live index unchanged
        assert not es.indices.exists_alias(name=cfg.read_alias, index=shadow)
        d = db_session.get(Document, v.document_id)
        d.visibility = "everyone"  # acl_sync reaches the shadow through chunks-all
        db_session.commit()
        handle_acl_sync(None, {"doc_id": str(d.id)}, es=es, session_factory=session_factory)
        assert {h["_source"]["visibility"] for h in _all(es, shadow, v.id)} == {"everyone"}
        with pytest.raises(ValueError):
            rebuild.drop_shadow(es, active, active, cfg.read_alias)
        rebuild.drop_shadow(es, shadow, active, cfg.read_alias)
        assert not es.indices.exists(index=shadow) and es.indices.exists(index=active)
    finally:
        Elasticsearch(cfg.elastic_url).indices.delete(index=[shadow], ignore_unavailable=True)
