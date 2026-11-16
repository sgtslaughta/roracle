"""Reject inputs Postgres cannot store before they reach a handler: NUL in query/JSON strings (422),
integer path ids beyond bigint (404). A DataError handler backs this up for anything else (query ints)."""

import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BIGINT_MAX = 2**63 - 1
_JSON_NUL = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000|\x00", re.IGNORECASE)
JSON_MAX = (
    3 * 1024 * 1024
)  # largest legit JSON body is a 2 MiB draft; /mcp and non-JSON bodies are never buffered
_RAW_NUL = (b"%00", b"\x00")


def _reply(status: int, detail: str) -> tuple[int, bytes]:
    return status, json.dumps({"detail": detail}).encode()


def _problem(scope: Scope) -> tuple[int, bytes] | None:
    for seg in scope["path"].split("/"):
        if seg.isascii() and seg.isdecimal() and int(seg) > BIGINT_MAX:
            return _reply(404, "not found")
    if any(b in scope.get("query_string", b"") for b in _RAW_NUL):
        return _reply(422, "NUL characters are not allowed")
    return None


class InputGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        bad = _problem(scope)
        scan = bad is None and _is_json(scope) and not scope["path"].startswith("/mcp")
        if scan and _length(scope) > JSON_MAX:
            bad = _reply(413, "request body too large")
        if scan and bad is None:
            chunks, more, total = [], True, 0
            while more:
                m = await receive()
                if m["type"] != "http.request":  # disconnect
                    return await self.app(scope, receive, send)
                chunks.append(m.get("body", b""))
                total += len(chunks[-1])
                if total > JSON_MAX:
                    bad = _reply(413, "request body too large")
                    break
                more = m.get("more_body", False)
        if scan and bad is None:
            body = b"".join(chunks)
            if _JSON_NUL.search(body):
                bad = _reply(422, "NUL characters are not allowed")
            sent = False

            async def replay() -> Message:
                nonlocal sent
                if sent:
                    return await receive()
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            receive = replay
        if bad is None:
            return await self.app(scope, receive, send)
        status, payload = bad
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})


def _length(scope: Scope) -> int:
    for k, v in scope["headers"]:
        if k == b"content-length" and v.isdigit():
            return int(v)
    return 0


def _is_json(scope: Scope) -> bool:
    for k, v in scope["headers"]:
        if k == b"content-type":
            return v.lower().startswith(b"application/json")
    return False


def install(app: FastAPI) -> None:
    app.add_middleware(InputGuard)

    @app.exception_handler(DataError)
    async def _data_error(_: Request, exc: DataError) -> JSONResponse:
        return JSONResponse({"detail": "value out of range or not storable"}, status_code=422)
