"""`roracle eval run|compare|ci`: exit 0 ok, 1 ACL violation or run error, 2 unusable input (R5 / bad input)."""

import json
import sys
from typing import Annotated

from pydantic import TypeAdapter, ValidationError

from roracle import clients, runtime
from roracle.config import Settings, get_settings
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
    r.add_argument("--judge", action="store_true", help="answer and judge a sample of pairs (chat path)")
    c = ev.add_parser("compare", help="A/B two configurations on one set and write a gate report")
    c.add_argument("--set", dest="set_name", required=True)
    c.add_argument("--path", choices=["chat", "search"], default="chat")
    c.add_argument("--baseline", default="", metavar="K=V,...")
    c.add_argument("--candidate", default="", metavar="K=V,...")
    c.add_argument("--candidate-index", default="", help="read this ES index for the candidate run")
    c.add_argument("--scope", choices=["all", "fact", "multihop", "global"], default="all")
    c.add_argument("--latency-budget-ms", type=int, default=None)
    c.add_argument("--judge", action="store_true")
    c.add_argument("--out", required=True, metavar="docs/eval/FILE.md")
    b = ev.add_parser("import-beir", help="seed a BEIR dataset (SciFact) from a local directory, offline")
    b.add_argument("dir")
    b.add_argument("--set", dest="set_name", required=True)
    b.add_argument(
        "--owner", required=True, help="non-admin user who owns the documents and is a gold principal"
    )
    b.add_argument("--dataset", default="scifact", help="dataset tag keying idempotent re-imports")
    b.add_argument(
        "--principals", default="", metavar="A,B,C", help="more non-admin gold principals (shares)"
    )
    b.add_argument("--share-group", action="append", default=[], metavar="GROUP")
    b.add_argument("--visibility", choices=["private", "shared", "everyone"], default="private")
    ci = ev.add_parser("ci", help="seed the synthetic fixture and fail on ACL violations or low metrics")
    ci.add_argument("--fixture", required=True)
    ci.add_argument("--floors", required=True)
    ci.add_argument("--out", required=True)


# Eval-only overrides (never in runtime.SettingsIn: an admin-settable rerank_url would be SSRF; the API key stays out
# so it can never land in eval_runs.config).
EVAL_KEYS = (
    "rerank_url", "rerank_api", "rerank_model", "rerank_timeout_s", "rerank_max_chars", "rerank_max_batch",
    "chat_candidates", "context_notes", "context_model",
)  # fmt: skip


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
            if k in EVAL_KEYS:
                f = Settings.model_fields[k]
                v = TypeAdapter(Annotated[f.annotation, f]).validate_python(v)
            else:
                runtime.SettingsIn.model_validate({k: v})
        except ValidationError as e:
            raise ValueError(f"bad override {k}: {e.errors()[0]['msg']}") from e
        out[k] = v
    return out


def validate_index(es, settings, name: str) -> None:
    """`--index`/`--candidate-index` must be one existing index built with the effective embedding model."""
    if not es.indices.exists(index=name):
        raise ValueError(f"index {name!r} does not exist")
    got = es.indices.get_mapping(index=name)
    if len(got) != 1:
        raise ValueError(f"{name!r} must be a single index, not an alias over {len(got)}")
    dims = next(iter(got.values()))["mappings"].get("properties", {}).get("embedding", {}).get("dims")
    if dims != settings.embed_dims:
        raise ValueError(
            f"index {name!r} has {dims} dims, the embedding model {settings.embed_model} has {settings.embed_dims}"
        )
    hits = es.search(index=name, size=1, source=["embedding_model"], query={"match_all": {}})["hits"]["hits"]
    if hits and hits[0]["_source"].get("embedding_model") != settings.embed_model:
        raise ValueError(
            f"index {name!r} was built with {hits[0]['_source'].get('embedding_model')}, not {settings.embed_model}"
        )


def table(agg: dict) -> str:
    o = agg["overall"]
    head = (
        f"pairs={o['pairs']} empty={agg['empty_pairs']} acl_violations={agg['acl_violations']} "
        f"es_acl_violations={agg['es_acl_violations']} p50={agg['latency_p50_ms']}ms p95={agg['latency_p95_ms']}ms"
    )
    lines = [head]
    lines += [f"{k:<14}{v:.3f}" for k, v in o.items() if k != "pairs"]
    return "\n".join(lines)


def import_beir_cmd(args) -> int:
    from pathlib import Path

    from roracle.eval import beir
    from roracle.storage import blob_store_from

    settings = get_settings()
    with make_session_factory(settings.database_url)() as session:
        try:
            out = beir.import_beir(
                session, blob_store_from(settings), runtime.effective(session, settings), Path(args.dir),
                set_name=args.set_name, owner=args.owner, dataset=args.dataset, visibility=args.visibility,
                principals=[x.strip() for x in args.principals.split(",") if x.strip()], groups=args.share_group,
            )  # fmt: skip
        except (ValueError, OSError) as e:
            session.rollback()
            print(f"error: {e}", file=sys.stderr)
            return 2
        except Exception as e:  # noqa: BLE001
            session.rollback()
            print(
                f"import failed: {type(e).__name__}: {e}; re-run the same command to resume", file=sys.stderr
            )
            return 1
    print(
        f"set {args.set_name!r}: {out['documents']} new documents, {out['reused']} reused, "
        f"{out['requeued']} re-queued, {out['queries']} queries; near-dups kept {out['near_dup_kept']}, "
        f"left parked {out['near_dup_parked']}. The worker ingests asynchronously: re-run after it drains to finalize"
    )
    return 0


def run(args) -> int:
    """0 ok; 1 ACL violation or run error; 2 bad input / unusable set (R5)."""
    if args.eval_cmd == "ci":
        from roracle.eval import ci

        return ci.run(args)
    if args.eval_cmd == "import-beir":
        return import_beir_cmd(args)
    if args.eval_cmd == "compare":
        from roracle.eval import compare

        return compare.run(args)
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
            if args.judge and args.path != "chat":
                raise ValueError("--judge needs --path chat")
            db_set = store.set_by_name(session, args.set_name)
            if db_set is None:
                raise ValueError(f"no eval set named {args.set_name!r}")
            gs = gold.load_db(session, db_set.id)
            gold.resolve_principals(session, gs)  # R5 and unknown principals are input errors
        except (ValueError, ValidationError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        settings = runtime.effective(session, settings)  # incl. the active (cutover) embedding model/index
        es = clients.es(settings)
        try:
            if args.index:
                validate_index(es, settings, args.index)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        try:
            run_id = runner.run_set(
                session, es, clients.llm(settings), settings, gs, path=args.path, trigger="cli",
                label=args.label, overrides=overrides, judge=args.judge,
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
