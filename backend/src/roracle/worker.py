from functools import partial

from roracle import clients
from roracle.config import get_settings
from roracle.db import make_session_factory
from roracle.ingest import handle_ingest
from roracle.queue import Handler, run_forever
from roracle.storage import blob_store_from

HANDLERS: dict[str, Handler] = {}  # filled by build_handlers() at worker startup


def build_handlers(settings, session_factory) -> dict[str, Handler]:
    return {
        "ingest": partial(
            handle_ingest,
            settings=settings,
            store=blob_store_from(settings),
            es=clients.es(settings),
            llm=clients.llm(settings),
            session_factory=session_factory,
        )
    }


def main() -> None:
    settings = get_settings()
    sf = make_session_factory(settings.database_url)
    HANDLERS.update(build_handlers(settings, sf))
    run_forever(sf, HANDLERS)
