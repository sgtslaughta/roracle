"""Elasticsearch chunk index: `chunks-<model-slug>-<gen>`; read alias `chunks` (one index), mutation alias
`chunks-all` (all live generations, ADR-0002 ACL fields)."""

import logging
import re
import time
import weakref
from typing import TYPE_CHECKING, Any

from elasticsearch import Elasticsearch, NotFoundError, helpers
from elasticsearch.exceptions import BadRequestError
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from roracle import runtime
from roracle.config import AliasConfig
from roracle.models import Document, DocumentCoauthor, DocumentShare, DocumentVersion
from roracle.queue import NonRetryable
from roracle.search_fields import SEARCH_FIELDS_VERSION, SEARCH_PROPS, doc_fields, head_fields

if TYPE_CHECKING:  # runtime import would be circular (ingest/__init__ -> pipeline -> here)
    from roracle.ingest.chunking import Chunk
    from roracle.ingest.raptor import Unit

_aliases = AliasConfig()  # ROR_READ_ALIAS / ROR_MUTATE_ALIAS (Settings fields; both or neither)
ALIAS = _aliases.read_alias  # read alias: exactly one (the active) index
ALIAS_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('reembed-alias', 0))"  # cutover vs ensure_index
# every live generation: ALL update_by_query / delete_by_query go here
MUTATE_ALIAS = _aliases.mutate_alias


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def index_name(model: str, gen: int = 1) -> str:
    return f"chunks-{slug(model)}-{gen}"


def active_index(settings: Any) -> str:
    """Index that kNN/BM25 reads and bulk writes target: persisted by re-embed cutover (R4), else the env default."""
    return settings.embed_index or index_name(settings.embed_model)


def vector_index_options(settings: Any = None) -> dict[str, Any]:
    """Explicit `index_options` (never rely on the server default: ES 9.1+ silently picks bbq_hnsw for >= 384 dims).
    bbq_hnsw adds float rescoring of `vector_rescore_oversample` x k candidates; int8 stays the default."""
    kind = getattr(settings, "vector_index_type", "int8_hnsw")
    opts: dict[str, Any] = {"type": kind}
    if kind == "bbq_hnsw":
        opts["rescore_vector"] = {"oversample": getattr(settings, "vector_rescore_oversample", 3.0)}
    return opts


def index_settings(settings: Any = None) -> dict[str, Any]:
    """Settings for a NEW index: shards/replicas, refresh interval, ACORN filtered kNN, vectors out of `_source`."""
    g = lambda k, d: getattr(settings, k, d)
    index: dict[str, Any] = {
        "number_of_shards": g("es_shards", 1),
        "number_of_replicas": g("es_replicas", 1),
        "refresh_interval": g("es_refresh_interval", "5s"),
        "dense_vector": {"hnsw_filter_heuristic": g("es_hnsw_filter_heuristic", "acorn")},
    }
    if g("es_exclude_source_vectors", True):
        index["mapping"] = {"exclude_source_vectors": True}
    return {"index": index}


def _mapping(dims: int, settings: Any = None) -> dict[str, Any]:
    kw = {"type": "keyword"}
    vec = {
        "type": "dense_vector",
        "dims": dims,
        "index": True,
        "similarity": "cosine",
        "index_options": vector_index_options(settings),
    }
    return {
        "_meta": {"search_fields": SEARCH_FIELDS_VERSION},
        "properties": {
            **SEARCH_PROPS,
            "doc_id": kw,
            "version_id": kw,
            "version": {"type": "integer"},
            "is_latest": {"type": "boolean"},
            "chunk_id": kw,
            "owner_id": kw,
            "visibility": kw,
            "org_unit": kw,
            "label_id": kw,
            "acl_hash": kw,  # acl_hash(): lets the reconcile find drift without rewriting anything
            "share_users": kw,
            "share_groups": kw,
            "coauthor_users": kw,
            "title": {"type": "text", "analyzer": "english"},
            "authors": kw,
            "tags": kw,
            "file_type": kw,
            "collections": kw,
            "year": {"type": "integer"},
            "text": {"type": "text", "analyzer": "english"},
            "headings": {"type": "text", "analyzer": "english"},
            "context": {"type": "text", "analyzer": "english"},
            "unit": kw,  # "section" | "document"; absent = ordinary chunk
            "source_chunks": kw,
            "pages": {"type": "integer"},
            "bboxes": {"type": "object", "enabled": False},
            "embedding_model": kw,
            "prompt_sha": kw,
            "embedding": vec,
            "summary_embedding": vec,
        },
    }


