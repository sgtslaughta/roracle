import uuid
from types import SimpleNamespace

import pytest
from api_seed import seed
from conftest import bearer
from es_fakes import msearch as _es_msearch
from sqlalchemy import select

from roracle.eval import generate
from roracle.models import AuditLog, Job, User
from roracle.models_eval import EvalQuery, EvalResult, EvalRun, EvalSet
from roracle.models_labels import Label

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


@pytest.fixture
def world(db_session, client, adm, as_ana):
    client.get("/me", headers=adm)
    client.get("/me", headers=as_ana)
    ana = db_session.scalar(select(User).where(User.sub == "ana"))
    root = db_session.scalar(select(User).where(User.sub == "adm"))
    pub, _ = seed(db_session, root.id, title="Pub", visibility="everyone")
    priv, _ = seed(db_session, root.id, title="Priv", visibility="private")
    sec, _ = seed(db_session, root.id, title="Sec", visibility="everyone")
    sec.label_id = db_session.scalar(
        Label.__table__.insert()
        .values(name="NoLLM", colour="#111111", rank=1, exclude_llm=True)
        .returning(Label.id)
    )
    db_session.flush()
    return SimpleNamespace(ana=ana, root=root, pub=pub, priv=priv, sec=sec)


def mkset(client, adm, name="s", **kw):
    r = client.post("/admin/eval/sets", json={"name": name, **kw}, headers=adm)
    assert r.status_code == 201, r.text
    return r.json()


def body(w, **kw):
    return {
        "text": "what?", "principals": [str(w.ana.id)],
        "relevant": [{"doc": str(w.pub.id), "grade": 3}], **kw,
    }  # fmt: skip


def test_crud_and_validation(client, adm, world):
    w, s = world, mkset(client, adm)
    assert client.post("/admin/eval/sets", json={"name": "s"}, headers=adm).status_code == 409
    q = client.post(f"/admin/eval/sets/{s['id']}/queries", json=body(w, status="draft"), headers=adm)
    assert q.status_code == 201 and q.json()["status"] == "draft"
    qid = q.json()["id"]
    assert (
        client.get(f"/admin/eval/sets/{s['id']}/queries?status=draft", headers=adm).json()["items"][0]["id"]
        == qid
    )
    assert client.get("/admin/eval/sets", headers=adm).json()["items"][0]["drafts"] == 1
    r = client.patch(f"/admin/eval/queries/{qid}", json={"status": "active", "kind": "global"}, headers=adm)
    assert (r.json()["status"], r.json()["kind"]) == ("active", "global")
    bad_p = body(w, principals=[str(uuid.uuid4())])
    bad_d = body(w, relevant=[{"doc": str(uuid.uuid4())}])
    w.priv.deleted_at = w.priv.created_at
    gone = body(w, relevant=[{"doc": str(w.priv.id)}])
    for b in (bad_p, bad_d, gone):
        assert client.post(f"/admin/eval/sets/{s['id']}/queries", json=b, headers=adm).status_code == 422
    assert (
        client.patch(
            f"/admin/eval/queries/{qid}", json={"relevant": bad_d["relevant"]}, headers=adm
        ).status_code
        == 422
    )
    assert client.delete(f"/admin/eval/queries/{qid}", headers=adm).status_code == 204
    assert (
        client.patch(f"/admin/eval/sets/{s['id']}", json={"description": "d"}, headers=adm).json()[
            "description"
        ]
        == "d"
    )
    assert client.delete(f"/admin/eval/sets/{s['id']}", headers=adm).status_code == 204
    assert client.get(f"/admin/eval/sets/{s['id']}/queries", headers=adm).status_code == 404


