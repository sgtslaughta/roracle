"""Worker liveness + metrics on a stdlib HTTP thread (R3). 503 when the loop heartbeat is stale."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import generate_latest

STALE_S = 300.0
_beat = time.monotonic()
working = False  # a job is running: long handlers must not trip liveness


def beat() -> None:
    global _beat
    _beat = time.monotonic()


def age() -> float:
    return time.monotonic() - _beat


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            ok = working or age() <= STALE_S
            body, code, ctype = (
                (b'{"status":"ok"}' if ok else b'{"status":"stale"}'),
                (200 if ok else 503),
                "application/json",
            )
        elif self.path == "/metrics":
            body, code, ctype = generate_latest(), 200, "text/plain; version=0.0.4"
        else:
            body, code, ctype = b"", 404, "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(port: int) -> ThreadingHTTPServer | None:
    """Start the daemon server thread; port 0 = off."""
    if not port:
        return None
    srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
