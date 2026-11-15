"""SSRF guard for the HTTP connector.

Name resolution happens exactly once per connection, inside `GuardedBackend.connect_tcp`: every address the
name resolves to is vetted and the socket connects to a vetted IP literal. There is no earlier "pre-check"
resolution, so DNS rebinding between check and use cannot happen. TLS keeps verifying against the URL host
(httpcore passes the origin host as server_hostname, independent of the IP we dial). Redirects are followed
manually and each hop goes through the same guarded connect. httpcore ignores proxy environment variables.
"""

import contextvars
import ipaddress
import os
import socket
import tempfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import quote, urljoin, urlsplit

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


class Skipped(Exception):
    """Response is reachable but not usable (off-origin redirect, content-encoding)."""


# Cloud metadata endpoints outside link-local: never reachable, allowlist or not.
NEVER = [
    ipaddress.ip_network(n)
    for n in (
        "fd00:ec2::254/128",
        "100.100.100.200/32",
        "168.63.129.16/32",
        "192.0.0.192/32",
    )
]
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("fetch_deadline", default=None)


def _embedded_v4(ip: ipaddress.IPv6Address) -> tuple[str, list[ipaddress.IPv4Address]] | None:
    """(kind, wrapped IPv4 candidates) for IPv6 forms that embed an IPv4 address."""
    if ip.ipv4_mapped:
        return "mapped", [ip.ipv4_mapped]
    if ip.sixtofour:
        return "6to4", [ip.sixtofour]
    if ip.teredo:
        return "teredo", [ip.teredo[1]]  # the client address
    if ip in (ipaddress.IPv6Address("::"), ipaddress.IPv6Address("::1")):
        return None
    p = ip.packed
    if p[:12] == bytes.fromhex("0064ff9b0000000000000000"):
        return "nat64", [ipaddress.IPv4Address(p[12:])]
    if p[:6] == bytes.fromhex("0064ff9b0001"):  # local-use NAT64: RFC 6052 /48 layout, and the plain tail
        cands = [ipaddress.IPv4Address(p[6:8] + p[9:11])]
        if p[12:] != bytes(4):
            cands.append(ipaddress.IPv4Address(p[12:]))
        return "local64", cands
    if p[:12] == bytes(12):
        return "compat", [ipaddress.IPv4Address(p[12:])]  # deprecated IPv4-compatible ::/96
    return None


def _never(a: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return a.is_link_local or a.is_multicast or a.is_unspecified or any(a in n for n in NEVER)


def _listed(a: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_cidrs: list[str]) -> bool:
    return any(a in ipaddress.ip_network(c, strict=False) for c in allow_cidrs)


def check_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address, allow_cidrs: list[str]) -> None:
    """Raise Blocked unless `ip` may be dialled. Never allowed (even inside an allowlisted CIDR): link-local,
    multicast, unspecified and known metadata addresses (R10), checked on the address and on any IPv4 it
    wraps. Plain addresses must be globally routable or allowlisted. An IPv6 that embeds an IPv4 (mapped,
    NAT64, 6to4, Teredo, ::/96) must pass twice: the wrapped IPv4 (global or allowlisted) AND the wrapper
    itself. The wrapper passes by default only for 64:ff9b::/96 and for mapped addresses of a global IPv4;
    6to4, Teredo, local-use NAT64 and ::/96 need an allowlist CIDR covering the wrapper."""
    addr = ipaddress.ip_address(ip.split("%")[0] if isinstance(ip, str) else ip)  # drop IPv6 zone id
    emb = _embedded_v4(addr) if isinstance(addr, ipaddress.IPv6Address) else None
    inners = emb[1] if emb else []
    if _never(addr) or any(_never(i) for i in inners):
        raise Blocked(f"address {addr} is never allowed")
    if emb is None:
        ok = addr.is_global or _listed(addr, allow_cidrs)
    else:
        outer = (
            emb[0] == "nat64" or (emb[0] == "mapped" and inners[0].is_global) or _listed(addr, allow_cidrs)
        )
        ok = outer and all(i.is_global or _listed(i, allow_cidrs) for i in inners)
    if not ok:
        raise Blocked(f"address {addr} is not publicly routable and not in ROR_CONNECTOR_HTTP_ALLOW_CIDRS")


def _clamp(timeout: float | None, exc: type[Exception]) -> float | None:
    """Per-operation timeout bounded by the fetch's absolute deadline."""
    end = _deadline.get()
    if end is None:
        return timeout
    left = end - time.monotonic()
    if left <= 0:
        raise exc("request deadline")
    return left if timeout is None else min(timeout, left)


