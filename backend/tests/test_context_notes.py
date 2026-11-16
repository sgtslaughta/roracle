"""P13 T5: contextual chunk notes (fake store/LLM, real Postgres for labels)."""

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from ingest_helpers import FakeLLM
from sqlalchemy import update

from roracle import retrieval
from roracle.config import Settings
from roracle.ingest import context
from roracle.ingest.chunking import Chunk
from roracle.ingest.embed import EMBED_PROMPT_VERSION, chunk_prompt, doc_prompt, prompt_sha
from roracle.labels.policy import Policy
from roracle.models import Document, DocumentVersion
from roracle.models_labels import Label

CFG = Settings(context_model="ctx", context_notes=True, context_doc_chars=24000, context_max_tokens=77)


class Store:
    def __init__(self):
        self.data = {}

    def get_bytes(self, key):
        if key not in self.data:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return self.data[key]

    def put_bytes(self, key, data, ct):
        self.data[key] = data


class SpyLLM(FakeLLM):
    def __init__(self, hook=None):
        super().__init__(4)
        self.calls, self.hook = [], hook
        self.chat.completions.create = self._spy

    def _spy(self, **kw):
        self.calls.append(kw)
        if self.hook:
            self.hook(len(self.calls))
        msg = SimpleNamespace(content="  Note  for\nchunk. " + "x" * 700)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def chunks(n=4, heads=("Intro", "Methods")):
    return [Chunk(i, f"chunk text {i}", f"ctx {i}", list(heads), [1], []) for i in range(n)]


@pytest.fixture
def vd(db_session, document, blob):
    v = DocumentVersion(
        document_id=document.id, version=1, blob_sha256=blob.sha256, filename="a.pdf",
        content_type="x", size=1,
    )  # fmt: skip
    db_session.add(v)
    db_session.flush()
    return v, document


def _label(db_session, **kw):
    lab = Label(name="NoLLM", colour="#aabbcc", rank=3, **kw)
    db_session.add(lab)
    db_session.flush()
    return lab


def test_prefix_identical_sequential_and_capped(db_session, vd):
    v, d = vd
    llm, store = SpyLLM(), Store()
    out = context.notes_for(db_session, llm, CFG, store, v, d, "the whole document", chunks())
    assert len(out) == 4 and all(len(n) <= 600 and n.startswith("Note for chunk.") for n in out)
    docs = {c["messages"][1]["content"] for c in llm.calls}
    assert docs == {"<document>\nthe whole document\n</document>"}  # one stable prefix
    assert [c["messages"][2]["content"].split("\n")[1] for c in llm.calls] == [
        f"chunk text {i}" for i in range(4)
    ]
    assert all(c["max_tokens"] == 77 and c["model"] == "ctx" and c["temperature"] == 0 for c in llm.calls)
    assert len(json.loads(store.data[f"derived/{v.id}/context-v1.json"])) == 4


def test_cache_hit_zero_calls(db_session, vd):
    v, d = vd
    store = Store()
    first = context.notes_for(db_session, SpyLLM(), CFG, store, v, d, "doc", chunks())
    llm = SpyLLM()
    assert context.notes_for(db_session, llm, CFG, store, v, d, "doc", chunks()) == first
    assert llm.calls == []


def test_exclude_llm_no_calls_heading_path(db_session, vd):
    v, d = vd
    d.label_id = _label(db_session, exclude_llm=True).id
    db_session.flush()
    llm, store = SpyLLM(), Store()
    out = context.notes_for(db_session, llm, CFG, store, v, d, "doc", chunks(2))
    assert llm.calls == [] and out == ["Intro > Methods"] * 2
    assert store.data == {}  # fallbacks are never cached


def test_label_raised_mid_job_stops_calls(db_session, vd):
    v, d = vd
    lab = _label(db_session, exclude_llm=True)

    def raise_label(n):
        if n == 2:  # the label lands after the second call (committed elsewhere)
            db_session.execute(update(Document).where(Document.id == d.id).values(label_id=lab.id))

    llm, store = SpyLLM(raise_label), Store()
    out = context.notes_for(db_session, llm, CFG, store, v, d, "doc", chunks(5))
    assert len(llm.calls) == 2 and out[2:] == ["Intro > Methods"] * 3
    assert len(json.loads(store.data[f"derived/{v.id}/context-v1.json"])) == 2


def test_progress_kept_on_failure(db_session, vd):
    v, d = vd
    store = Store()

    def boom(n):
        if n == 3:
            raise RuntimeError("llm down")

    with pytest.raises(RuntimeError):
        context.notes_for(db_session, SpyLLM(boom), CFG, store, v, d, "doc", chunks(4))
    assert len(json.loads(store.data[f"derived/{v.id}/context-v1.json"])) == 2
    llm = SpyLLM()  # retry only generates the rest
    context.notes_for(db_session, llm, CFG, store, v, d, "doc", chunks(4))
    assert len(llm.calls) == 2


def test_window_snaps_and_contains_chunk():
    text = "".join(f"[{i:05d}]" for i in range(2000))  # 14000 chars
    cs = [Chunk(i, text[i * 700 : i * 700 + 100], "", [], [], []) for i in range(20)]
    wins = [context.window(text, c, c.n, 20, 4000) for c in cs]
    assert all(len(w) == 4000 and c.text in w for w, c in zip(wins, cs, strict=True))
    assert len(set(wins)) < len(wins)  # neighbours share a window (prefix cache)


def test_prompt_version_bump_and_chunk_prompt():
    assert EMBED_PROMPT_VERSION == "2" and context.CONTEXT_PROMPT_VERSION == "1"
    import hashlib

    p = doc_prompt("T", "body", "sum")
    assert prompt_sha(p) != hashlib.sha256(p.encode()).hexdigest()  # v1 hash never matches
    assert chunk_prompt("T", "body", "sum", None) == p == chunk_prompt("T", "body", "sum", "  ")
    with_note = chunk_prompt("T", "body", "sum", "NOTE")
    assert "sum" not in with_note and with_note == doc_prompt("T", "NOTE\n\nbody")


def test_bm25_includes_context():
    seen = []

    class E:
        def search(self, index, **kw):
            seen.append(kw)
            return {"hits": {"hits": []}}

    p = SimpleNamespace(user_id="u", groups=(), is_admin=True, via="jwt")
    retrieval.search_chunks(
        E(), FakeLLM(2), Settings(embed_dims=2), p, "q", policy=Policy([]), filters={}, qvec=[0.0, 0.0]
    )
    fields = seen[0]["query"]["bool"]["must"][0]["multi_match"]["fields"]
    assert "context" in fields
