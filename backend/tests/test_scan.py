import io
import socketserver
import struct
import threading

import pytest

from roracle.ingest.scan import scan
from roracle.queue import NonRetryable


def _serve(reply: bytes | None, received: dict):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            f = self.request.makefile("rb")
            received["cmd"] = f.read(10)
            total = 0
            while True:
                hdr = f.read(4)
                if len(hdr) < 4:
                    return
                (n,) = struct.unpack(">I", hdr)
                if n == 0:
                    break
                total += len(f.read(n))
                received["max_chunk"] = max(received.get("max_chunk", 0), n)
            received["total"] = total
            if reply is not None:
                self.request.sendall(reply)

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def fake():
    servers = []

    def make(reply, received=None):
        srv = _serve(reply, received if received is not None else {})
        servers.append(srv)
        return srv.server_address[1]

    yield make
    for s in servers:
        s.shutdown()
        s.server_close()


def test_clean(fake):
    port = fake(b"stream: OK\0")
    assert scan(io.BytesIO(b"hello"), "127.0.0.1", port) is None


def test_found(fake):
    port = fake(b"stream: Eicar-Test-Signature FOUND\0")
    assert scan(io.BytesIO(b"x"), "127.0.0.1", port) == "Eicar-Test-Signature"


def test_early_close(fake):
    port = fake(None)
    with pytest.raises(ConnectionError):
        scan(io.BytesIO(b"x"), "127.0.0.1", port)


def test_error_reply(fake):
    port = fake(b"some other ERROR\0")
    with pytest.raises(ConnectionError, match="other ERROR"):
        scan(io.BytesIO(b"x"), "127.0.0.1", port)


def test_size_limit_non_retryable(fake):
    port = fake(b"INSTREAM size limit exceeded. ERROR\0")
    with pytest.raises(NonRetryable, match="size limit"):
        scan(io.BytesIO(b"x"), "127.0.0.1", port)


@pytest.mark.parametrize(
    "reply", [b"stream: OK", b"stream: Sig-OK", b"", b"weird\0", b"nope: OK\0", b"stream: Sig-OK\0"]
)
def test_truncated_or_unexpected_fails_closed(fake, reply):
    port = fake(reply)
    with pytest.raises(ConnectionError):
        scan(io.BytesIO(b"x"), "127.0.0.1", port)


def test_timeout(fake):
    ev = threading.Event()

    class H(socketserver.BaseRequestHandler):
        def handle(self):
            ev.wait(3)

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ConnectionError):
            scan(io.BytesIO(b"x"), "127.0.0.1", srv.server_address[1], timeout=0.2)
    finally:
        ev.set()
        srv.shutdown()
        srv.server_close()


def test_local_read_error_propagates(fake):
    class Bad(io.BytesIO):
        def read(self, n=-1):
            raise OSError("disk")

    port = fake(b"stream: OK\0")
    with pytest.raises(OSError, match="disk") as ei:
        scan(Bad(), "127.0.0.1", port)
    assert not isinstance(ei.value, ConnectionError)


def test_refused():
    with pytest.raises(ConnectionError):
        scan(io.BytesIO(b"x"), "127.0.0.1", 1, timeout=1)


def test_large_payload_chunked(fake):
    rec: dict = {}
    port = fake(b"stream: OK\0", rec)
    data = b"a" * (150 * 1024)
    assert scan(io.BytesIO(data), "127.0.0.1", port) is None
    assert rec["cmd"] == b"zINSTREAM\0"
    assert rec["total"] == len(data)
    assert rec["max_chunk"] <= 64 * 1024
