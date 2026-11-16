from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from roracle import __version__, clients, metrics
from roracle.storage import blob_store_from

router = APIRouter()
CHECK_TIMEOUT_S = 2.0
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="readyz")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def _default_checks(app) -> dict:
    s = app.state.settings
    es = clients.es(s)  # clients are built once and cached with the checks on app.state
    store = blob_store_from(s)

    def db() -> bool:
        with app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
        return True

    return {
        "db": db,
        "es": lambda: es.options(request_timeout=CHECK_TIMEOUT_S).ping(),
        "s3": lambda: bool(store.client.head_bucket(Bucket=store.bucket)),
    }


def _run(fn) -> str:
    try:
        return "ok" if _pool.submit(fn).result(timeout=CHECK_TIMEOUT_S) else "fail"
    except (FutureTimeout, Exception):  # noqa: BLE001 never surface error text
        return "fail"


@router.get("/readyz")
def readyz(request: Request) -> JSONResponse:
    app = request.app
    if not hasattr(app.state, "ready_checks"):
        app.state.ready_checks = _default_checks(app)
    checks = {name: _run(fn) for name, fn in app.state.ready_checks.items()}
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"checks": checks}, status_code=200 if ok else 503)


@router.get("/metrics", include_in_schema=False)
def prometheus(request: Request) -> Response:
    return Response(metrics.render(request.app.state.session_factory), media_type="text/plain; version=0.0.4")
