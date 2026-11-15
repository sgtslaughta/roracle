"""Minimal clamd INSTREAM client (stdlib only)."""

import socket
import struct
from typing import BinaryIO

from roracle.queue import NonRetryable

CHUNK = 64 * 1024


class _LocalReadError(Exception):
    """Wraps a local stream read failure so it is not mistaken for a socket error."""


def _read(stream: BinaryIO) -> bytes:
    try:
        return stream.read(CHUNK)
    except OSError as e:
        raise _LocalReadError from e


def scan(stream: BinaryIO, host: str, port: int = 3310, timeout: float = 30) -> str | None:
    """Return signature name if infected, None if clean (fails closed otherwise).

    Raises ConnectionError (retryable) on transport/clamd failure or a truncated or
    unexpected reply, NonRetryable if clamd rejects the size. Note: clamd
    StreamMaxLength must be >= the 500 MB upload limit. Local stream read errors
    propagate unchanged.
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        raise ConnectionError(f"clamd transport failure: {e}") from e
    with s:
        try:
            s.sendall(b"zINSTREAM\0")
            while chunk := _read(stream):
                s.sendall(struct.pack(">I", len(chunk)))
                s.sendall(chunk)
            s.sendall(struct.pack(">I", 0))
            buf = b""
            while not buf.endswith(b"\0"):
                data = s.recv(4096)
                if not data:
                    break
                buf += data
        except _LocalReadError as e:
            raise e.__cause__ from None
        except OSError as e:  # includes socket.timeout
            raise ConnectionError(f"clamd transport failure: {e}") from e
    if not buf.endswith(b"\0"):
        raise ConnectionError("clamd reply truncated")
    reply = buf.rstrip(b"\0").decode(errors="replace").strip()
    if not reply:
        raise ConnectionError("clamd returned empty reply")
    if "size limit exceeded" in reply:
        raise NonRetryable("file exceeds the malware scanner size limit")
    if reply.endswith("ERROR"):
        raise ConnectionError(f"clamd error: {reply}")
    if reply.endswith("FOUND"):
        return reply.removeprefix("stream: ").removesuffix(" FOUND")
    if reply.endswith("OK"):
        return None
    raise ConnectionError(f"clamd unexpected reply: {reply}")
