"""`roracle rebuild --what context[,raptor] [--into NAME] | --drop NAME` (exit 0 ok, 2 refused)."""

import sys

from roracle import clients, reembed, runtime, search_index
from roracle.config import get_settings
from roracle.db import make_session_factory
from roracle.ingest import rebuild


def add_parser(sub) -> None:
    r = sub.add_parser(
        "rebuild", help="rebuild index-time derived fields (contextual notes, RAPTOR summaries) per version"
    )
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--what", metavar="context[,raptor]")
    g.add_argument(
        "--drop",
        metavar="NAME",
        help="delete a shadow index (never the active one, a re-embed generation, or while jobs run)",
    )
    r.add_argument(
        "--into",
        metavar="NAME",
        help="shadow index name chunks-<slug> (not ending in -<number>; default: active index)",
    )


def run(args) -> int:
    settings = get_settings()
    es = clients.es(settings)
    with make_session_factory(settings.database_url, settings.db_connect_timeout_s)() as session:
        eff = runtime.effective(session, settings)
        try:
            if args.drop:
                rebuild.drop_shadow(
                    es, args.drop, search_index.active_index(eff), settings.read_alias, session
                )
                print(f"dropped {args.drop}")
                return 0
            what = {w.strip() for w in args.what.split(",") if w.strip()}
            if not what or what - {"context", "raptor"}:
                raise ValueError("--what takes context and/or raptor")
            n = rebuild.start(session, es, eff, what, args.into)
            session.commit()
        except (ValueError, reembed.AlreadyRunning) as e:
            session.rollback()
            print(f"error: {e or 'a re-embed is running'}", file=sys.stderr)
            return 2
    print(f"enqueued {n} rebuild_units job(s) into {args.into or search_index.active_index(eff)}")
    return 0
