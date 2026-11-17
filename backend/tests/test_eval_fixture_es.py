"""`eval ci` end to end against the dev Elasticsearch (fresh throwaway index) and a rolled-back Postgres transaction."""

import argparse
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from roracle.eval import ci, fakes, fixture, gold, runner
from roracle.models import Document
from roracle.models_eval import EvalResult
from roracle.models_labels import Label

pytestmark = pytest.mark.integration
FIX = Path(__file__).parent / "eval"


def test_eval_ci_on_fixture_has_no_violations_and_meets_floors(tmp_path):
    out = tmp_path / "eval-report.json"
    ns = argparse.Namespace(
        eval_cmd="ci", fixture=str(FIX / "fixture.yaml"), floors=str(FIX / "floors.json"), out=str(out)
    )
    assert ci.run(ns) == 0
    rep = json.loads(out.read_text())
    assert rep["status"] == "pass" and rep["violations"] == [] and rep["below_floor"] == []
    for path, m in rep["paths"].items():
        assert (m["acl_violations"], m["es_acl_violations"]) == (0, 0), path
        assert m["overall"]["pairs"] > 0 and m["empty_pairs"] > 0  # restricted gold really shrank for someone
    assert rep["paths"]["chat"]["judge"]["judged_pairs"] > 0


def test_pat_pairs_never_return_block_agents_docs(settings):
    cs = ci.ci_settings(settings)
    llm = fakes.CiLLM()
    from roracle import clients

    es = clients.es(settings)
    try:
        with ci.sandbox(cs) as s:
            fixture.create_index(es, ci.INDEX, fakes.DIMS)
            gs = fixture.seed(s, es, llm, cs, gold.load_file(FIX / "fixture.yaml"))
            internal = {
                str(i)
                for i in s.scalars(
                    select(Document.id).where(
                        Document.label_id.in_(select(Label.id).where(Label.block_agents))
                    )
                )
            }
            assert len(internal) == 1
            for path in ("chat", "search"):
                rid = runner.run_set(s, es, llm, cs, gs, path=path, trigger="ci")
                rows = list(s.scalars(select(EvalResult).where(EvalResult.run_id == rid)))
                pat = [r for r in rows if r.principal.endswith(":pat")]
                human = [r for r in rows if not r.principal.endswith(":pat")]
                assert len(pat) >= 5 and any(r.ranked for r in pat)
                assert not {d for r in pat for _, d in r.ranked} & internal, path
                assert {d for r in human for _, d in r.ranked} & internal, (
                    path
                )  # humans do get it: the check bites
    finally:
        es.indices.delete(index=ci.INDEX, ignore_unavailable=True)
