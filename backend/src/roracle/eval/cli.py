"""`roracle eval run`: exit 0 ok, 1 ACL violation, 2 unusable set (R5 / bad input)."""

import json
import sys

from pydantic import ValidationError

from roracle import clients, runtime
from roracle.config import get_settings
from roracle.db import make_session_factory
from roracle.eval import gold, runner, store
from roracle.models_eval import EvalRun


def add_parser(sub) -> None:
    ev = sub.add_parser("eval", help="retrieval-quality evaluation").add_subparsers(
        dest="eval_cmd", required=True
    )
    r = ev.add_parser("run", help="score a gold set per principal")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", dest="set_name")
    g.add_argument("--fixture", metavar="PATH", help="not implemented yet (use `eval ci`, task 2)")
    r.add_argument("--path", choices=["chat", "search"], default="chat")
    r.add_argument("--label", default="")
    r.add_argument("--set-override", action="append", default=[], metavar="KEY=VAL")
    r.add_argument("--index", default="", help="read this ES index instead of the active one")
    r.add_argument("--json", dest="json_out", metavar="OUT")


def parse_overrides(items: list[str], index: str = "") -> dict:
    out: dict = {"embed_index": index} if index else {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep:
            raise ValueError(f"expected KEY=VAL: {it}")
        try:
            v = json.loads(v)
        except ValueError:
            pass
        try:
            runtime.SettingsIn.model_validate({k: v})
        except ValidationError as e:
            raise ValueError(f"bad override {k}: {e.errors()[0]['msg']}") from e
        out[k] = v
    return out


def table(agg: dict) -> str:
    o = agg["overall"]
    head = (
        f"pairs={o['pairs']} empty={agg['empty_pairs']} acl_violations={agg['acl_violations']} "
        f"es_acl_violations={agg['es_acl_violations']} p50={agg['latency_p50_ms']}ms p95={agg['latency_p95_ms']}ms"
    )
    lines = [head]
    lines += [f"{k:<14}{v:.3f}" for k, v in o.items() if k != "pairs"]
    return "\n".join(lines)


def run(args) -> int:
    """0 ok; 1 ACL violation or run error; 2 bad input / unusable set (R5)."""
    if args.fixture:
        print(
            "error: --fixture runs are not implemented yet (`eval ci` seeds and runs fixtures)",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    sf = make_session_factory(settings.database_url)
    with sf() as session:
        try:
            overrides = parse_overrides(args.set_override, args.index)
            db_set = store.set_by_name(session, args.set_name)
            if db_set is None:
                raise ValueError(f"no eval set named {args.set_name!r}")
            gs = gold.load_db(session, db_set.id)
            gold.resolve_principals(session, gs)  # R5 and unknown principals are input errors
        except (ValueError, ValidationError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        settings = runtime.effective(session, settings)  # incl. the active (cutover) embedding model/index
        try:
            run_id = runner.run_set(
                session, clients.es(settings), clients.llm(settings), settings, gs,
                path=args.path, trigger="cli", label=args.label, overrides=overrides,
            )  # fmt: skip
        except Exception as e:  # noqa: BLE001  (the failed run row is already stored)
            print(f"run failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        r = session.get(EvalRun, run_id)
        print(table(r.metrics))
        if r.metrics["es_acl_violations"]:
            print("warning: Elasticsearch returned documents the principal may not see", file=sys.stderr)
        if args.json_out:
            with open(args.json_out, "w") as f:
                json.dump({"run_id": str(run_id), "status": r.status, "metrics": r.metrics}, f, indent=2)
        return 1 if r.metrics["acl_violations"] else 0