def _adopt_plain_index(
    es: Elasticsearch, name: str, dims: int, alias: str = ALIAS, settings: Any = None
) -> None:
    """A concrete index called `chunks` (pre-alias deployment) blocks the read alias: copy it into `name`,
    then drop it and add both aliases in ONE atomic update_aliases. Idempotent (same `_id`s)."""
    if not es.indices.exists(index=alias) or es.indices.exists_alias(name=alias):
        return
    if not es.indices.exists(index=name):
        es.indices.create(index=name, mappings=_mapping(dims, settings), settings=index_settings(settings))
    r = (
        es.reindex(source={"index": alias}, dest={"index": name}, refresh=True, wait_for_completion=True)
        or {}
    )
    if r.get("failures"):
        raise RuntimeError(f"legacy reindex failed: {r['failures'][:1]}")
    es.indices.update_aliases(
        actions=[
            {"remove_index": {"index": alias}},
            {"add": {"index": name, "alias": alias}},
            {"add": {"index": name, "alias": MUTATE_ALIAS}},
        ]
    )


def _holders(es: Elasticsearch, alias: str) -> list[str]:
    try:
        return list(es.indices.get_alias(name=alias))
    except NotFoundError:
        return []


def _point_read_alias(
    es: Elasticsearch, name: str, alias: str, sf: sessionmaker[Session] | None, settings: Any
) -> None:
    """Make `alias` (exclusive) point at `name`. With `sf`, only when `name` is the active index persisted in
    Postgres, read fresh under the cutover's advisory lock: a stale worker never moves it back (S5)."""
    if es.indices.exists_alias(name=alias, index=name):
        return

    def move() -> None:
        es.indices.update_aliases(
            actions=[
                *({"remove": {"index": i, "alias": alias}} for i in _holders(es, alias)),
                {"add": {"index": name, "alias": alias}},
            ]
        )

    if sf is None:  # unguarded (tests/tools): add-only, never steal the alias from an existing holder
        if not _holders(es, alias):
            move()
        return
    with sf() as s:
        s.execute(text(ALIAS_LOCK))  # held until this session closes (rollback)
        if active_index(runtime.effective(s, settings)) == name:
            move()


_ENSURED: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()  # transport -> {key: monotonic time}


def _memo_for(es) -> dict | None:
    """Per-connection memo (`es.options()` clones share the transport); None for objects that cannot be weak keys."""
    try:
        return _ENSURED.setdefault(getattr(es, "transport", es), {})
    except TypeError:
        return None


ENSURE_TTL_S = 30.0  # the idempotent existence/mapping/alias checks (4-6 metadata calls) run once per window


def ensure_index(
    es: Elasticsearch,
    model: str,
    dims: int,
    alias: str = ALIAS,
    name: str | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Any = None,
    create: bool = True,
) -> str:
    """`_ensure_index`, memoized per (client, index, dims, alias, create) for ENSURE_TTL_S: an ingest does not repeat
    the metadata round trips for every document. Failures are never memoized; the unguarded mode (no
    `session_factory`: tools/tests, which repair the read alias) is never memoized."""
    name = name or index_name(model)
    key = (name, dims, alias)
    memo = (
        _memo_for(es) if session_factory is not None and create else None
    )  # `create=False` (shadow rebuild)
    if memo is not None and (at := memo.get(key)) is not None and time.monotonic() - at < ENSURE_TTL_S:
        return name  # ...must re-check that the shadow still exists: a bulk into a dropped one would auto-create it
    out = _ensure_index(
        es, model, dims, alias, name, session_factory=session_factory, settings=settings, create=create
    )
    if memo is not None:
        memo[key] = time.monotonic()
    return out


