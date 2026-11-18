import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_INI = Path(__file__).parents[2] / "alembic.ini"


def maybe_wrap_otel(argv: list[str]) -> None:
    """Opt-in OTel: re-exec api/worker under opentelemetry-instrument when an OTLP endpoint is set."""
    if (
        argv[:1] not in (["api"], ["worker"])
        or not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("ROR_OTEL_WRAPPED")
    ):
        return
    exe = shutil.which("opentelemetry-instrument")
    if exe:
        os.environ["ROR_OTEL_WRAPPED"] = "1"
        os.execvp(
            "opentelemetry-instrument", ["opentelemetry-instrument", sys.executable, "-m", "roracle", *argv]
        )


def main(argv: list[str] | None = None) -> None:
    maybe_wrap_otel(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="roracle")
    sub = p.add_subparsers(dest="cmd", required=True)
    api = sub.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run the job worker")
    mig = sub.add_parser("migrate", help="alembic upgrade head")
    mig.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="wait for the database first")
    mig.add_argument("--alembic-ini", default=str(DEFAULT_INI))
    from roracle.eval.cli import add_parser

    add_parser(sub)
    from roracle.ingest.rebuild_cli import add_parser as add_rebuild

    add_rebuild(sub)
    from roracle.ingest.reparse import add_parser as add_reparse

    add_reparse(sub)
    from roracle.autotag import add_parser as add_autotag

    add_autotag(sub)
    from roracle.perf.cli import add_parser as add_perf

    add_perf(sub)
    args = p.parse_args(argv)

    if args.cmd == "api":
        import uvicorn

        uvicorn.run("roracle.api.app:create_app", factory=True, host=args.host, port=args.port)
    elif args.cmd == "worker":
        from roracle.worker import main as worker_main

        worker_main()
    elif args.cmd == "rebuild":
        from roracle.ingest.rebuild_cli import run as rebuild_run

        sys.exit(rebuild_run(args))
    elif args.cmd == "reparse-refs":
        from roracle.ingest.reparse import run as reparse_run

        sys.exit(reparse_run(args))
    elif args.cmd == "auto-tags":
        from roracle.autotag import run as autotag_run

        sys.exit(autotag_run(args))
    elif args.cmd == "perf":
        from roracle.perf.cli import run as perf_run

        sys.exit(perf_run(args))
    elif args.cmd == "eval":
        from roracle.eval.cli import run

        sys.exit(run(args))
    else:
        from alembic import command
        from alembic.config import Config

        from roracle import db
        from roracle.config import get_settings

        if args.wait:
            db.wait_for_db(get_settings().database_url, args.wait)

        command.upgrade(Config(args.alembic_ini), "head")


if __name__ == "__main__":
    main()