class _Stream(httpcore.NetworkStream):
    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._s = inner

    def read(self, max_bytes, timeout=None):
        return self._s.read(max_bytes, _clamp(timeout, httpcore.ReadTimeout))

    def write(self, buffer, timeout=None):
        self._s.write(buffer, _clamp(timeout, httpcore.WriteTimeout))

    def close(self):
        self._s.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return _Stream(
            self._s.start_tls(ssl_context, server_hostname, _clamp(timeout, httpcore.ConnectTimeout))
        )

    def get_extra_info(self, info):
        return self._s.get_extra_info(info)


class GuardedBackend(httpcore.SyncBackend):
    def __init__(self, allow_cidrs: list[str] | None = None) -> None:
        self.allow_cidrs = list(allow_cidrs or [])

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # the only resolution; getaddrinfo itself has no app-level timeout (resolv.conf bounds it)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not infos:
            raise Blocked(f"{host} does not resolve")
        for info in infos:  # fail closed: one bad answer poisons the lot
            check_ip(info[4][0], self.allow_cidrs)
        timeout = _clamp(timeout, httpcore.ConnectTimeout)
        return _Stream(super().connect_tcp(infos[0][4][0], port, timeout, local_address, socket_options))


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


def origin(url: str) -> tuple[str, str, int]:
    u = urlsplit(url)
    return u.scheme, (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)


def check_url(url: str) -> str:
    """Validate and normalise (IDNA host, percent-encoded non-ASCII path/query); returns the URL to request."""
    try:
        u = urlsplit(url)
        host, port = u.hostname, u.port
    except ValueError:
        raise Blocked("malformed url") from None
    if u.scheme not in ("http", "https"):
        raise Blocked(f"scheme {u.scheme!r} is not allowed")
    if not host or u.username is not None or u.password is not None:
        raise Blocked("url needs a host and no credentials")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise Blocked("invalid host name") from None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    path = quote(u.path, safe="/%:@!$&'()*+,;=~-._")
    query = quote(u.query, safe="=&%/:?+,;@!$'()*~-._")
    return f"{u.scheme}://{netloc}{path}" + (f"?{query}" if u.query else "")


def fetch(
    pool: httpcore.ConnectionPool,
    url: str,
    max_bytes: int,
    deadline_s: float,
    *,
    same_origin: bool = False,
    type_caps: dict[str, int] | None = None,
) -> Fetched:
    """GET with manual redirects (<= 5, each re-vetted; with `same_origin` a redirect off the start origin is
    refused before it is requested). Body streamed to a temp file, `type_caps` lowers the cap per content type.
    The deadline is absolute (connect, TLS, headers and body). Raises Blocked, TooLarge, Skipped,
    TimeoutError, or the transport's errors."""
    end = time.monotonic() + deadline_s
    token = _deadline.set(end)
    try:
        return _fetch(pool, url, max_bytes, end, same_origin, type_caps or {})
    except httpcore.TimeoutException as e:
        raise TimeoutError("request deadline") from e
    finally:
        _deadline.reset(token)


def _fetch(pool, url, max_bytes, end, same_origin, type_caps) -> Fetched:
    headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
    url = check_url(url)
    start = origin(url)
    for _ in range(MAX_REDIRECTS + 1):
        left = end - time.monotonic()
        if left <= 0:
            raise TimeoutError("request deadline")
        t = {"connect": left, "read": left, "write": left, "pool": left}
        with pool.stream("GET", url, headers=headers, extensions={"timeout": t}) as r:
            loc = _header(r.headers, b"location")
            if r.status in REDIRECTS and loc:
                url = check_url(urljoin(url, loc))
                if same_origin and origin(url) != start:
                    raise Skipped(f"redirected off-origin to {url}")
                continue
            ct = _header(r.headers, b"content-type").split(";")[0].strip().lower()
            etag = _header(r.headers, b"etag") or _header(r.headers, b"last-modified")
            if not 200 <= r.status < 300:
                return Fetched(url, r.status, ct, etag, None)
            if _header(r.headers, b"content-encoding").lower() not in ("", "identity"):
                raise Skipped("unsupported content-encoding")
            cap = min(max_bytes, type_caps.get(ct, max_bytes))
            cl = _header(r.headers, b"content-length")
            if cl.isdigit() and int(cl) > cap:
                raise TooLarge(f"content-length {cl} > {cap}")
            fd, path = tempfile.mkstemp(prefix="ror-http-")
            try:
                with os.fdopen(fd, "wb") as f:
                    total = 0
                    for chunk in r.iter_stream():
                        total += len(chunk)
                        if total > cap:
                            raise TooLarge(f"body exceeds {cap} bytes")
                        if time.monotonic() > end:
                            raise TimeoutError("request deadline")
                        f.write(chunk)
            except BaseException:
                os.unlink(path)
                raise
            return Fetched(url, r.status, ct, etag, path)
    raise Blocked("too many redirects")
