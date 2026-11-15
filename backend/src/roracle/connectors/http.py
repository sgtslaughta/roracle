"""HTTP crawl source: BFS from seed URLs, same origin only, SSRF-guarded (connectors/netguard.py).

Every fetch (pages, documents, robots.txt) goes through `netguard.fetch`. Links come from `<a href>` of
HTML pages (parsed only if <= 5 MiB). Document responses become Items; the final URL is the key.
"""

import logging
import mimetypes
import os
import time
from collections import deque
from collections.abc import Iterator
from urllib.parse import unquote, urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import lxml.html

from roracle.config import Settings
from roracle.connectors import Item, RunDeadline, netguard

log = logging.getLogger(__name__)
HTML_MAX = 5 * 1024 * 1024
ROBOTS_MAX = 512 * 1024
DOC_TYPES = {
    "application/pdf",
    "application/msword",
    "application/rtf",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
}
DOC_PREFIXES = ("application/vnd.openxmlformats-officedocument.", "application/vnd.oasis.opendocument.")
DOC_PREFIXES += ("application/vnd.ms-",)
HTML_TYPES = ("text/html", "application/xhtml+xml")
Origin = tuple[str, str, int]


def origin(url: str) -> Origin:
    u = urlsplit(url)
    return u.scheme, (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)


def is_document(ct: str, ingest_html: bool) -> bool:
    return ct in DOC_TYPES or ct.startswith(DOC_PREFIXES) or (ingest_html and ct in HTML_TYPES)


def links(path: str, base: str) -> list[str]:
    """Absolute http(s) URLs (fragments stripped) from `<a href>`; [] if the page is too big or unparsable."""
    try:
        if os.path.getsize(path) > HTML_MAX:
            return []
        with open(path, "rb") as f:
            doc = lxml.html.fromstring(f.read())
    except Exception:  # noqa: BLE001 - any parse failure just means no links
        return []
    out = []
    for href in doc.xpath("//a/@href"):
        try:
            url, _ = urldefrag(urljoin(base, href.strip()))
            u = urlsplit(url)
            if u.scheme in ("http", "https") and u.hostname and u.username is None and u.password is None:
                out.append(url)
        except ValueError:
            continue
    return out


def filename_for(url: str, ct: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).replace("\\", "_").replace("\0", "")[:200]
    name = name or "index.html"
    if "." not in name:
        name += (mimetypes.guess_extension(ct) or "") if ct else ""
    return name


class Crawl:
    def __init__(self, config: dict, settings: Settings, deadline: float | None) -> None:
        self.cfg = config
        self.cap = settings.max_upload_bytes
        self.timeout = settings.connector_http_timeout_s
        self.run_deadline = deadline
        self.pool = netguard.client(settings)
        self.robots: dict[Origin, RobotFileParser | None] = {}
        self.fetched = 0
        self.ok = 0
        self.errors: list[str] = []

    def request(self, url: str, cap: int) -> netguard.Fetched:
        left = self.timeout
        if self.run_deadline is not None:
            remaining = self.run_deadline - time.monotonic()
            if remaining <= 0:
                raise RunDeadline("run time cap reached")
            left = min(left, remaining)
        return netguard.fetch(self.pool, url, cap, left)

    def allowed(self, url: str) -> bool:
        if not self.cfg.get("respect_robots", True):
            return True
        o = origin(url)
        if o not in self.robots:
            rp = None
            try:
                r = self.request(f"{o[0]}://{urlsplit(url).netloc}/robots.txt", ROBOTS_MAX)
                if r.path:
                    with open(r.path, "rb") as f:
                        rp = RobotFileParser()
                        rp.parse(f.read().decode("utf-8", "replace").splitlines())
                    os.unlink(r.path)
            except RunDeadline:
                raise
            except Exception as e:  # noqa: BLE001 - missing/unreadable/oversize robots.txt = no rules (R5)
                log.info("robots.txt for %s unavailable: %s", o, e)
            self.robots[o] = rp
        rp = self.robots[o]
        return rp is None or rp.can_fetch(netguard.UA, url)

    def run(self) -> Iterator[Item]:
        seen: set[str] = set()
        max_pages = self.cfg.get("max_pages", 100)
        max_depth = self.cfg.get("max_depth", 1)
        ingest_html = self.cfg.get("ingest_html", False)
        for seed in self.cfg["start_urls"]:
            seed_origin = origin(seed)
            queue = deque([(urldefrag(seed)[0], 0)])
            while queue:
                url, depth = queue.popleft()
                if url in seen or origin(url) != seed_origin:
                    continue
                seen.add(url)
                if self.fetched >= max_pages:
                    return
                if not self.allowed(url):
                    log.info("robots.txt disallows %s", url)
                    continue
                self.fetched += 1
                try:
                    r = self.request(url, self.cap)
                except RunDeadline:
                    raise
                except Exception as e:  # noqa: BLE001 - one bad page must not stop the crawl
                    self.errors.append(f"{url}: {type(e).__name__}: {str(e)[:150]}")
                    log.info("crawl skip %s: %s", url, e)
                    continue
                if r.path is None or origin(r.url) != seed_origin:  # non-2xx, or redirected off-origin
                    if r.path:
                        os.unlink(r.path)
                    else:
                        self.errors.append(f"{url}: HTTP {r.status}")
                    continue
                self.ok += 1
                try:
                    ct = r.content_type
                    if ct in HTML_TYPES and depth < max_depth:
                        seen.add(r.url)
                        queue.extend((u, depth + 1) for u in links(r.path, r.url) if u not in seen)
                    if is_document(ct, ingest_html):
                        yield self.item(r, ct)
                finally:
                    os.unlink(r.path)

    def item(self, r: netguard.Fetched, ct: str) -> Item:
        return Item(
            key=r.url,
            etag=r.etag,
            filename=filename_for(r.url, ct),
            content_type=ct,
            size=os.path.getsize(r.path),
            open=lambda p=r.path: open(p, "rb"),
        )


def items(config: dict, settings: Settings, deadline: float | None = None) -> Iterator[Item]:
    return _items(Crawl(config, settings, deadline))


def _items(c: Crawl) -> Iterator[Item]:
    try:
        yield from c.run()
    finally:
        c.pool.close()
    if c.ok == 0 and c.errors:
        raise RuntimeError("crawl fetched nothing: " + "; ".join(c.errors[:3]))
