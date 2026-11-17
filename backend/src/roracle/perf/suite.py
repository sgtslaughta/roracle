"""`roracle perf suite`: every Task 10 budget against a seeded scratch environment, one command, one verdict.

HTTP scenarios (search, filtered search, browse, chat retrieval, MCP search, document read) run against a real API
process whose OIDC settings point at a local JWKS (see tokens.py) and whose models are `fake_models` (fixed latency);
claim, near-dup, ingest overhead, reconcile and rebuild run in-process on the same Postgres / Elasticsearch.
Profiles: `ci` (small, fixed, budgets loosened by `factor` for shared runners) and `full` (sizes from the CLI, factor 1)."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import yaml
from elasticsearch import Elasticsearch
from sqlalchemy import text

from roracle.perf import fake_models, ingest_bench, load, rebuild_bench, synth_es, synth_pg, tokens
from roracle.perf.report import Report, check

_CI = {"docs": 20_000, "chunks": 20_000, "users": 100, "lsh": 20_000, "jobs_done": 100_000, "jobs_queued": 10_000,
       "factor": 1.5, "rps": 8, "rps_per": {"search_filtered": 4}, "duration": 20, "warmup": 5, "ingest_docs": 15, "nd_samples": 60, "rebuild_chunks": 6_000}  # fmt: skip
PROFILES = {
    "ci": _CI,
    "full": {**_CI, "docs": 1_000_000, "chunks": 1_000_000, "users": 1000, "lsh": 1_000_000, "jobs_done": 3_000_000,
             "jobs_queued": 100_000, "factor": 1.0, "rps": 10, "rps_per": {"search_filtered": 5}, "duration": 60, "warmup": 20, "ingest_docs": 40,
             "nd_samples": 200, "rebuild_chunks": 60_000},
}  # fmt: skip
SCENARIOS = ("search", "search_filtered", "browse", "chat_retrieve", "mcp_search", "doc_read")
READ, MUTATE, INDEX = "perf-chunks", "perf-chunks-all", "perf-chunks-1"


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(pg: str, es_url: str, jwks_url: str, models_url: str) -> dict[str, str]:
    return {
        "ROR_DATABASE_URL": pg, "ROR_ELASTIC_URL": es_url, "ROR_S3_ENDPOINT": "http://127.0.0.1:1",
        "ROR_S3_ACCESS_KEY": "perf", "ROR_S3_SECRET_KEY": "perf", "ROR_OIDC_ISSUER": "http://perf.invalid/realm",
        "ROR_OIDC_JWKS_URL": jwks_url, "ROR_READ_ALIAS": READ, "ROR_MUTATE_ALIAS": MUTATE,
        "ROR_LLM_BASE_URL": f"{models_url}/v1", "ROR_EMBED_MODEL": "perf", "ROR_EMBED_DIMS": "768",
        "ROR_RERANK_URL": f"{models_url}/rerank", "ROR_RERANK_API": "tei", "ROR_RERANK_MODEL": "perf",
        "ROR_SEARCH_MIN_SIMILARITY": "0", "ROR_CHAT_MIN_SIMILARITY": "0", "ROR_WORKER_HEALTH_PORT": "0",
    }  # fmt: skip


def _row(name: str, spec: dict, values: dict, factor: float, note: str = "") -> dict:
    ok, text = check(spec, values, factor) if spec else (True, "no budget")
    print(f"{'PASS' if ok else 'FAIL'} {name}: {text}", flush=True)
    return {"name": name, "ok": ok, "detail": text, "values": values, "note": note}


def _wait(url: str, secs: int = 90) -> None:
    for _ in range(secs * 2):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become healthy")


def run_suite(profile: dict, *, pg_url: str, es_url: str, budgets: dict, seed: bool, out: Path,
              embed_ms: float = 10, rerank_ms: float = 50, only: set[str] | None = None) -> int:  # fmt: skip
    from roracle.config import Settings
    from roracle.db import make_session_factory
    from roracle.perf.cli import _require_scratch, claim_latencies

    _require_scratch(pg_url)
    es = Elasticsearch(es_url, request_timeout=120)
    f = profile["factor"]
    rows: list[dict] = []
    if seed:
        _seed(profile, pg_url, es)
    work = Path(tempfile.mkdtemp(prefix="perf-suite-"))
    jwks, toks = tokens.mint(min(profile["users"], 200), issuer="http://perf.invalid/realm")
    (work / "jwks.json").write_text(json.dumps(jwks))
    jport, mport, aport = _free_port(), _free_port(), _free_port()
    jsrv = ThreadingHTTPServer(("127.0.0.1", jport), partial(SimpleHTTPRequestHandler, directory=str(work)))
    threading.Thread(target=jsrv.serve_forever, daemon=True).start()
    msrv = fake_models.serve(mport, embed_ms=embed_ms, rerank_ms=rerank_ms)
    env = _env(pg_url, es_url, f"http://127.0.0.1:{jport}/jwks.json", f"http://127.0.0.1:{mport}")
    st = Settings(**{k[4:].lower(): v for k, v in env.items() if k[4:].lower() in Settings.model_fields})
    sf = make_session_factory(pg_url)
    with (
        sf() as s
    ):  # same as the chart (postgres.parameters.jit = off): with the default `on`, JIT compiles the ACL
        s.execute(
            text("DO $$ BEGIN EXECUTE format('ALTER DATABASE %I SET jit = off', current_database()); END $$")
        )
        s.commit()  # queries (inflated cost estimates of never-executed subplans) for 100-500 ms each under load
    ingest_bench.prepare(sf, st, es)
    api = subprocess.Popen([sys.executable, "-m", "roracle", "api", "--host", "127.0.0.1", "--port", str(aport)],
                           env={**os.environ, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        _wait(f"http://127.0.0.1:{aport}/healthz")
        for sc in [x for x in SCENARIOS if not only or x in only]:
            rps = profile.get("rps_per", {}).get(
                sc, profile["rps"]
            )  # filtered search does two passes: ~2x the CPU
            r = load.run(f"http://127.0.0.1:{aport}", toks, sc, rps=rps, duration_s=profile["duration"],
                         concurrency=32, docs=profile["docs"], warmup_s=profile["warmup"])  # fmt: skip
            rows.append(_row(sc, budgets.get(sc, {}), r.__dict__, f,
                             f"{rps} rps open loop, {profile['duration']} s after {profile['warmup']} s warm-up; "
                             f"fake models: embed {embed_ms} ms, rerank {rerank_ms} ms"))  # fmt: skip
    finally:
        api.terminate()
        api.wait(20)
        jsrv.shutdown()
        msrv.shutdown()
    if only:  # a focused run: HTTP scenarios only
        out.write_text(json.dumps({"profile": profile, "rows": rows}, indent=1, default=str))
        return 0 if all(r["ok"] for r in rows) else 1
    kinds = ["ingest", "acl_sync", "rebuild_units"]
    lat = claim_latencies(sf, kinds, 1000, 100, workers=1)
    rows.append(
        _row(
            "claim",
            budgets.get("claim", {}),
            Report.of(lat).__dict__,
            f,
            "one claimer, 100 warm-up claims dropped",
        )
    )
    lat4 = claim_latencies(sf, kinds, 1000, 100, workers=4)
    rows.append(_row("claim_x4", {}, Report.of(lat4).__dict__, f, "4 concurrent claimers in one process (informational: GIL + SKIP LOCKED contention)"))  # fmt: skip
    nd = ingest_bench.near_dup_probe(sf, st, es, samples=profile["nd_samples"], lsh_docs=profile["lsh"])
    rows.append(
        _row("near_dup", budgets.get("near_dup", {}), nd.__dict__, f, f"LSH over {profile['lsh']:,} versions")
    )
    ing = ingest_bench.ingest_overhead(sf, st, es, docs=profile["ingest_docs"])
    rows.append(_row("ingest_overhead_s", budgets.get("ingest_overhead_s", {}), ing["seconds"].__dict__, f,
                     f"handle_ingest from `parsed`, instant fake models; stage {ing['last_stage']}"))  # fmt: skip
    rb = rebuild_bench.run(es, sf, st, source=INDEX, chunks=profile["rebuild_chunks"])
    rows.append(_row("rebuild_chunks_per_s", budgets.get("rebuild_chunks_per_s", {}), {"value": rb["chunks_per_s"]}, f,
                     f"{rb['replicas']} replicas x {rb['per_replica_cps']:.0f} chunks/s modelled, {rb['slices']} slices, "
                     f"{rb['chunks']:,} chunks"))  # fmt: skip
    rows.append(_reconcile_row(profile, pg_url, es_url, budgets, f))
    out.write_text(json.dumps({"profile": profile, "rows": rows}, indent=1, default=str))
    print(_markdown(rows))
    return 0 if all(r["ok"] for r in rows) else 1


def _reconcile_row(profile, pg_url, es_url, budgets, f) -> dict:
    import io
    from argparse import Namespace
    from contextlib import redirect_stdout

    from roracle.perf.cli import _reconcile

    os.environ.update({"ROR_READ_ALIAS": READ, "ROR_MUTATE_ALIAS": MUTATE})
    buf = io.StringIO()
    with redirect_stdout(buf):
        _reconcile(Namespace(url=pg_url, es_url=es_url, index=INDEX))
    res = json.loads(buf.getvalue().strip().splitlines()[-1])
    return _row("reconcile_es_writes", budgets.get("reconcile_es_writes", {}), {"value": res["es_writes"] + res["drift_found"]}, f,
                f"full walk of {profile['docs']:,} documents in {res['batches']} batches, {res['seconds']} s; "
                f"{res['es_writes']} ES writes + {res['drift_repairs_enqueued']} documents with drift")  # fmt: skip


def _seed(profile: dict, pg_url: str, es: Elasticsearch) -> None:
    synth_pg.seed(pg_url, docs=profile["docs"], users=profile["users"], jobs_done=profile["jobs_done"],
                  jobs_queued=profile["jobs_queued"], audit_rows=profile["docs"])  # fmt: skip
    synth_pg.seed_lsh(pg_url, n=profile["lsh"])
    from sqlalchemy import create_engine, text

    with create_engine(pg_url).connect() as c:
        label = str(c.execute(text("SELECT id FROM labels WHERE is_default LIMIT 1")).scalar())
    for idx in (INDEX, "perf-reembed-bench"):
        if es.indices.exists(index=idx):
            es.indices.delete(index=idx)
    synth_es.seed(es, INDEX, chunks=profile["chunks"], dims=768, docs=profile["docs"],
                  personas={"users": profile["users"]}, aliases=(READ, MUTATE), label_id=label)  # fmt: skip


def _markdown(rows: list[dict]) -> str:
    out = ["| budget | result | measured vs limit | note |", "|---|---|---|---|"]
    out += [f"| {r['name']} | {'PASS' if r['ok'] else 'FAIL'} | {r['detail']} | {r['note']} |" for r in rows]
    return "\n".join(out)


def load_budgets(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())