def _ensure_index(
    es: Elasticsearch,
    model: str,
    dims: int,
    alias: str,
    name: str,
    *,
    session_factory: sessionmaker[Session] | None,
    settings: Any,
    create: bool,
) -> str:
    """Create the index and add it to `chunks-all`. The read alias is (re)pointed here only when no
    `session_factory` is given (tests) or `name` is the Postgres-persisted active index."""
    _adopt_plain_index(es, name, dims, alias, settings)
    if not create and not es.indices.exists(index=name):
        raise NonRetryable(f"index {name!r} no longer exists")  # shadow dropped mid-rebuild: never re-created
    if not es.indices.exists(index=name):
        try:
            es.indices.create(
                index=name, mappings=_mapping(dims, settings), settings=index_settings(settings)
            )
        except BadRequestError as e:
            if e.error != "resource_already_exists_exception":
                raise
    if (
        settings is not None and create
    ):  # dynamic setting: existing (8.x-era) indices get ACORN without a re-embed;
        # not for the shadow rebuild path (`create=False`), which runs this for every document
        try:
            es.indices.put_settings(
                index=name,
                settings={
                    "index": {
                        "dense_vector": {
                            "hnsw_filter_heuristic": index_settings(settings)["index"]["dense_vector"][
                                "hnsw_filter_heuristic"
                            ]
                        }
                    }
                },
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "could not set hnsw_filter_heuristic on %s", name, exc_info=True
            )
    props = es.indices.get_mapping(index=name)[name]["mappings"].get("properties", {})
    if (
        props.get("embedding", {}).get("dims") != dims
        or props.get("summary_embedding", {}).get("dims", dims) != dims
    ):
        raise NonRetryable("search index dimension mismatch: reindex required")
    missing = {
        f: t
        for f, t in _mapping(dims)["properties"].items()
        if f not in props
        and (t["type"] in ("keyword", "dense_vector") or f == "context" or f in SEARCH_PROPS)
    }
    if missing:  # fields added after the index was created (R9)
        es.indices.put_mapping(index=name, properties=missing)
    if not es.indices.exists_alias(name=MUTATE_ALIAS, index=name):
        es.indices.update_aliases(actions=[{"add": {"index": name, "alias": MUTATE_ALIAS}}])
    _point_read_alias(es, name, alias, session_factory, settings)
    return name


def acl_fields(session: Session, document: Document) -> dict[str, Any]:
    shares = session.execute(
        select(DocumentShare.principal_kind, DocumentShare.principal).where(
            DocumentShare.document_id == document.id
        )
    ).all()
    coauthors = session.scalars(
        select(DocumentCoauthor.user_id).where(DocumentCoauthor.document_id == document.id)
    )
    return {
        "owner_id": str(document.owner_id),
        "coauthor_users": sorted(str(u) for u in coauthors),
        "visibility": document.visibility,
        "org_unit": document.org_unit,
        "share_users": sorted(p for k, p in shares if k == "user"),
        "share_groups": sorted(p for k, p in shares if k == "group"),
        "label_id": str(document.label_id),
    }


def file_type(filename: str | None) -> str:
    """Lowercase extension without dot; "other" when absent."""
    ext = (filename or "").rsplit(".", 1)[1].lower() if "." in (filename or "") else ""
    return ext if re.fullmatch(r"[a-z0-9]{1,8}", ext) else "other"


def _base(version, document, acl, model, title, is_latest, tags) -> dict[str, Any]:
    """Fields shared by a document's chunks and summary units (parity: ACL, label, latest, tags)."""
    meta = version.meta or {}
    return {
        **acl,
        "acl_hash": acl_hash({**acl, "tags": tags or []}, title),
        "doc_id": str(document.id),
        "version_id": str(version.id),
        "version": version.version,
        "is_latest": version.is_latest if is_latest is None else is_latest,
        "title": title,
        "authors": meta.get("authors") or [],
        "year": meta.get("year"),
        "tags": tags or [],
        "file_type": file_type(version.filename),
        "embedding_model": model,
        **doc_fields(version, document),
    }


