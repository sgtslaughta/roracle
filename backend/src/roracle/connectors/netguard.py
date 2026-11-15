"""SSRF guard for the HTTP connector.

Name resolution happens exactly once per connection, inside `GuardedBackend.connect_tcp`: every address the
name resolves to is vetted and the socket connects to a vetted IP literal. There is no earlier "pre-check"
resolution, so DNS rebinding between check and use cannot happen. TLS keeps verifying against the URL host
(httpcore passes the origin host as server_hostname, independent of the IP we dial). Redirects are followed
manually and each hop goes through the same guarded connect. httpcore ignores proxy environment variables.
"""

import ipaddress
import os
import socket
import tempfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urljoin, urlsplit

import httpcore

from roracle.config import Settings

MAX_REDIRECTS = 5
REDIRECTS = (301, 302, 303, 307, 308)
CHUNK = 64 * 1024
try:
    UA = f"rOracle-connector/{version('roracle')}"
except PackageNotFoundError:  # pragma: no cover
    UA = "rOracle-connector"


class Blocked(Exception):
    """The request targets something the connector may not reach."""


class TooLarge(Exception):
    pass


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]  # the client address (server is checked as the outer address)
    packed = ip.packed
    if packed[:12] == bytes.fromhex("0064ff9b0000000000000000") or packed[:12] == bytes(12):
        return ipaddress.IPv4Address(packed[12:])  # NAT64 64:ff9b::/96 and deprecated IPv4-compatible ::/96
    return None


def check_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address, allow_cidrs: list[str]) -> None:
    """Raise Blocked unless `ip` may be dialled. Never allowed (even inside an allowlisted CIDR): link-local
    (cloud metadata, R10), multicast, unspecified. Everything not globally routable needs an allowlisted CIDR."""
    addr = ipaddress.ip_address(ip.split("%")[0] if isinstance(ip, str) else ip)  # drop IPv6 zone id
    if isinstance(addr, ipaddress.IPv6Address) and (inner := _embedded_v4(addr)) is not None:
        check_ip(inner, allow_cidrs)  # an embedded address is judged as the IPv4 it wraps
    if addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        raise Blocked(f"address {addr} is never allowed")
    if addr.is_global:
        return
    if any(addr in ipaddress.ip_network(c, strict=False) for c in allow_cidrs):
        return
    raise Blocked(f"address {addr} is not publicly routable and not in ROR_CONNECTOR_HTTP_ALLOW_CIDRS")


class GuardedBackend(httpcore.SyncBackend):
    def __init__(self, allow_cidrs: list[str] | None = None) -> None:
        self.allow_cidrs = list(allow_cidrs or [])

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)  # the only resolution
        if not infos:
            raise Blocked(f"{host} does not resolve")
        for info in infos:  # fail closed: one bad answer poisons the lot
            check_ip(info[4][0], self.allow_cidrs)
        return super().connect_tcp(infos[0][4][0], port, timeout, local_address, socket_options)


def client(settings: Settings) -> httpcore.ConnectionPool:
    return httpcore.ConnectionPool(
        network_backend=GuardedBackend(settings.connector_http_allow_cidrs),
        max_connections=4,
        keepalive_expiry=30,
        retries=0,
    )


@dataclass
class Fetched:
    url: str  # final URL after redirects
    status: int
    content_type: str  # media type only, lowercase
    etag: str  # ETag, else Last-Modified, else ""
    path: str | None  # temp file with the body (2xx only); caller deletes


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str:
    return next((v.decode("latin-1").strip() for k, v in headers if k.lower() == name), "")


def check_url(url: str) -> None:
    u = urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise Blocked(f"scheme {u.scheme!r} is not allowed")
    if not u.hostname or u.username is not None or u.password is not None:
        raise Blocked("url needs a host and no credentials")


def fetch(pool: httpcore.ConnectionPool, url: str, max_bytes: int, deadline_s: float) -> Fetched:
    """GET with manual redirects (<= 5, each re-vetted). Body streamed to a temp file. Raises Blocked,
    TooLarge, TimeoutError (deadline), or the transport's errors."""
    end = time.monotonic() + deadline_s
    headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        left = end - time.monotonic()
        if left <= 0:
            raise TimeoutError("request deadline")
        t = {"connect": left, "read": left, "write": left, "pool": left}
        with pool.stream("GET", url, headers=headers, extensions={"timeout": t}) as r:
            loc = _header(r.headers, b"location")
            if r.status in REDIRECTS and loc:
                url = urljoin(url, loc)
                continue
            ct = _header(r.headers, b"content-type").split(";")[0].strip().lower()
            etag = _header(r.headers, b"etag") or _header(r.headers, b"last-modified")
            if not 200 <= r.status < 300:
                return Fetched(url, r.status, ct, etag, None)
            cl = _header(r.headers, b"content-length")
            if cl.isdigit() and int(cl) > max_bytes:
                raise TooLarge(f"content-length {cl} > {max_bytes}")
            fd, path = tempfile.mkstemp(prefix="ror-http-")
            try:
                with os.fdopen(fd, "wb") as f:
                    total = 0
                    for chunk in r.iter_stream():
                        total += len(chunk)
                        if total > max_bytes:
                            raise TooLarge(f"body exceeds {max_bytes} bytes")
                        if time.monotonic() > end:
                            raise TimeoutError("request deadline")
                        f.write(chunk)
            except BaseException:
                os.unlink(path)
                raise
            return Fetched(url, r.status, ct, etag, path)
    raise Blocked("too many redirects")
