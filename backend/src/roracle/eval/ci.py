"""`roracle eval ci`: seed the synthetic fixture into a fresh ES index and a rolled-back Postgres
transaction, run it on both paths with offline models, and fail on any ACL violation or a metric under its
floor. Exit 0 ok, 1 violation / below floor / error, 2 bad input."""

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlalchemy.orm import Session

from roracle import clients
from roracle.config import Settings, get_settings
from roracle.db import make_session_factory
from roracle.eval import fakes, fixture, gold, runner
from roracle.models_eval import EvalRun

INDEX = "chunks-evalci-1"
MODEL = "eval-hash-bow"
PATHS = ("chat", "search")
ES_WAIT_S = 120


@contextmanager
def sandbox(settings: Settings):
    """A session whose commits are savepoints of one outer transaction, rolled back at the end."""
    bind = make_session_factory(settings.database_url).kw["bind"]
    try:
        with bind.connect() as conn:
            outer = conn.begin()
            session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
            try:
                yield session
            finally:
                session.close()
                outer.rollback()
    finally:
        bind.dispose()


def ci_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"embed_model": MODEL, "embed_dims": fakes.DIMS, "embed_index": INDEX, "rerank_url": ""}
    )


def load_floors(path: str) -> dict[str, dict[str, float]]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or set(raw) - set(PATHS):
        raise ValueError(f"floors must be an object keyed by {list(PATHS)}")
    return {p: {k: float(v) for k, v in raw.get(p, {}).items()} for p in PATHS}


def wait_for_es(es, seconds: float = ES_WAIT_S) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            es.info()
            return
        except Exception as e:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"elasticsearch not reachable: {type(e).__name__}") from e
            time.sleep(2)


def execute(session: Session, es, llm, settings: Settings, gs: gold.GoldSet, floors: dict) -> dict:
    """Run both paths (the chat path with the judge) and compare with `floors`."""
    report: dict = {"paths": {}, "violations": [], "below_floor": []}
    for path in PATHS:
        rid = runner.run_set(
            session, es, llm, settings, gs, path=path, trigger="ci", label="ci", judge=path == "chat"
        )
        m = session.get(EvalRun, rid).metrics
        report["paths"][path] = m
        for key in ("acl_violations", "es_acl_violations"):
            if m[key]:
                report["violations"].append(f"{path}: {key}={m[key]}")
        for key, floor in floors.get(path, {}).items():
            got = m["overall"].get(key, (m.get("judge") or {}).get(key))
            if got is None or got < floor:
                report["below_floor"].append({"path": path, "metric": key, "floor": floor, "measured": got})
    report["status"] = "fail" if report["violations"] or report["below_floor"] else "pass"
    return report


def run(args) -> int:
    settings = get_settings()
    try:
        fx = gold.load_file(args.fixture)
        floors = load_floors(args.floors)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ci = ci_settings(settings)
    es, llm = clients.es(settings), fakes.CiLLM()
    try:
        wait_for_es(es)
        with sandbox(ci) as session:
            fixture.create_index(es, INDEX, fakes.DIMS)
            gs = fixture.seed(session, es, llm, ci, fx)
            report = execute(session, es, llm, ci, gs, floors)
    except Exception as e:  # noqa: BLE001
        print(f"ci failed: {type(e).__name__}: {e}", file=sys.stderr)
        Path(args.out).write_text(
            json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}, indent=2)
        )
        return 1
    finally:
        try:
            es.indices.delete(index=INDEX, ignore_unavailable=True)
        except Exception:  # noqa: BLE001, S110
            pass
    Path(args.out).write_text(json.dumps(report, indent=2))
    for p, m in report["paths"].items():
        o = m["overall"]
        print(f"{p}: pairs={o['pairs']} ndcg@10={o['p_ndcg@10']:.3f} recall@10={o['p_recall@10']:.3f}"
              f" acl={m['acl_violations']} es_acl={m['es_acl_violations']}")  # fmt: skip
    for v in report["violations"]:
        print(f"VIOLATION {v}", file=sys.stderr)
    for b in report["below_floor"]:
        print(f"BELOW FLOOR {b['path']} {b['metric']}: {b['measured']} < {b['floor']}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1