def index_chunks(
    es: Elasticsearch,
    version: DocumentVersion,
    document: Document,
    chunks: "list[Chunk]",
    vectors: list[list[float]],
    acl: dict[str, Any],
    model: str,
    *,
    title: str,
    is_latest: bool | None = None,
    tags: list[str] | None = None,
    summary_vector: list[float] | None = None,
    prompt_shas: list[str] | None = None,
    index: str | None = None,
    contexts: list[str] | None = None,
    refresh: bool | str = "wait_for",
    signals: dict | None = None,
) -> int:
    """Bulk index with deterministic `_id` (re-runs overwrite); raise on any item error."""
    base = _base(version, document, acl, model, title, is_latest, tags)
    actions = [
        {
            "_op_type": "index",
            "_index": index or index_name(model),
            "_id": f"{version.id}:{c.n}",
            "_source": {
                **base,
                "chunk_id": f"{version.id}:{c.n}",
                "text": c.text,
                "headings": c.headings,
                "pages": c.pages,
                "bboxes": c.bboxes,
                "embedding": v,
                **({"prompt_sha": prompt_shas[i]} if prompt_shas else {}),
                **({"context": contexts[i]} if contexts and contexts[i] else {}),
                **({"summary_embedding": summary_vector} if c.n == 0 and summary_vector else {}),
                **({**head_fields(version), **(signals or {})} if c.n == 0 else {}),
            },
        }
        for i, (c, v) in enumerate(zip(chunks, vectors, strict=True))
    ]
    ok, errors = helpers.bulk(es, actions, refresh=refresh, raise_on_error=False)
    if errors:
        raise RuntimeError(f"bulk indexing failed for {len(errors)} chunks: {errors[0]}")
    return ok


def index_units(
    es: Elasticsearch,
    version: DocumentVersion,
    document: Document,
    units: "list[Unit]",
    vectors: list[list[float]],
    acl: dict[str, Any],
    model: str,
    *,
    title: str,
    is_latest: bool | None = None,
    tags: list[str] | None = None,
    index: str | None = None,
    prompt_shas: list[str] | None = None,
    refresh: bool | str = "wait_for",
) -> int:
    """Bulk index per-document summary units (same ACL/label fields as the document's chunks)."""
    base = _base(version, document, acl, model, title, is_latest, tags)
    actions = [
        {
            "_op_type": "index",
            "_index": index or index_name(model),
            "_id": f"{version.id}:{u.key}",
            "_source": {
                **base,
                "chunk_id": f"{version.id}:{u.key}",
                "unit": u.kind,
                "source_chunks": u.source_chunks,
                "text": u.text,
                "headings": u.headings,
                "pages": u.pages,
                "bboxes": [],
                "embedding": v,
                **({"prompt_sha": prompt_shas[i]} if prompt_shas else {}),
            },
        }
        for i, (u, v) in enumerate(zip(units, vectors, strict=True))
    ]
    ok, errors = helpers.bulk(es, actions, refresh=refresh, raise_on_error=False)
    if errors:
        raise RuntimeError(f"bulk indexing failed for {len(errors)} units: {errors[0]}")
    return ok


def cached_vectors(
    es: Elasticsearch, model: str, doc_id, shas: list[str], dims: int, index: str | None = None
) -> dict[str, list[float]]:
    """Embeddings already indexed for this document under the same prompt hash. Scope is the one
    document only: cross-document reuse would leak content existence via timing."""
    out: dict[str, list[float]] = {}
    for i in range(0, len(shas), 1000):
        batch = shas[i : i + 1000]
        try:
            r = es.search(
                index=index or index_name(model),
                query={
                    "bool": {"filter": [{"term": {"doc_id": str(doc_id)}}, {"terms": {"prompt_sha": batch}}]}
                },
                source={
                    "includes": ["prompt_sha", "embedding"],
                    "exclude_vectors": False,
                },  # ES >= 9.2 hides them
                collapse={"field": "prompt_sha"},
                size=10000,
            )
        except NotFoundError:
            return {}
        except BadRequestError as e:
            if "no mapping found" in str(e):
                return {}
            raise
        for h in r["hits"]["hits"]:
            src = h["_source"]
            vec = src.get("embedding")
            if vec and len(vec) == dims:
                out[src["prompt_sha"]] = vec
    return out


from roracle.search_index_acl import (  # noqa: F401 - re-exported: callers keep using `search_index.*`
    _ACL_SCRIPT,
    ACL_BATCH,
    ACL_KEYS,
    MAX_SYNC_TRIES,
    _acl_request,
    acl_hash,
    acl_payload,
    delete_doc_chunks,
    run_update_by_query,
    sync_acl,
    sync_acl_many,
)
from roracle.search_index_versions import (  # noqa: F401
    delete_stale_chunks,
    delete_stale_units,
    demote_version,
    mark_previous_versions,
)
