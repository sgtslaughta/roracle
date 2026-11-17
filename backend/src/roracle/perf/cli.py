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
    pg.add_argument(
        "--lsh", type=int, default=0, help="also seed minhash + LSH bands for the first N versions"
    )

    lsh = s.add_parser("seed-lsh", help="minhash + LSH bands for the first N seeded versions of a seeded DB")
    lsh.add_argument("--url", required=True)
    lsh.add_argument("--n", type=int, required=True)
    lsh.add_argument("--seed", type=int, default=1)

    es = s.add_parser("seed-es", help="seed a perf-* Elasticsearch index")
    es.add_argument("--es-url", default="http://localhost:9200")
    es.add_argument("--index", default="perf-chunks-1")
    es.add_argument("--chunks", type=int, default=100_000)
    es.add_argument("--dims", type=int, default=768)
    es.add_argument("--docs", type=int, default=20_000)
    es.add_argument("--users", type=int, default=100)
    es.add_argument("--alias", action="append", default=[], help="alias to add (repeatable)")
    es.add_argument("--label-id", default=None, help="default label id of the PG corpus (enables acl_hash)")
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

    rc = s.add_parser(
        "reconcile", help="run the drift-only ACL reconcile over the scratch corpus (set ROR_MUTATE_ALIAS)"
    )
    rc.add_argument("--url", required=True)
    rc.add_argument("--es-url", default="http://localhost:9200")
    rc.add_argument("--index", default="perf-chunks-1")

    su = s.add_parser("suite", help="every Task 10 budget against a scratch environment (CI gate / report)")
    su.add_argument("--profile", choices=["ci", "full"], default="ci")
    su.add_argument("--url", required=True, help="scratch Postgres URL (name must contain 'perf')")
    su.add_argument("--es-url", default="http://localhost:9200")
    su.add_argument(
        "--skip-seed", action="store_true", help="the scratch DB and perf-chunks-1 are already seeded"
    )
    su.add_argument("--out", default="perf-results.json")
    su.add_argument("--budget", default=DEFAULT_BUDGETS)
    su.add_argument(
        "--only", default="", help="comma-separated HTTP scenarios only (skips the in-process ones)"
    )
    su.add_argument("--rps", type=float, default=0, help="override the profile's request rate")
    su.add_argument("--duration", type=int, default=0, help="override the profile's measured seconds")
    su.add_argument("--embed-ms", type=float, default=10)
    su.add_argument("--rerank-ms", type=float, default=50)

    ex = s.add_parser("explain", help="time a hot statement (claim) on the scratch DB")
    ex.add_argument("what", choices=["claim"])
    ex.add_argument("--url", required=True)
    ex.add_argument("--iterations", type=int, default=500)
    ex.add_argument("--warmup", type=int, default=50, help="claims per worker discarded before measuring")
    ex.add_argument(
        "--workers", type=int, default=1, help="concurrent claiming sessions (SKIP LOCKED contention)"
    )
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
            if args.lsh:
                counts["version_lsh"] = synth_pg.seed_lsh(
                    args.url, n=min(args.lsh, args.docs), seed=args.seed
                )
            print(json.dumps(counts))
        case "seed-lsh":
            from roracle.perf import synth_pg

            _require_scratch(args.url)
            print(json.dumps({"version_lsh": synth_pg.seed_lsh(args.url, n=args.n, seed=args.seed)}))
        case "seed-es":
            from elasticsearch import Elasticsearch

            from roracle.perf import synth_es

            n = synth_es.seed(
                Elasticsearch(args.es_url), args.index, chunks=args.chunks, dims=args.dims, docs=args.docs,
                personas={"users": args.users}, seed=args.seed, aliases=tuple(args.alias),
                label_id=args.label_id,
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
        case "suite":
            from roracle.perf import suite

            prof = {**suite.PROFILES[args.profile]}
            if args.rps:
                prof["rps"], prof["rps_per"] = args.rps, {}
            prof["duration"] = args.duration or prof["duration"]
            return suite.run_suite(
                prof, only=set(filter(None, args.only.split(","))) or None, pg_url=args.url, es_url=args.es_url, seed=not args.skip_seed,
                budgets=suite.load_budgets(args.budget), out=Path(args.out), embed_ms=args.embed_ms,
                rerank_ms=args.rerank_ms,
            )  # fmt: skip
        case "explain":
            return _explain_claim(args)
        case "reconcile":
            return _reconcile(args)
    return 2


def _budgets(path: str) -> dict:
    p = Path(path)
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def claim_latencies(sf, kinds: list[str], iterations: int, warmup: int, workers: int = 1) -> list[float]:
    """ms per claim, `workers` sessions claiming concurrently (each claim rolled back: the jobs stay queued). The
    first `warmup` claims of every worker (connect, plan and page-cache warm-up) are discarded."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from roracle.queue import claim

    def work(_: int) -> list[float]:
        lat: list[float] = []
        with sf() as s:
            for i in range(warmup + max(1, iterations // workers)):
                t = time.perf_counter()
                claim(s, kinds)
                ms = (time.perf_counter() - t) * 1000
                s.rollback()
                if i >= warmup:
                    lat.append(ms)
        return lat

    with ThreadPoolExecutor(workers) as pool:
        return [x for part in pool.map(work, range(workers)) for x in part]


def _explain_claim(args: argparse.Namespace) -> int:
    """Claim latency: each iteration claims one due job in a transaction and rolls back (jobs stay queued)."""
    from roracle.db import make_session_factory
    from roracle.perf.explain import index_names, plan
    from roracle.perf.report import Report
    from roracle.queue import _USER_ONLY, BULK_KINDS, claim_stmt

    _require_scratch(args.url)
    sf = make_session_factory(args.url)
    kinds = ["ingest", "acl_sync", *BULK_KINDS]
    lat = claim_latencies(sf, kinds, args.iterations, args.warmup, args.workers)
    with sf() as s:
        user_plan = plan(s, claim_stmt([k for k in kinds if k not in BULK_KINDS], _USER_ONLY))
        s.rollback()
    r = Report.of(lat)
    budget = _budgets(args.budget).get("claim", {})
    ok = all(getattr(r, q) <= v for q, v in budget.items())
    print(json.dumps({**r.__dict__, "indexes": sorted(index_names(user_plan)), "budget": budget, "pass": ok}))
    return 0 if ok else 1


def _queued_repairs(url: str) -> int:
    from sqlalchemy import create_engine, text

    with create_engine(url).connect() as c:
        return (
            c.execute(
                text("SELECT count(*) FROM jobs WHERE kind = 'acl_sync' AND status = 'queued'")
            ).scalar()
            or 0
        )


def _reconcile(args: argparse.Namespace) -> int:
    """Walk the whole corpus with `handle_reconcile_tags`; report duration, batches and ES index operations
    (indexing.index_total delta of the scratch index: updates count, no-ops do not)."""
    import time

    from elasticsearch import Elasticsearch
    from sqlalchemy import delete, select

    from roracle.db import make_session_factory
    from roracle.maintenance import handle_reconcile_tags
    from roracle.models import Job

    _require_scratch(args.url)
    es = Elasticsearch(args.es_url)

    def ops() -> int:
        es.indices.refresh(index=args.index)
        return es.indices.stats(index=args.index, metric="indexing")["_all"]["primaries"]["indexing"][
            "index_total"
        ]

    before, t0, batches, payload = ops(), time.perf_counter(), 0, {}
    queued_before = _queued_repairs(args.url)
    sf = make_session_factory(args.url)
    with sf() as s:
        while True:
            handle_reconcile_tags(s, payload, es=es)
            batches += 1
            nxt = s.scalar(select(Job).where(Job.kind == "reconcile_tags").order_by(Job.id.desc()))
            s.execute(delete(Job).where(Job.kind == "reconcile_tags"))
            s.commit()
            if nxt is None:
                break
            payload = nxt.payload
    repairs = (
        _queued_repairs(args.url) - queued_before
    )  # drift found = acl_sync jobs enqueued (they do the writes)
    print(
        json.dumps(
            {"batches": batches, "seconds": round(time.perf_counter() - t0, 1), "es_writes": ops() - before,
             "drift_repairs_enqueued": repairs}
        )
    )  # fmt: skip
    return 0
