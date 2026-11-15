"""Minimal clamd INSTREAM client (stdlib only)."""

import socket
import struct
from typing import BinaryIO

CHUNK = 64 * 1024


def scan(stream: BinaryIO, host: str, port: int = 3310, timeout: float = 30) -> str | None:
    """Return signature name if infected, None if clean. ConnectionError on transport/clamd error."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(b"zINSTREAM\0")
            while chunk := stream.read(CHUNK):
                s.sendall(struct.pack(">I", len(chunk)) + chunk)
            s.sendall(struct.pack(">I", 0))
            buf = b""
            while not buf.endswith(b"\0"):
                data = s.recv(4096)
                if not data:
                    break
                buf += data
    except OSError as e:  # includes socket.timeout
        raise ConnectionError(f"clamd transport failure: {e}") from e
    reply = buf.rstrip(b"\0").decode(errors="replace").strip()
    if not reply:
        raise ConnectionError("clamd returned empty reply")
    if reply.endswith("ERROR"):
        raise ConnectionError(f"clamd error: {reply}")
    if reply.endswith("FOUND"):
        return reply.removeprefix("stream: ").removesuffix(" FOUND")
    if reply.endswith("OK"):
        return None
    raise ConnectionError(f"clamd unexpected reply: {reply}")
