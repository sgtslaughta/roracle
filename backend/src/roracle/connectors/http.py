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
from roracle.connectors.netguard import origin

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
DOC_TYPES |= {
    "image/png", "image/jpeg", "image/tiff", "image/webp", "image/bmp",
    "application/xml", "text/xml", "application/jats+xml",
    "application/x-tex", "text/x-tex", "application/x-latex",
    "text/csv", "text/tab-separated-values", "application/json", "application/epub+zip",
    "application/zip", "application/x-zip-compressed", "application/gzip", "application/x-gzip",
    "application/x-tar", "application/x-gtar",
}  # fmt: skip
DOC_EXTS = (
    ".pdf", ".doc", ".docx", ".rtf", ".odt", ".ppt", ".pptx", ".odp", ".xls", ".xlsx", ".ods",
    ".txt", ".md", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".xml", ".nxml",
    ".tex", ".csv", ".tsv", ".json", ".epub", ".zip", ".tar", ".tgz", ".tar.gz", ".gz",
)  # fmt: skip
DOC_PREFIXES = ("application/vnd.openxmlformats-officedocument.", "application/vnd.oasis.opendocument.")
DOC_PREFIXES += ("application/vnd.ms-",)
HTML_TYPES = ("text/html", "application/xhtml+xml")
Origin = tuple[str, str, int]


def is_document(ct: str, ingest_html: bool, url: str = "") -> bool:
    """`application/octet-stream` (or no type) falls back to the URL's file extension."""
    if ct in ("", "application/octet-stream"):
        return urlsplit(url).path.lower().endswith(DOC_EXTS)
    return ct in DOC_TYPES or ct.startswith(DOC_PREFIXES) or (ingest_html and ct in HTML_TYPES)


def links(path: str, base: str, charset: str = "") -> list[str]:
    """Absolute http(s) URLs (fragments stripped) from `<a href>`; [] if the page is too big or unparsable."""
    try:
        if os.path.getsize(path) > HTML_MAX:
            return []
        with open(path, "rb") as f:
            data = f.read()
        try:
            parser = lxml.html.HTMLParser(encoding=charset or "utf-8")
        except LookupError:
            parser = lxml.html.HTMLParser(encoding="utf-8")
        doc = lxml.html.fromstring(data, parser=parser)
    except Exception:  # noqa: BLE001 - any parse failure just means no links
        return []
    out = []
    for href in doc.xpath("//a/@href"):
        try:
            url, _ = urldefrag(netguard.check_url(urljoin(base, href.strip())))
            u = urlsplit(url)
            if u.scheme in ("http", "https") and u.hostname and u.username is None and u.password is None:
                out.append(url)
        except (ValueError, netguard.Blocked):
            continue
    return out


def filename_for(url: str, ct: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).replace("\\", "_").replace("\0", "")[:200]
    name = name or "index.html"
    if "." not in name:
        name += (mimetypes.guess_extension(ct) or "") if ct else ""
    return name


