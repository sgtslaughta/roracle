"""Stream a multipart/form-data request body straight to object storage (R2): no local spool.

Non-file fields are collected in memory (capped); the single `file` part goes through `TmpUpload`.
Raises TooLarge / EmptyFile (storage) or HTTPException 422 (malformed). The caller promotes or discards."""

import codecs
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

import anyio
from fastapi import HTTPException, Request
from python_multipart import MultipartParser
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool

from roracle.storage import BlobStore, StagedBlob, TmpUpload

FIELD_MAX = 64 * 1024
FIELDS_TOTAL_MAX = 1 << 20
MAX_PARTS = 100
HEADER_MAX = 8 * 1024  # bytes of header names+values per part
HEADERS_PER_PART = 16
HEADER_DETAIL = "form part header too large"
BODY_ALLOWANCE = 2 * FIELDS_TOTAL_MAX  # non-file bytes (fields, boundaries, headers) on top of the file limit


def _bad(detail: str) -> HTTPException:
    return HTTPException(422, detail)


@dataclass
class Received:
    fields: dict[str, str]
    staged: StagedBlob | None


@dataclass
class _Part:
    name: str = ""
    filename: str | None = None
    content_type: str = ""
    data: bytearray = field(default_factory=bytearray)
    headers: list[tuple[bytes, bytes]] = field(default_factory=list)


class _Sink:
    def __init__(self, store: BlobStore, max_bytes: int, file_field: str, field_max: int, charset: str):
        self.store, self.max_bytes, self.file_field = store, max_bytes, file_field
        self.field_max, self.charset = field_max, charset
        self.fields: dict[str, str] = {}
        self.upload: TmpUpload | None = None
        self.filename = ""
        self.content_type = ""
        self._part = _Part()
        self._hname = self._hvalue = b""
        self._parts = 0
        self._hbytes = 0
        self._field_bytes = 0

    def _dec(self, b: bytes) -> str:
        return b.decode(self.charset, errors="replace")

    def on_part_begin(self) -> None:
        self._parts += 1
        if self._parts > MAX_PARTS:
            raise _bad("too many form parts")
        self._part = _Part()
        self._hbytes = 0

    def _hgrow(self, n: int) -> None:
        self._hbytes += n
        if self._hbytes > HEADER_MAX:
            raise HTTPException(400, HEADER_DETAIL)

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._hgrow(end - start)
        self._hname += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._hgrow(end - start)
        self._hvalue += data[start:end]

    def on_header_end(self) -> None:
        if len(self._part.headers) >= HEADERS_PER_PART:
            raise HTTPException(400, HEADER_DETAIL)
        self._part.headers.append((self._hname.lower(), self._hvalue))
        self._hname = self._hvalue = b""

    def on_headers_finished(self) -> None:
        h = dict(self._part.headers)
        _, opts = parse_options_header(h.get(b"content-disposition", b""))
        if b"name" not in opts:
            raise _bad("form part without a name")
        p = self._part
        p.name = self._dec(opts[b"name"])
        if b"filename" not in opts:
            return
        if p.name != self.file_field or self.upload is not None:
            raise _bad("unexpected file part")
        p.filename = self._dec(opts[b"filename"])
        ctype = self._dec(parse_options_header(h.get(b"content-type", b""))[0]) or "application/octet-stream"
        self.filename, self.content_type = p.filename, ctype
        self.upload = TmpUpload(self.store, ctype, self.max_bytes)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self._part.filename is not None:
            assert self.upload is not None
            self.upload.feed(chunk)
            return
        self._field_bytes += len(chunk)
        if len(self._part.data) + len(chunk) > self.field_max or self._field_bytes > FIELDS_TOTAL_MAX:
            raise _bad(f"form field '{self._part.name}' too large")
        self._part.data += chunk

    def on_part_end(self) -> None:
        if self._part.filename is None:
            self.fields[self._part.name] = self._dec(bytes(self._part.data))


async def receive(
    request: Request,
    store: BlobStore,
    max_bytes: int,
    *,
    file_field: str = "file",
    field_max: int = FIELD_MAX,
) -> Received:
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    if ctype == b"application/x-www-form-urlencoded":  # file-less submissions (e.g. evidence with a uri)
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > FIELDS_TOTAL_MAX:
                raise _bad("form too large")
        return Received(dict(parse_qsl(body.decode("utf-8", errors="replace"), keep_blank_values=True)), None)
    if ctype != b"multipart/form-data" or b"boundary" not in params:
        raise _bad("expected multipart/form-data")
    try:
        charset = codecs.lookup(params.get(b"charset", b"utf-8").decode("latin-1")).name
    except LookupError:
        charset = "utf-8"
    sink = _Sink(store, max_bytes, file_field, field_max, charset)
    parser = MultipartParser(
        params[b"boundary"],
        {
            "on_part_begin": sink.on_part_begin,
            "on_part_data": sink.on_part_data,
            "on_part_end": sink.on_part_end,
            "on_header_field": sink.on_header_field,
            "on_header_value": sink.on_header_value,
            "on_header_end": sink.on_header_end,
            "on_headers_finished": sink.on_headers_finished,
        },
        max_header_size=HEADER_MAX,
        max_header_count=HEADERS_PER_PART,
    )
    try:
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes + BODY_ALLOWANCE:
                raise HTTPException(413, "request body too large")
            parser.write(chunk)
            while sink.upload is not None and sink.upload.ready:
                await run_in_threadpool(sink.upload.flush)
        parser.finalize()
        staged = None
        if sink.upload is not None:
            await run_in_threadpool(sink.upload.finish)
            staged = StagedBlob(
                sink.upload, sink.upload.sha256, sink.upload.size, sink.filename, sink.content_type
            )
        return Received(sink.fields, staged)
    except BaseException as e:
        if sink.upload is not None:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(sink.upload.abort)
        if isinstance(e, FormParserError):
            if "Maximum header" in str(e):  # the parser's own header limits, same answer as ours
                raise HTTPException(400, HEADER_DETAIL) from None
            raise _bad("invalid multipart data") from None
        raise


def multipart_body(properties: dict, required: list[str]) -> dict:
    """`openapi_extra` for routes that parse the body themselves, so the schema still shows the form fields."""
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {"type": "object", "properties": properties, "required": required}
                }
            },
        }
    }
