import argparse
from pathlib import Path

DEFAULT_INI = Path(__file__).parents[2] / "alembic.ini"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="roracle")
    sub = p.add_subparsers(dest="cmd", required=True)
    api = sub.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run the job worker")
    mig = sub.add_parser("migrate", help="alembic upgrade head")
    mig.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="wait for the database first")
    mig.add_argument("--alembic-ini", default=str(DEFAULT_INI))
    args = p.parse_args(argv)

    if args.cmd == "api":
        import uvicorn

        uvicorn.run("roracle.api.app:create_app", factory=True, host=args.host, port=args.port)
    elif args.cmd == "worker":
        from roracle.worker import main as worker_main

        worker_main()
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
