import logging
from functools import partial

from roracle import clients, runtime, search_index, worker_health
from roracle.acl_sync import handle_acl_sync
from roracle.citations import handle_citations
from roracle.config import get_settings
from roracle.connectors.run import ensure_tick, handle_connector_run, handle_connector_tick
from roracle.db import make_session_factory
from roracle.eval.jobs import handle_eval_run
from roracle.ingest import handle_ingest
from roracle.maintenance import ensure_maintenance, handle_maintenance, handle_reconcile_tags
from roracle.notes.evidence import handle_evidence_scan
from roracle.queue import Handler, run_forever
from roracle.reembed import handle_reembed
from roracle.storage import blob_store_from

log = logging.getLogger(__name__)
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
        ),
        "connector_run": partial(
            handle_connector_run,
            settings=settings,
            store=blob_store_from(settings),
            session_factory=session_factory,
        ),
        "connector_tick": partial(handle_connector_tick, settings=settings),
        "citations": handle_citations,
        "evidence_scan": partial(handle_evidence_scan, settings=settings, store=blob_store_from(settings)),
        "acl_sync": partial(handle_acl_sync, es=clients.es(settings), session_factory=session_factory),
        "maintenance": partial(
            handle_maintenance, settings=settings, store=blob_store_from(settings), es=clients.es(settings)
        ),
        "reconcile_tags": handle_reconcile_tags,
        "eval_run": partial(
            handle_eval_run,
            settings=settings,
            es=clients.es(settings),
            llm=clients.llm(settings),
            session_factory=session_factory,
        ),
        "reembed": partial(
            handle_reembed,
            settings=settings,
            es=clients.es(settings),
            llm=clients.llm(settings),
            session_factory=session_factory,
        ),
    }


def ensure_active_index(settings, sf) -> None:
    """Worker start: create `chunks-all`/legacy aliases for the active index (upgrade path); never fatal."""
    try:
        with sf() as s:
            eff = runtime.effective(s, settings)
        search_index.ensure_index(
            clients.es(settings), eff.embed_model, eff.embed_dims, settings.read_alias,
            search_index.active_index(eff), session_factory=sf, settings=settings,
        )  # fmt: skip
    except Exception:
        log.exception("could not ensure the active search index at startup")


def main() -> None:
    settings = get_settings()
    if not settings.clamav_host:
        log.warning("clamav_host is empty: malware scanning disabled")
    sf = make_session_factory(settings.database_url, settings.db_connect_timeout_s)
    HANDLERS.update(build_handlers(settings, sf))
    worker_health.serve(
        settings.worker_health_port
    )  # first: startup work below can block (legacy adopt, slow ES)
    ensure_active_index(settings, sf)
    ensure_tick(sf)
    ensure_maintenance(sf)
    run_forever(sf, HANDLERS)
