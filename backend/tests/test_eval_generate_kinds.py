import json
import uuid
from types import SimpleNamespace

import pytest
from api_seed import seed
from sqlalchemy import select
from test_admin_eval import adm, mkset, world  # noqa: F401  (fixtures)

from roracle.eval import curate, generate
from roracle.eval import generate_kinds as gk
from roracle.models import Job, User
from roracle.models_eval import EvalQuery

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class KindsES:
    """Sampling (function_score), similarity (bool.must match) and anchor (bool.filter doc_id) queries."""

    def __init__(self, chunks):
        self.chunks = chunks  # doc id -> text

    def _src(self, d):
        return {"_source": {"doc_id": d, "text": self.chunks[d], "pages": [1], "chunk_id": f"{d}:0"}}

    def search(self, **kw):
        q = kw["query"]
        if "function_score" in q:
            d = q["function_score"]["query"]["bool"]["filter"][0]["term"]["doc_id"]
            return {"hits": {"hits": [self._src(d)]}}
        if "must" in q["bool"]:
            seed_doc = q["bool"]["must_not"][1]["term"]["doc_id"]
            return {"hits": {"hits": [self._src(d) for d in sorted(self.chunks) if d != seed_doc]}}
        return {"hits": {"hits": [self._src(q["bool"]["filter"][0]["term"]["doc_id"])]}}


class ScriptLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._c))

    def _c(self, messages, **_):
        self.prompts.append(messages[-1]["content"])
        out = self.reply(messages[-1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=out))])


@pytest.fixture
def kw(world, db_session):  # noqa: F811
    w = world
    extra = [seed(db_session, w.root.id, title=f"E{i}", visibility="everyone")[0] for i in range(3)]
    texts = {str(d.id): f"topic alpha fact {i} here" for i, d in enumerate([w.pub, *extra])}
    texts |= {str(w.priv.id): "secret priv text", str(w.sec.id): "excluded sec text"}
    return SimpleNamespace(w=w, visible=[w.pub, *extra], es=KindsES(texts))


def _rows(db_session, sid):
    return db_session.scalars(select(EvalQuery).where(EvalQuery.set_id == uuid.UUID(sid))).all()


def test_api_passes_kind(client, adm, world, db_session):  # noqa: F811
    s = mkset(client, adm)
    url = f"/admin/eval/sets/{s['id']}/generate"
    r = client.post(url, json={"n": 2, "principal": str(world.ana.id), "kind": "global"}, headers=adm)
    assert db_session.get(Job, r.json()["job_id"]).payload["kind"] == "global"
    assert (
        client.post(url, json={"n": 2, "principal": str(world.ana.id), "kind": "x"}, headers=adm).status_code
        == 422
    )


def test_multihop_pairs_two_visible_llm_allowed_docs(client, adm, kw, db_session, settings):  # noqa: F811
    s = mkset(client, adm)

    def reply(txt):
        a, b = (part.strip().split("\n")[1] for part in txt.split("</passage>")[:2])
        return json.dumps({"question": "How do A and B compare?", "quote_a": a[:10], "quote_b": b[:10]})

    llm = ScriptLLM(reply)
    payload = {"set_id": s["id"], "principal": str(kw.w.ana.id), "n": 10, "kind": "multihop"}
    generate.handle_eval_generate(db_session, payload, settings=settings, es=kw.es, llm=llm)
    rows = _rows(db_session, s["id"])
    assert rows and all(q.kind == "multihop" and len(q.relevant) == 2 for q in rows)
    allowed = {str(d.id) for d in kw.visible}
    assert all(
        {g["doc"] for g in q.relevant} <= allowed and all(g["quote"] for g in q.relevant) for q in rows
    )
    assert not any("secret" in p or "excluded" in p for p in llm.prompts)


def test_global_doc_level_gold_needs_three(client, adm, kw, db_session, settings):  # noqa: F811
    s = mkset(client, adm)
    payload = {"set_id": s["id"], "principal": str(kw.w.ana.id), "n": 1, "kind": "global"}
    picks = iter([[1, 2, 3], [1, 2]])
    llm = ScriptLLM(lambda _: json.dumps({"question": "What do the papers report?", "passages": next(picks)}))
    generate.handle_eval_generate(db_session, payload, settings=settings, es=kw.es, llm=llm)
    generate.handle_eval_generate(db_session, payload, settings=settings, es=kw.es, llm=llm)
    rows = _rows(db_session, s["id"])
    assert [(q.kind, len(q.relevant)) for q in rows] == [("global", 3)]
    assert all("quote" not in g for g in rows[0].relevant)


def test_parse_multihop_rejects_quote_not_in_passage():
    a, b = {"doc_id": "a", "text": "alpha beta"}, {"doc_id": "b", "text": "gamma delta"}
    ok = '{"question": "q?", "quote_a": "alpha", "quote_b": "delta"}'
    assert gk.parse_multihop(ok, a, b, 100)[1][1] == {"doc": "b", "quote": "delta", "grade": 3}
    assert gk.parse_multihop(ok.replace("delta", "zeta"), a, b, 100) is None
    assert gk.parse_global('{"question": "q", "passages": [1, 9]}', [a, b]) is None


def test_curate_verifies_dedupes_widens(client, adm, kw, db_session, settings, as_bob):  # noqa: F811
    s, w = mkset(client, adm), kw.w
    client.get("/me", headers=as_bob)
    bob = db_session.scalar(select(User).where(User.sub == "bob"))
    pub, other = str(w.pub.id), str(kw.visible[1].id)
    for key, text in (
        ("a", "What is alpha fact zero?"),
        ("b", "What is alpha fact zero ?"),
        ("c", "Bad one?"),
        ("e", "What does the first study report about alpha?"),
    ):
        db_session.add(EvalQuery(
            set_id=uuid.UUID(s["id"]), key=key, text=text, kind="fact", principals=[w.ana.id],
            relevant=[{"doc": pub, "quote": "alpha", "grade": 3}], status="draft",
        ))  # fmt: skip
    db_session.add(EvalQuery(
        set_id=uuid.UUID(s["id"]), key="d", text="Compare?", kind="multihop", principals=[w.ana.id],
        relevant=[{"doc": pub, "quote": "alpha", "grade": 3}, {"doc": other, "quote": "fact", "grade": 3}],
        status="draft",
    ))  # fmt: skip
    db_session.flush()
    verdict = {"answerable": True, "needs_context": False, "single_passage": True}
    llm = ScriptLLM(lambda t: json.dumps({**verdict, "answerable": "Bad" not in t}))
    out = curate.curate(
        db_session, llm=llm, es=kw.es, settings=settings, set_id=uuid.UUID(s["id"]), widen=[bob.id]
    )
    assert out == {
        "fact": {"drafts": 4, "kept": 1, "dups": 1, "failed": 2},
        "multihop": {"drafts": 1, "failed": 1},
    }
    (kept,) = _rows(db_session, s["id"])
    assert kept.key == "a" and kept.status == "active" and kept.principals == [w.ana.id, bob.id]
    assert "topic alpha fact 0" in llm.prompts[0]  # the judge saw the anchor chunk
    assert len(llm.prompts) == 3  # no judge call for the duplicate or the deictic question
