"""`roracle perf seed-pg | seed-es | load | explain` (opt-in tooling; scratch DB/index only)."""

import argparse
import json
import sys
from pathlib import Path

import yaml

_REPO_BUDGETS = Path(__file__).parents[4] / "docs/perf/budgets.yaml"
DEFAULT_BUDGETS = "docs/perf/budgets.yaml" if Path("docs/perf/budgets.yaml").exists() else str(_REPO_BUDGETS)


def add_parser(sub) -> None:
    p = sub.add_parser("perf", help="synthetic data and load tests (scratch DB/index only)")
    s = p.add_subparsers(dest="perf_cmd", required=True)

    pg = s.add_parser("seed-pg", help="seed a migrated scratch database (--url must name a *perf* DB)")
    pg.add_argument("--url", required=True)
    pg.add_argument("--docs", type=int, default=20_000)
    pg.add_argument("--users", type=int, default=100)
    pg.add_argument("--jobs-done", type=int, default=0)
    pg.add_argument("--jobs-queued", type=int, default=0)
    pg.add_argument("--audit-rows", type=int, default=0)
    pg.add_argument("--seed", type=int, default=1)

    es = s.add_parser("seed-es", help="seed a perf-* Elasticsearch index")
    es.add_argument("--es-url", default="http://localhost:9200")
    es.add_argument("--index", default="perf-chunks-1")
    es.add_argument("--chunks", type=int, default=100_000)
    es.add_argument("--dims", type=int, default=768)
    es.add_argument("--docs", type=int, default=20_000)
    es.add_argument("--users", type=int, default=100)
    es.add_argument("--alias", action="append", default=[], help="alias to add (repeatable)")
    es.add_argument("--seed", type=int, default=1)

    ld = s.add_parser("load", help="open-loop load test against a running API")
    ld.add_argument("scenario")
    ld.add_argument("--base-url", default="http://localhost:8000")
    ld.add_argument("--token", action="append", required=True, help="bearer token (repeatable)")
    ld.add_argument("--rps", type=float, default=10)
    ld.add_argument("--duration", type=int, default=30)
    ld.add_argument("--concurrency", type=int, default=32)
    ld.add_argument("--docs", type=int, default=1000)
    ld.add_argument("--budget", default=DEFAULT_BUDGETS)

    ex = s.add_parser("explain", help="time a hot statement (claim) on the scratch DB")
    ex.add_argument("what", choices=["claim"])
    ex.add_argument("--url", required=True)
    ex.add_argument("--iterations", type=int, default=500)
    ex.add_argument("--budget", default=DEFAULT_BUDGETS)


def _require_scratch(url: str) -> None:
    from sqlalchemy.engine import make_url

    db = make_url(url).database or ""
    if "perf" not in db:
        sys.exit(f"refusing to touch database {db!r}: scratch databases must contain 'perf' in the name")


def run(args: argparse.Namespace) -> int:
    match args.perf_cmd:
        case "seed-pg":
            from roracle.perf import synth_pg

            _require_scratch(args.url)
            counts = synth_pg.seed(
                args.url, docs=args.docs, users=args.users, jobs_done=args.jobs_done,
                jobs_queued=args.jobs_queued, audit_rows=args.audit_rows, seed=args.seed,
            )  # fmt: skip
            print(json.dumps(counts))
        case "seed-es":
            from elasticsearch import Elasticsearch

            from roracle.perf import synth_es

            n = synth_es.seed(
                Elasticsearch(args.es_url), args.index, chunks=args.chunks, dims=args.dims, docs=args.docs,
                personas={"users": args.users}, seed=args.seed, aliases=tuple(args.alias),
            )  # fmt: skip
            print(json.dumps({"chunks": n}))
        case "load":
            from roracle.perf import load
            from roracle.perf.report import gate

            r = load.run(
                args.base_url, args.token, args.scenario, rps=args.rps, duration_s=args.duration,
                concurrency=args.concurrency, docs=args.docs,
            )  # fmt: skip
            budget = _budgets(args.budget).get(args.scenario, {})
            ok = all(gate(r, v, q=q) for q, v in budget.items()) if budget else True
            print(json.dumps({**r.__dict__, "budget": budget, "pass": ok}))
            return 0 if ok else 1
        case "explain":
            return _explain_claim(args)
    return 2


def _budgets(path: str) -> dict:
    p = Path(path)
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def _explain_claim(args: argparse.Namespace) -> int:
    """Claim latency: each iteration claims one due job in a transaction and rolls back (jobs stay queued)."""
    import time

    from sqlalchemy import select, text

    from roracle.db import make_session_factory
    from roracle.models import Job
    from roracle.perf.explain import index_names, plan
    from roracle.perf.report import Report
    from roracle.queue import BULK_KINDS, claim

    _require_scratch(args.url)
    sf = make_session_factory(args.url)
    kinds = ["ingest", "acl_sync", *BULK_KINDS]
    lat: list[float] = []
    with sf() as s:
        for _ in range(args.iterations):
            t = time.perf_counter()
            claim(s, kinds)
            lat.append((time.perf_counter() - t) * 1000)
            s.rollback()
        user_plan = plan(
            s,
            select(Job.id).where(Job.status == "queued", Job.run_after <= text("now()"))
            .where(Job.kind != "rebuild_units").order_by(Job.run_after, Job.id).limit(1),
        )  # fmt: skip
        s.rollback()
    r = Report.of(lat)
    budget = _budgets(args.budget).get("claim", {})
    ok = all(getattr(r, q) <= v for q, v in budget.items())
    print(json.dumps({**r.__dict__, "indexes": sorted(index_names(user_plan)), "budget": budget, "pass": ok}))
    return 0 if ok else 1
