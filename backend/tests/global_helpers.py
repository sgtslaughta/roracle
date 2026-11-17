"""Shared fakes for the global-answer tests: ES that ignores the ACL (Postgres is the authority), a map/reduce LLM."""

import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from api_seed import seed

from roracle.auth import Principal
from roracle.chat import global_admit
from roracle.models import User
from roracle.models_labels import Label


class GES:
    """Honours ids / doc_id / no-unit filters (like ES would); ignores ACL on purpose."""

    def __init__(self, hits=()):
        self.hits, self.calls = list(hits), []

    def search(self, **kw):
        self.calls.append(kw)
        if "knn" in kw:
            return {"hits": {"hits": []}}
        flt = kw["query"]["bool"]["filter"]
        ids = next((f["ids"]["values"] for f in flt if "ids" in f), None)
        doc = next((f["term"]["doc_id"] for f in flt if "doc_id" in f.get("term", {})), None)
        docs = next((f["terms"]["doc_id"] for f in flt if "doc_id" in f.get("terms", {})), None)
        nounit = {"bool": {"must_not": [{"exists": {"field": "unit"}}]}} in flt
        out = [
            h for h in self.hits
            if (ids is None or h["_source"]["chunk_id"] in ids)
            and (doc is None or h["_source"]["doc_id"] == doc)
            and (docs is None or h["_source"]["doc_id"] in docs)
            and not (nounit and h["_source"].get("unit"))
        ]  # fmt: skip
        return {"hits": {"hits": out}}


def mk(doc, ver, key="0", text=None, unit=None, sources=(), pages=(2,)):
    return {
        "_source": {
            "chunk_id": f"{ver.id}:{key}", "doc_id": str(doc.id), "version_id": str(ver.id),
            "text": text or f"chunk {key} of {doc.title}", "pages": list(pages), "bboxes": [],
            "title": doc.title, "authors": ["Ann"], "year": 2020,
            **({"unit": unit, "source_chunks": [f"{ver.id}:{n}" for n in sources]} if unit else {}),
        }
    }  # fmt: skip


class LLM:
    """embeddings + chat; `reply(doc_title, messages)` decides each map answer; tracks concurrency."""

    def __init__(self, reply=None, delay=0.0):
        self.reply = reply or (lambda title, msgs: f"SECTION: d\nFinding about {title}")
        self.delay, self.calls, self.live, self.peak, self.lock = delay, [], 0, 0, threading.Lock()
        self.embeddings = SimpleNamespace(
            create=lambda model, input: SimpleNamespace(
                data=[SimpleNamespace(index=i, embedding=[0.1] * 768) for i in range(len(input))]
            )
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        title = kw["messages"][1]["content"].split("\n", 1)[0].removeprefix("Document: ")
        with self.lock:
            self.calls.append(kw)
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            time.sleep(self.delay)
            r = self.reply(title, kw["messages"])
        finally:
            with self.lock:
                self.live -= 1
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])


@pytest.fixture
def me(user):
    return Principal(user.id, "sub-1", "researcher", (), False)


@pytest.fixture
def cfg(settings):
    return settings.model_copy(update={"router": True, "global_timeout_s": 5.0})


def world(db, owner, n=5, **kw):
    docs = [seed(db, owner, title=f"Doc{i}", **kw) for i in range(n)]
    hits = []
    for d, v in docs:
        hits += [mk(d, v, "d", text=f"overview of {d.title}", unit="document", sources=(0, 1)),
                 mk(d, v, "0"), mk(d, v, "1", pages=(7,))]  # fmt: skip
    return docs, hits


def other_user(db) -> uuid.UUID:
    u = User(id=uuid.uuid4(), sub="sub-2", username="o", email="o@example.org")
    db.add(u)
    db.flush()
    return u.id


def label(db, name, **gates):
    lab = Label(name=name, colour="#123456", rank=50 + len(name), **gates)
    db.add(lab)
    db.flush()
    return lab


@pytest.fixture(autouse=True)
def fresh_slots(monkeypatch):
    monkeypatch.setattr(global_admit, "SLOTS", global_admit.Slots())


def run_build(db, es, cfg, me, llm, q="alpha doc"):
    cands = _ga().candidates(db, es, llm, cfg, me, q)
    notes = _ga().map_docs(llm, cfg, q, cands)
    return cands, notes, *_ga().build(db, es, cfg, me, q, notes, cands)


def _ga():
    from roracle.chat import global_answer

    return global_answer
