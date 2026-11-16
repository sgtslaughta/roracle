"""Seed a gold-set fixture (users, labels, documents, shares, ES chunks) for `eval ci`.

Everything runs inside the caller's transaction (rolled back afterwards); ES gets its own throwaway index.
"""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roracle.config import Settings
from roracle.eval.gold import FixtureSet, GoldItem, GoldQuery, GoldSet
from roracle.ingest.chunking import Chunk
from roracle.ingest.embed import doc_prompt, embed, prompt_sha
from roracle.models import Blob, Document, DocumentShare, DocumentVersion, User
from roracle.models_labels import Label
from roracle.search_index import _mapping, acl_fields, index_chunks


def create_index(es, name: str, dims: int) -> None:
    es.indices.delete(index=name, ignore_unavailable=True)
    es.indices.create(index=name, mappings=_mapping(dims))


def seed(session: Session, es, llm, settings: Settings, fx: FixtureSet) -> GoldSet:
    """Insert the fixture and index it into `settings.embed_index`; returns the gold set with refs resolved
    to the real user and document UUIDs."""
    users = {
        k: User(sub=f"evalci:{k}", username=k, groups=list(v.groups), is_admin=v.admin)
        for k, v in fx.principals.items()
    }
    session.add_all(users.values())
    rank = (session.scalar(select(func.max(Label.rank))) or 0) + 1
    labels = {}
    for i, lab in enumerate(fx.labels):
        labels[lab.name] = Label(
            name=lab.name,
            colour="#884400",
            rank=rank + i,
            exclude_llm=lab.exclude_llm,
            block_agents=lab.block_agents,
        )
    session.add_all(labels.values())
    session.flush()

    docs: dict[str, Document] = {}
    for d in fx.documents:
        doc = Document(
            owner_id=users[d.owner].id, title=d.title, visibility=d.visibility, org_unit=d.org_unit or "/"
        )
        if d.label:
            doc.label_id = labels[d.label].id
        session.add(doc)
        session.flush()
        session.refresh(doc)  # the default label is a server-side expression
        session.add_all(
            DocumentShare(document_id=doc.id, principal_kind="user", principal=str(users[s].id))
            for s in d.shares
        )
        sha = hashlib.sha256(f"evalci:{d.key}".encode()).hexdigest()
        session.add(Blob(sha256=sha, size=1))
        session.flush()
        ver = DocumentVersion(
            document_id=doc.id, version=1, blob_sha256=sha, filename=f"{d.key}.pdf",
            content_type="application/pdf", size=1, stage="searchable", is_latest=True,
            page_count=max(max(c.pages) for c in d.chunks), meta={"authors": [], "year": 2024},
        )  # fmt: skip
        session.add(ver)
        session.flush()
        chunks = [Chunk(i, c.text, "", c.headings, c.pages, []) for i, c in enumerate(d.chunks)]
        prompts = [doc_prompt(d.title, c.text) for c in chunks]
        index_chunks(
            es, ver, doc, chunks, embed(llm, settings.embed_model, prompts, settings.embed_dims),
            acl_fields(session, doc), settings.embed_model, title=d.title,
            prompt_shas=[prompt_sha(x) for x in prompts], index=settings.embed_index,
        )  # fmt: skip
        docs[d.key] = doc
    session.flush()

    def item(g: GoldItem) -> GoldItem:
        return g.model_copy(update={"doc": str(docs[g.doc].id)})

    return GoldSet(
        name=fx.name or "fixture",
        queries=[
            GoldQuery(
                **q.model_dump(exclude={"principals", "relevant"}),
                principals=[str(users[r].id) for r in q.principals],
                relevant=[item(g) for g in q.relevant],
            )
            for q in fx.queries
        ],
    )