class Crawl:
    def __init__(self, config: dict, settings: Settings, deadline: float | None, warn=None) -> None:
        self.cfg = config
        self.cap = settings.max_upload_bytes
        self.timeout = settings.connector_http_timeout_s
        self.run_deadline = deadline
        self.warn = warn or (lambda msg: None)
        self.pool = netguard.client(settings)
        self.robots: dict[Origin, RobotFileParser | None] = {}
        self.fetched = 0
        self.ok = 0
        self.max_queue = 0
        self.errors: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        self.warn(msg)
        log.info("crawl: %s", msg)

    def request(self, url: str, cap: int, type_caps: dict[str, int] | None = None) -> netguard.Fetched:
        left = self.timeout
        if self.run_deadline is not None:
            remaining = self.run_deadline - time.monotonic()
            if remaining <= 0:
                raise RunDeadline("run time cap reached")
            left = min(left, remaining)
        return netguard.fetch(self.pool, url, cap, left, same_origin=True, type_caps=type_caps)

    def _load_robots(self, o: Origin, url: str) -> RobotFileParser | None:
        """2xx: parse. 5xx: disallow all (RFC 9309). 4xx/404: allow all. Network error/oversize: allow all
        (ruling: robots is advisory, a broken robots.txt must not silently stop a crawl)."""
        try:
            r = self.request(f"{o[0]}://{urlsplit(url).netloc}/robots.txt", ROBOTS_MAX)
        except RunDeadline:
            raise
        except Exception as e:  # noqa: BLE001 - see docstring
            log.info("robots.txt for %s unavailable: %s", o, e)
            return None
        rp = RobotFileParser()
        if r.path:
            try:
                with open(r.path, "rb") as f:
                    rp.parse(f.read().decode("utf-8", "replace").splitlines())
            finally:
                os.unlink(r.path)
            return rp
        if r.status >= 500:
            rp.disallow_all = True
            return rp
        return None

    def allowed(self, url: str) -> bool:
        if not self.cfg.get("respect_robots", True):
            return True
        o = origin(url)
        if o not in self.robots:
            self.robots[o] = self._load_robots(o, url)
        rp = self.robots[o]
        return rp is None or rp.can_fetch(netguard.UA, url)

    def run(self) -> Iterator[Item]:
        seen: set[str] = set()  # fetched or queued: never enqueued twice
        max_pages = self.cfg.get("max_pages", 100)
        max_depth = self.cfg.get("max_depth", 1)
        ingest_html = self.cfg.get("ingest_html", False)
        type_caps = None if ingest_html else dict.fromkeys(HTML_TYPES, HTML_MAX)
        for seed in self.cfg["start_urls"]:
            try:
                seed = urldefrag(netguard.check_url(seed))[0]
            except netguard.Blocked as e:
                self.error(f"{seed}: {e}")
                continue
            seed_origin = origin(seed)
            queue = deque([(seed, 0)])
            seen.add(seed)
            while queue:
                url, depth = queue.popleft()
                if self.fetched >= max_pages:
                    return
                if not self.allowed(url):
                    log.info("robots.txt disallows %s", url)
                    continue
                self.fetched += 1
                try:
                    r = self.request(url, self.cap, type_caps)
                except RunDeadline:
                    raise
                except Exception as e:  # noqa: BLE001 - one bad page must not stop the crawl
                    self.error(f"{url}: {type(e).__name__}: {str(e)[:150]}")
                    continue
                if r.path is None:
                    self.error(f"{url}: HTTP {r.status}")
                    continue
                self.ok += 1
                try:
                    ct = r.content_type
                    if ct in HTML_TYPES and depth < max_depth:
                        seen.add(r.url)
                        for u in links(r.path, r.url, r.charset):
                            if len(queue) + self.fetched >= max_pages:
                                break  # frontier never outgrows the page budget
                            if u in seen or origin(u) != seed_origin or not self.allowed(u):
                                continue
                            seen.add(u)
                            queue.append((u, depth + 1))
                        self.max_queue = max(self.max_queue, len(queue))
                    if is_document(ct, ingest_html, r.url):
                        yield self.item(r, ct)
                finally:
                    os.unlink(r.path)

    def item(self, r: netguard.Fetched, ct: str) -> Item:
        name = filename_for(r.url, ct)
        if ct in ("", "application/octet-stream"):
            ct = mimetypes.guess_type(name)[0] or ct or "application/octet-stream"
        return Item(
            key=r.url,
            etag=r.etag,
            filename=name,
            content_type=ct,
            size=os.path.getsize(r.path),
            open=lambda p=r.path: open(p, "rb"),
        )


def items(config: dict, settings: Settings, deadline: float | None = None, warn=None) -> Iterator[Item]:
    return _items(Crawl(config, settings, deadline, warn))


def _items(c: Crawl) -> Iterator[Item]:
    try:
        yield from c.run()
    finally:
        c.pool.close()
    if c.ok == 0 and c.errors:
        raise RuntimeError("crawl fetched nothing: " + "; ".join(c.errors[:3]))