def test_every_route_is_admin_only(client, adm, world, as_ana):
    s = mkset(client, adm)
    pat = client.post("/me/tokens", json={"name": "x", "scopes": ["read"]}, headers=adm).json()["token"]
    sid, u = s["id"], str(uuid.uuid4())
    calls = [
        ("get", "/admin/eval/sets", None), ("post", "/admin/eval/sets", {"name": "n"}),
        ("patch", f"/admin/eval/sets/{sid}", {}), ("delete", f"/admin/eval/sets/{sid}", None),
        ("get", f"/admin/eval/sets/{sid}/queries", None), ("post", f"/admin/eval/sets/{sid}/queries", body(world)),
        ("patch", f"/admin/eval/queries/{u}", {}), ("delete", f"/admin/eval/queries/{u}", None),
        ("post", f"/admin/eval/sets/{sid}/import", {"queries": []}),
        ("post", f"/admin/eval/sets/{sid}/generate", {"n": 1, "principal": u}),
        ("post", "/admin/eval/runs", {"set_id": sid}), ("get", "/admin/eval/runs", None),
        ("get", f"/admin/eval/runs/{u}", None),
    ]  # fmt: skip
    for h in (as_ana, bearer(pat)):  # a non-admin user, and a PAT (even an admin's)
        for m, path, b in calls:
            r = getattr(client, m)(path, headers=h, **({"json": b} if b is not None else {}))
            assert r.status_code == 403, (m, path, r.status_code)


def test_second_reference_unsets_first(client, adm):
    a, b = mkset(client, adm, "a", is_reference=True), mkset(client, adm, "b")
    r = client.patch(f"/admin/eval/sets/{b['id']}", json={"is_reference": True}, headers=adm)
    assert r.json()["is_reference"] is True
    ref = {x["name"]: x["is_reference"] for x in client.get("/admin/eval/sets", headers=adm).json()["items"]}
    assert ref == {"a": False, "b": True}
    assert mkset(client, adm, "c", is_reference=True)["is_reference"] is True
    assert sum(x["is_reference"] for x in client.get("/admin/eval/sets", headers=adm).json()["items"]) == 1
    assert a["name"] == "a"


def test_import_yaml_and_limits(client, adm, world):
    w, s = world, mkset(client, adm)
    doc = f"""
queries:
  - key: k1
    text: hello
    kind: fact
    principals: ["{w.ana.id}"]
    relevant: [{{doc: "{w.pub.id}", grade: 2}}]
"""
    r = client.post(f"/admin/eval/sets/{s['id']}/import", content=doc, headers=adm)
    assert r.json() == {"created": 1}
    assert client.post(f"/admin/eval/sets/{s['id']}/import", content=doc, headers=adm).status_code == 409
    assert (
        client.post(f"/admin/eval/sets/{s['id']}/import", content="queries: 5", headers=adm).status_code
        == 422
    )
    big = "queries: []\n#" + "x" * (2 * 1024 * 1024)
    assert client.post(f"/admin/eval/sets/{s['id']}/import", content=big, headers=adm).status_code == 413
    ghost = doc.replace(str(w.pub.id), str(uuid.uuid4())).replace("k1", "k2")
    assert client.post(f"/admin/eval/sets/{s['id']}/import", content=ghost, headers=adm).status_code == 422


class FakeES:
    def __init__(self, chunks):
        self.chunks = chunks

    msearch = _es_msearch

    def search(self, **kw):
        d = kw["query"]["function_score"]["query"]["bool"]["filter"][0]["term"]["doc_id"]
        return {"hits": {"hits": [{"_source": self.chunks[d]}]}}


