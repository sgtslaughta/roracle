import argparse
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from es_fakes import msearch as _es_msearch

from roracle import runtime
from roracle.eval import ci, cli, compare, fakes, gold, runner
from roracle.models import User
from roracle.models_eval import EvalQuery, EvalRun, EvalSet


def args(**kw):
    return argparse.Namespace(
        **{
            "eval_cmd": "run",
            "judge": False,
            "set_name": "s",
            "fixture": None,
            "path": "chat",
            "label": "",
            "set_override": [],
            "index": "",
            "json_out": None,
        }
        | kw
    )


@pytest.fixture
def patched(monkeypatch, db_session, settings):
    @contextmanager
    def sf():
        yield db_session

    monkeypatch.setattr(cli, "make_session_factory", lambda url: sf)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(compare, "make_session_factory", lambda url: sf)
    monkeypatch.setattr(compare, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.clients, "es", lambda s: None)
    monkeypatch.setattr(cli.clients, "llm", lambda s: None)
    return db_session


def make_set(db, admin_only=False):
    u = User(id=uuid.uuid4(), sub="u", username="u", is_admin=admin_only)
    s = EvalSet(name="s")
    db.add_all([u, s])
    db.flush()
    db.add(
        EvalQuery(
            set_id=s.id,
            key="q",
            text="t",
            kind="fact",
            principals=[u.id],
            relevant=[{"doc": str(uuid.uuid4())}],
        )
    )
    db.flush()


def test_fixture_not_implemented_exit_2(patched, capsys):
    assert cli.run(args(set_name=None, fixture="x.yaml")) == 2
    assert "not implemented" in capsys.readouterr().err


def test_unknown_set_and_r5_exit_2(patched):
    assert cli.run(args(set_name="nope")) == 2
    make_set(patched, admin_only=True)
    assert cli.run(args()) == 2


