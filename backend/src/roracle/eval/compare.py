"""`roracle eval compare`: two runs of one set (baseline vs candidate settings), gate verdict, report file."""

import re
import sys
from pathlib import Path

from pydantic import ValidationError

from roracle import clients, runtime
from roracle.config import get_settings
from roracle.db import make_session_factory
from roracle.eval import cli, gate, gold, report, runner, store

_SPLIT = re.compile(r",(?=\s*[A-Za-z_]\w*=)")  # a comma inside a JSON value is not a separator


def split_kv(s: str) -> list[str]:
    return [x.strip() for x in _SPLIT.split(s) if x.strip()]


def run(args) -> int:
    """Always writes the report when both runs finish (a FAIL verdict is a result, not an error);
    exit 1 on run errors or any ACL violation, 2 on bad input."""
    settings = get_settings()
    sf = make_session_factory(settings.database_url)
    with sf() as session:
        try:
            base_ov = cli.parse_overrides(split_kv(args.baseline))
            cand_ov = cli.parse_overrides(split_kv(args.candidate), args.candidate_index)
            if args.judge and args.path != "chat":
                raise ValueError("--judge needs --path chat")
            db_set = store.set_by_name(session, args.set_name)
            if db_set is None:
                raise ValueError(f"no eval set named {args.set_name!r}")
            gs = gold.load_db(session, db_set.id)
            gold.resolve_principals(session, gs)
            settings = runtime.effective(session, settings)
            es = clients.es(settings)
            if args.candidate_index:
                cli.validate_index(es, settings, args.candidate_index)
        except (ValueError, ValidationError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        llm = clients.llm(settings)
        try:
            ids = [
                runner.run_set(
                    session,
                    es,
                    llm,
                    settings,
                    gs,
                    path=args.path,
                    trigger="compare",
                    label=name,
                    overrides=ov,
                    judge=args.judge,
                    sf=sf,
                    parallel_sf=sf,
                )
                for name, ov in (("baseline", base_ov), ("candidate", cand_ov))
            ]
            base, cand = (store.load_run(session, i) for i in ids)
        except Exception as e:  # noqa: BLE001  (failed run rows are already stored)
            print(f"compare failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    v = gate.verdict(base, cand, scope=args.scope, latency_budget_ms=args.latency_budget_ms)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.markdown(base, cand, v))
    print(f"verdict: {v.status} -> {out}")
    for r in v.reasons:
        print(f"  {r}")
    leaks = base.metrics["acl_violations"] + cand.metrics["acl_violations"]
    return 1 if leaks else 0