class FakeLLM:
    def __init__(self):
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._c))

    def _c(self, messages, **_):
        self.prompts.append(messages[-1]["content"])
        txt = self.prompts[-1]
        reply = '{"question": "What does it say?", "quote": "%s"}' % (
            "alpha beta" if "alpha" in txt else "zzz"
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def test_generate_enqueues_and_job_respects_gates(client, adm, world, db_session, settings):
    w, s = world, mkset(client, adm)
    r = client.post(
        f"/admin/eval/sets/{s['id']}/generate", json={"n": 5, "principal": str(w.ana.id)}, headers=adm
    )
    assert r.status_code == 202
    job = db_session.get(Job, r.json()["job_id"])
    assert job.kind == "eval_generate"
    for n in (0, 201):
        assert (
            client.post(
                f"/admin/eval/sets/{s['id']}/generate", json={"n": n, "principal": str(w.ana.id)}, headers=adm
            ).status_code
            == 422
        )
    es = FakeES(
        {
            str(d.id): {"text": f"alpha beta gamma {n}", "pages": [1], "chunk_id": "c"}
            for n, d in enumerate((w.pub, w.priv, w.sec))
        }
    )
    llm = FakeLLM()
    generate.handle_eval_generate(db_session, job.payload, settings=settings, es=es, llm=llm)
    rows = db_session.scalars(select(EvalQuery).where(EvalQuery.set_id == uuid.UUID(s["id"]))).all()
    assert [(q.status, q.relevant[0]["doc"]) for q in rows] == [("draft", str(w.pub.id))]
    assert len(llm.prompts) == 1 and "gamma 0" in llm.prompts[0]  # priv invisible to ana, sec exclude_llm
    assert not any("gamma 2" in p for p in llm.prompts)
    # drafts stay out of runs until activated
    from roracle.eval import gold

    assert gold.load_db(db_session, uuid.UUID(s["id"])).queries == []
    rows[0].status = "active"
    db_session.flush()
    assert [q.key for q in gold.load_db(db_session, uuid.UUID(s["id"])).queries] == [rows[0].key]


def test_generate_rechecks_label_before_each_llm_call(client, adm, world, db_session, settings):
    w, s = world, mkset(client, adm)
    es_hits = {str(w.pub.id): {"text": "alpha beta", "pages": [1], "chunk_id": "c"}}

    class RaceES(FakeES):  # the label is raised after sampling (ES lag)
        msearch = _es_msearch

        def search(self, **kw):
            w.pub.label_id = w.sec.label_id
            db_session.flush()
            return super().search(**kw)

    llm = FakeLLM()
    generate.handle_eval_generate(
        db_session,
        {"set_id": s["id"], "principal": str(w.ana.id), "n": 3},
        settings=settings,
        es=RaceES(es_hits),
        llm=llm,
    )
    assert llm.prompts == []


def test_run_enqueue_listing_and_detail(client, adm, world, db_session, settings):
    w, s = world, mkset(client, adm)
    sid = s["id"]
    assert client.post("/admin/eval/runs", json={"set_id": sid}, headers=adm).status_code == 422  # no queries
    q = client.post(
        f"/admin/eval/sets/{sid}/queries", json=body(w, status="draft", key="k"), headers=adm
    ).json()
    assert (
        client.post("/admin/eval/runs", json={"set_id": sid}, headers=adm).status_code == 422
    )  # drafts excluded
    client.patch(f"/admin/eval/queries/{q['id']}", json={"status": "active"}, headers=adm)
    assert (
        client.post(
            "/admin/eval/runs", json={"set_id": sid, "path": "search", "judge": True}, headers=adm
        ).status_code
        == 422
    )
    r = client.post("/admin/eval/runs", json={"set_id": sid, "judge": True}, headers=adm)
    assert r.status_code == 202
    j = db_session.get(Job, r.json()["job_id"])
    assert (j.kind, j.payload["trigger"], j.payload["path"], j.payload["judge"]) == (
        "eval_run",
        "admin",
        "chat",
        True,
    )
    # R5: all-admin principals are refused
    s2 = mkset(client, adm, "adm-only")
    client.post(
        f"/admin/eval/sets/{s2['id']}/queries", json=body(w, principals=[str(w.root.id)]), headers=adm
    )
    r = client.post("/admin/eval/runs", json={"set_id": s2["id"]}, headers=adm)
    assert r.status_code == 422 and "non-admin" in r.json()["detail"]

    ov = {"p_ndcg@10": 0.5, "p_recall@10": 0.4, "pairs": 1}
    ids = []
    for n, (rec, started) in enumerate([(0.9, "2026-11-02"), (0.1, "2026-11-01")]):
        run = EvalRun(
            set_id=uuid.UUID(sid), set_name="s", trigger="admin", path="chat", status="done",
            metrics={"overall": {**ov, "p_recall@10": rec}, "by_kind": {"fact": ov}, "by_principal": {str(w.ana.id): ov}},
            started_at=f"{started}T00:00:00+00:00",
        )  # fmt: skip
        db_session.add(run)
        db_session.flush()
        ids.append(run.id)
        db_session.add(EvalResult(run_id=run.id, query_key="k", principal=str(w.ana.id), kind="fact",
                                  metrics={"p_recall@10": rec}, ranked=[["c1", str(w.pub.id)], ["c2", str(w.pub.id)]], latency_ms=1))  # fmt: skip
    items = client.get(f"/admin/eval/runs?set_id={sid}", headers=adm).json()["items"]
    assert [i["started_at"][:10] for i in items] == ["2026-11-01", "2026-11-02"]
    assert items[0]["metrics"]["overall"]["p_recall@10"] == 0.1
    assert client.get("/admin/eval/runs?limit=201", headers=adm).status_code == 422
    d = client.get(f"/admin/eval/runs/{ids[0]}", headers=adm).json()
    assert d["by_principal"] == {"ana": ov} and list(d["by_kind"]) == ["fact"]
    assert d["worst"][0]["principal"] == "ana" and d["worst"][0]["text"] == "what?"
    assert d["worst"][0]["top_docs"] == ["Pub"]


def test_audit_rows(client, adm, world, db_session):
    w, s = world, mkset(client, adm)
    q = client.post(f"/admin/eval/sets/{s['id']}/queries", json=body(w), headers=adm).json()
    client.patch(f"/admin/eval/queries/{q['id']}", json={"kind": "multihop"}, headers=adm)
    client.post(
        f"/admin/eval/sets/{s['id']}/generate", json={"n": 1, "principal": str(w.ana.id)}, headers=adm
    )
    client.delete(f"/admin/eval/queries/{q['id']}", headers=adm)
    client.delete(f"/admin/eval/sets/{s['id']}", headers=adm)
    acts = {a.action for a in db_session.scalars(select(AuditLog))}
    assert {
        "eval.set.create",
        "eval.query.create",
        "eval.query.update",
        "eval.generate",
        "eval.query.delete",
        "eval.set.delete",
    } <= acts
    assert db_session.scalar(select(EvalSet).limit(1)) is None


def test_patch_nulls_422_and_approval_rechecks_gold(client, adm, world):
    w, s = world, mkset(client, adm)
    q = client.post(f"/admin/eval/sets/{s['id']}/queries", json=body(w, status="draft"), headers=adm).json()
    for f in ("text", "kind", "status", "as_pat", "principals", "relevant"):
        assert (
            client.patch(f"/admin/eval/queries/{q['id']}", json={f: None}, headers=adm).status_code == 422
        ), f
    assert (
        client.patch(
            f"/admin/eval/queries/{q['id']}", json={"reference_answer": None}, headers=adm
        ).status_code
        == 200
    )
    assert client.patch(f"/admin/eval/sets/{s['id']}", json={"name": None}, headers=adm).status_code == 422
    w.pub.deleted_at = w.pub.created_at  # deleted since generation
    r = client.patch(f"/admin/eval/queries/{q['id']}", json={"status": "active"}, headers=adm)
    assert r.status_code == 422


def test_generate_refuses_admin_principal(client, adm, world, db_session, settings):
    w, s = world, mkset(client, adm)
    assert (
        client.post(
            f"/admin/eval/sets/{s['id']}/generate", json={"n": 1, "principal": str(w.root.id)}, headers=adm
        ).status_code
        == 422
    )
    with pytest.raises(generate.NonRetryable):
        generate.handle_eval_generate(
            db_session,
            {"set_id": s["id"], "principal": str(w.root.id), "n": 1},
            settings=settings,
            es=None,
            llm=None,
        )


def test_generate_sampler_excludes_units_and_uses_effective_index(world, db_session, settings):
    seen = {}

    class ES:
        msearch = _es_msearch

        def search(self, **kw):
            seen.update(kw)
            return {"hits": {"hits": []}}

    generate.sample_chunks(ES(), settings, ["d"])
    assert {"exists": {"field": "unit"}} in seen["query"]["function_score"]["query"]["bool"]["must_not"]