def test_run_error_exit_1(patched, monkeypatch):
    make_set(patched)
    monkeypatch.setattr(runner, "run_set", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cli.run(args()) == 1


def test_uses_cutover_embedding(patched, monkeypatch):
    make_set(patched)
    runtime.set_active_embedding(patched, "cut-model", 4, "chunks-cut")
    seen = {}

    def fake(session, es, llm, settings, gs, **k):
        seen["s"] = settings
        raise RuntimeError("stop")

    monkeypatch.setattr(runner, "run_set", fake)
    cli.run(args())
    assert (seen["s"].embed_model, seen["s"].embed_index) == ("cut-model", "chunks-cut")


# ---- N3: --index validation ----------------------------------------------------------------------
class IdxES:
    def __init__(self, dims=768, model="embeddinggemma", exists=True, n=1, hits=True):
        self.n, self.exists = n, exists
        self.mapping = {
            f"i{k}": {"mappings": {"properties": {"embedding": {"dims": dims}}}} for k in range(n)
        }
        self.hits = [{"_source": {"embedding_model": model}}] if hits else []
        self.indices = SimpleNamespace(
            exists=lambda index: self.exists, get_mapping=lambda index: self.mapping
        )

    msearch = _es_msearch

    def search(self, **kw):
        return {"hits": {"hits": self.hits}}


@pytest.mark.parametrize(
    "es,msg",
    [
        (IdxES(exists=False), "does not exist"),
        (IdxES(n=2), "single index"),
        (IdxES(dims=4), "4 dims"),
        (IdxES(model="other"), "built with other"),
    ],
)
def test_validate_index_rejects(es, msg, settings):
    with pytest.raises(ValueError, match=msg):
        cli.validate_index(es, settings, "x")


def test_validate_index_accepts_matching_and_empty(settings):
    cli.validate_index(IdxES(model=settings.embed_model, dims=settings.embed_dims), settings, "x")
    cli.validate_index(IdxES(dims=settings.embed_dims, hits=False), settings, "x")


def test_run_bad_index_exit_2(patched, monkeypatch, capsys):
    make_set(patched)
    monkeypatch.setattr(cli.clients, "es", lambda s: IdxES(exists=False))
    assert cli.run(args(index="nope")) == 2
    assert "does not exist" in capsys.readouterr().err


def test_judge_needs_chat_path_exit_2(patched):
    make_set(patched)
    assert cli.run(args(judge=True, path="search")) == 2


# ---- compare ---------------------------------------------------------------------------------------
def cargs(tmp_path, **kw):
    return argparse.Namespace(
        **{
            "eval_cmd": "compare", "set_name": "s", "path": "chat", "baseline": "", "candidate": "rerank_depth=80",
            "candidate_index": "", "scope": "all", "latency_budget_ms": None, "judge": False,
            "out": str(tmp_path / "docs" / "eval" / "r.md"),
        } | kw
    )  # fmt: skip


def test_split_kv_keeps_json_commas():
    assert compare.split_kv('rerank_depth=80, chat_models=["a","b"],pats_enabled=true') == [
        "rerank_depth=80", 'chat_models=["a","b"]', "pats_enabled=true",
    ]  # fmt: skip
    assert compare.split_kv("") == []


def fake_runs(db, leaks=(0, 0)):
    ids = []

    def fake(session, es, llm, settings, gs, *, trigger, label, overrides, **k):
        r = EvalRun(
            set_name="s", trigger=trigger, label=label, path="chat", status="done",
            config={"overrides": overrides, "principals": {}},
            metrics={
                "overall": {"pairs": 1, "p_ndcg@10": 0.5}, "by_kind": {}, "by_principal": {}, "empty_pairs": 0,
                "acl_violations": leaks[len(ids)], "es_acl_violations": 0, "latency_p50_ms": 1, "latency_p95_ms": 2,
            },
        )  # fmt: skip
        db.add(r)
        db.flush()
        ids.append(r.id)
        return r.id

    return fake, ids


def test_compare_writes_report_and_runs_both_sides(patched, monkeypatch, tmp_path, capsys):
    make_set(patched)
    fake, _ = fake_runs(patched)
    seen = []
    monkeypatch.setattr(runner, "run_set", lambda *a, **k: (seen.append(k), fake(*a, **k))[1])
    assert compare.run(cargs(tmp_path)) == 0
    assert [(k["trigger"], k["label"], k["overrides"]) for k in seen[-2:]] == [
        ("compare", "baseline", {}), ("compare", "candidate", {"rerank_depth": 80}),
    ]  # fmt: skip
    md = (tmp_path / "docs" / "eval" / "r.md").read_text()
    assert "**Verdict: INSUFFICIENT**" in md and "| rerank_depth | - | 80 |" in md
    assert "verdict: INSUFFICIENT" in capsys.readouterr().out


def test_compare_acl_violation_exit_1_but_report_written(patched, monkeypatch, tmp_path):
    make_set(patched)
    fake, _ = fake_runs(patched, leaks=(0, 3))
    monkeypatch.setattr(runner, "run_set", fake)
    assert compare.run(cargs(tmp_path)) == 1
    assert "**Verdict: FAIL**" in (tmp_path / "docs" / "eval" / "r.md").read_text()


def test_compare_bad_input_exit_2(patched, tmp_path):
    assert compare.run(cargs(tmp_path, set_name="nope")) == 2
    make_set(patched)
    assert compare.run(cargs(tmp_path, candidate="rerank_depth=5")) == 2
    assert compare.run(cargs(tmp_path, judge=True, path="search")) == 2


# ---- ci --------------------------------------------------------------------------------------------
FIX = Path(__file__).parent / "eval"


def test_fixture_meets_the_spec_shape():
    fx = gold.load_file(FIX / "fixture.yaml")
    assert len(fx.documents) >= 30 and set(fx.principals) == {"alice", "bob", "carol", "root"}
    assert {d.visibility for d in fx.documents} == {"private", "shared", "org_unit", "everyone"}
    assert sorted(x.name for x in fx.labels if x.exclude_llm or x.block_agents) == ["Internal", "Secret"]
    kinds = [q.kind for q in fx.queries]
    assert (kinds.count("fact"), kinds.count("multihop"), kinds.count("global")) == (24, 8, 8)
    assert sum(q.as_pat for q in fx.queries) >= 5 and all(len(q.principals) >= 2 for q in fx.queries)
    assert set(json.loads((FIX / "floors.json").read_text())) == {"chat", "search"}


def fake_ci_run(db, acl=0, es_acl=0):
    def fake(session, es, llm, settings, gs, *, path, **k):
        r = EvalRun(
            set_name="evalci", trigger="ci", path=path, status="done",
            metrics={
                "overall": {"pairs": 4, "p_ndcg@10": 0.8, "p_recall@10": 1.0}, "judge": {"faithfulness": 0.9},
                "acl_violations": acl, "es_acl_violations": es_acl,
            },
        )  # fmt: skip
        db.add(r)
        db.flush()
        return r.id

    return fake


def test_execute_passes_at_and_fails_above_the_measured_floor(db_session, monkeypatch):
    monkeypatch.setattr(ci.runner, "run_set", fake_ci_run(db_session))
    gs = gold.GoldSet(name="x", queries=[])
    ok = ci.execute(
        db_session, None, None, None, gs, {"chat": {"p_ndcg@10": 0.8, "faithfulness": 0.9}, "search": {}}
    )
    assert ok["status"] == "pass" and ok["below_floor"] == []
    bad = ci.execute(db_session, None, None, None, gs, {"chat": {"p_ndcg@10": 0.81}, "search": {"nope": 0.1}})
    assert bad["status"] == "fail"
    assert [(b["path"], b["metric"]) for b in bad["below_floor"]] == [
        ("chat", "p_ndcg@10"),
        ("search", "nope"),
    ]
    assert bad["below_floor"][1]["measured"] is None  # a floor on an absent metric fails


@pytest.mark.parametrize("kw", [{"acl": 1}, {"es_acl": 1}])
def test_execute_fails_on_any_acl_violation_incl_es_only(db_session, monkeypatch, kw):
    monkeypatch.setattr(ci.runner, "run_set", fake_ci_run(db_session, **kw))
    r = ci.execute(
        db_session, None, None, None, gold.GoldSet(name="x", queries=[]), {"chat": {}, "search": {}}
    )
    assert r["status"] == "fail" and len(r["violations"]) == 2  # one per path


def ci_args(tmp_path, floors):
    f = tmp_path / "floors.json"
    f.write_text(json.dumps(floors))
    return argparse.Namespace(
        eval_cmd="ci", fixture=str(FIX / "fixture.yaml"), floors=str(f), out=str(tmp_path / "o.json")
    )


@pytest.fixture
def ci_world(monkeypatch, db_session):
    @contextmanager
    def sandbox(settings):
        yield db_session

    es = SimpleNamespace(indices=SimpleNamespace(delete=lambda **k: None))
    monkeypatch.setattr(ci, "sandbox", sandbox)
    monkeypatch.setattr(ci.clients, "es", lambda s: es)
    monkeypatch.setattr(ci, "wait_for_es", lambda es: None)
    monkeypatch.setattr(ci.fixture, "create_index", lambda *a: None)
    monkeypatch.setattr(ci.fixture, "seed", lambda *a: gold.GoldSet(name="x", queries=[]))
    monkeypatch.setattr(ci.runner, "run_set", fake_ci_run(db_session))


def test_ci_exit_codes(ci_world, tmp_path):
    assert ci.run(ci_args(tmp_path, {"chat": {"p_ndcg@10": 0.8}})) == 0
    out = json.loads((tmp_path / "o.json").read_text())
    assert out["status"] == "pass" and set(out["paths"]) == {"chat", "search"}
    assert ci.run(ci_args(tmp_path, {"chat": {"p_ndcg@10": 0.9}})) == 1  # floor above the measured value
    assert json.loads((tmp_path / "o.json").read_text())["below_floor"][0]["metric"] == "p_ndcg@10"


def test_ci_bad_input_exit_2(ci_world, tmp_path):
    assert ci.run(ci_args(tmp_path, {"bogus": {}})) == 2
    a = ci_args(tmp_path, {})
    a.fixture = str(tmp_path / "missing.yaml")
    assert ci.run(a) == 2


def test_ci_dispatch_and_offline_models():
    llm = fakes.CiLLM()
    [v1] = llm.embeddings.create(model="m", input=["spectral gaps"]).data
    [v2] = llm.embeddings.create(model="m", input=["spectral gaps"]).data
    assert v1.embedding == v2.embedding and len(v1.embedding) == 768
    assert abs(sum(x * x for x in v1.embedding) - 1) < 1e-9


def test_ci_error_still_writes_the_artifact(ci_world, tmp_path, monkeypatch):
    monkeypatch.setattr(ci.runner, "run_set", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ci.run(ci_args(tmp_path, {})) == 1
    out = json.loads((tmp_path / "o.json").read_text())
    assert out["status"] == "error" and "boom" in out["error"]
