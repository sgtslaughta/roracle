"""Version-lifecycle mutations of the chunk index (split from search_index.py): prune stale chunks/units of a
re-run and flag superseded versions as not latest. Re-exported by `search_index`."""

from elasticsearch import Elasticsearch

from roracle.config import AliasConfig

MUTATE_ALIAS = AliasConfig().mutate_alias


def delete_stale_chunks(
    es: Elasticsearch,
    version_id,
    keep: int,
    alias: str = MUTATE_ALIAS,
    keep_units: list[str] | None = None,
    refresh: bool = True,
) -> None:
    """Remove chunks of this version with n >= keep (a re-run may produce fewer chunks). Summary units are left
    alone unless `keep_units` is given: then ONE query also removes units not in it (raptor ingest)."""
    keep_ids = [f"{version_id}:{n}" for n in range(keep)]
    es.delete_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"version_id": str(version_id)}}],
                "must_not": [
                    {"ids": {"values": keep_ids + (keep_units or [])}},
                    *([] if keep_units is not None else [{"exists": {"field": "unit"}}]),
                ],
            }
        },
        refresh=refresh,
        conflicts="proceed",
    )


def delete_stale_units(
    es: Elasticsearch, version_id, keep_ids: list[str], alias: str = MUTATE_ALIAS, refresh: bool = True
) -> None:
    """Remove summary units of this version that were not just (re)written (`keep_ids` = their chunk ids)."""
    es.delete_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"version_id": str(version_id)}}, {"exists": {"field": "unit"}}],
                "must_not": [{"ids": {"values": keep_ids}}] if keep_ids else [],
            }
        },
        refresh=refresh,
        conflicts="proceed",
    )


def demote_version(es: Elasticsearch, version_id, alias: str = MUTATE_ALIAS, refresh: bool = True) -> None:
    """Flag every chunk and unit of ONE version as not latest (it was superseded while it was being indexed)."""
    es.update_by_query(
        index=alias,
        query={
            "bool": {"filter": [{"term": {"version_id": str(version_id)}}, {"term": {"is_latest": True}}]}
        },
        script={"source": "ctx._source.is_latest = false", "lang": "painless"},
        refresh=refresh,
        conflicts="proceed",
    )


def mark_previous_versions(
    es: Elasticsearch, doc_id, version: int, alias: str = MUTATE_ALIAS, refresh: bool = True
) -> None:
    """Flag every chunk of this document's versions older than `version` as not latest."""
    es.update_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"doc_id": str(doc_id)}}, {"range": {"version": {"lt": version}}}],
            }
        },
        script={"source": "ctx._source.is_latest = false", "lang": "painless"},
        refresh=refresh,
        conflicts="proceed",
    )
