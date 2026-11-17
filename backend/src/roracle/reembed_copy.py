"""One page of the re-embed copy: Postgres-fresh ACL/search fields/signals, vectors reused or embedded, bulk, verify."""

from elasticsearch import helpers
from sqlalchemy import select

from roracle import search_index
from roracle.config import Settings
from roracle.ingest.embed import BATCH_RETRIES, chunk_prompt, doc_prompt, embed, prompt_sha
from roracle.models import DocumentVersion
from roracle.queue import check_lease
from roracle.reembed_converge import _pg_state, _uuid, _verify
from roracle.search_fields import fields_for
from roracle.search_signals import signals_for

_VECTORS = ["embedding", "summary_embedding"]


def _reusable(x: dict, key: str, target: dict) -> list[float] | None:
    """The source chunk's stored vector when it came from the target model and has the target dims."""
    v = x.get(key)
    return v if v and x.get("embedding_model") == target["model"] and len(v) == target["dims"] else None


def _copy(es, llm, sf, hits: list[dict], target: dict, source: str, settings: Settings | None = None) -> None:
    acls, live, newest = _pg_state(
        sf, [h["_source"] for h in hits]
    )  # AFTER the ES read: ACL never from source
    hits = [
        h for h in hits
        if acls[h["_source"]["doc_id"]] is not None and _uuid(h["_source"]["version_id"]) in live
    ]  # fmt: skip
    if not hits:
        return
    srcs = [h["_source"] for h in hits]
    vids = list({_uuid(x["version_id"]) for x in srcs})
    with sf() as s:
        summaries = dict(
            s.execute(
                select(DocumentVersion.id, DocumentVersion.summary).where(DocumentVersion.id.in_(vids))
            ).all()
        )
        fields = fields_for(s, vids)  # bibliographic fields + head signals: Postgres, never the source
        sigs = signals_for(s, list({u for x in srcs if (u := _uuid(x["doc_id"]))}))
    prompts = [
        doc_prompt(x["title"], x["text"])  # summary unit: same prompt as pipeline._index_units
        if x.get("unit")
        else chunk_prompt(
            x["title"],
            "\n".join([*(x.get("headings") or []), x["text"]]),  # = Chunk.contextual
            summaries.get(_uuid(x["version_id"])),
            x.get("context"),  # copied from the source chunk: the note is never regenerated here
        )
        for x in srcs
    ]
    conc = settings.embed_concurrency if settings is not None else 1
    sums = {
        i: x
        for i, x in enumerate(srcs)
        if x["chunk_id"].endswith(":0")
        and not x.get("unit")
        and (summaries.get(_uuid(x["version_id"])) or "").strip()
    }
    sum_prompts = {i: doc_prompt(x["title"], summaries[_uuid(x["version_id"])]) for i, x in sums.items()}
    vectors = {i: v for i, x in enumerate(srcs) if (v := _reusable(x, "embedding", target))}
    sv = {i: v for i, x in sums.items() if (v := _reusable(x, "summary_embedding", target))}
    need = [("c", i, p) for i, p in enumerate(prompts) if i not in vectors]
    need += [("s", i, p) for i, p in sum_prompts.items() if i not in sv]
    # chunk and summary prompts go out as ONE stream of requests, `embed_concurrency` in flight
    got = embed(
        llm, target["model"], [p for *_, p in need], target["dims"], concurrency=conc, retries=BATCH_RETRIES
    )
    for (kind, i, _), v in zip(need, got, strict=True):
        (vectors if kind == "c" else sv)[i] = v
    check_lease()  # last cooperative checkpoint before the page is written
    actions = []
    for i, (h, x, p) in enumerate(zip(hits, srcs, prompts, strict=True)):
        acl, title = acls[x["doc_id"]]
        doc = {k: val for k, val in x.items() if k not in _VECTORS}
        doc.update(acl, title=title)  # ACL/tags/title from the fresh Postgres read, never from the source
        doc["acl_hash"] = search_index.acl_hash(acl, title)  # describes exactly the fields written above
        doc["is_latest"] = bool(x.get("is_latest")) and x.get("version", 0) >= newest.get(x["doc_id"], 0)
        dfields, hfields = fields.get(x["version_id"], ({}, {}))
        doc.update(dfields)  # every chunk
        if x["chunk_id"].endswith(":0") and not x.get(
            "unit"
        ):  # head chunk: abstract + votes/cited/searchable
            doc.update({**hfields, **sigs.get(x["version_id"], {})})
        doc.update(embedding=vectors[i], embedding_model=target["model"], prompt_sha=prompt_sha(p))
        if i in sv:
            doc["summary_embedding"] = sv[i]
        actions.append({"_op_type": "index", "_index": target["index"], "_id": h["_id"], "_source": doc})
    # no explicit refresh: the target refreshes every COPY_REFRESH (and the cutover once more)
    _, errors = helpers.bulk(es, actions, refresh=False, raise_on_error=False)
    if errors:
        raise RuntimeError(f"re-embed bulk failed for {len(errors)} chunks: {errors[0]}")
    _verify(es, sf, target["index"], source, hits, acls)
