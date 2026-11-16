import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpcore
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from roracle.config import Settings
from roracle.connectors import RunDeadline, netguard
from roracle.connectors import http as crawl
from roracle.connectors.netguard import Blocked, TooLarge
from roracle.connectors.run import handle_connector_run
from roracle.models import Connector, ConnectorRun, Document

PAGES = {
    "/index.html": b"""<html><body>
        <a href="/a.txt#frag">a</a> <a href="sub/page.html">sub</a> <a href="/private/x.txt">p</a>
        <a href="http://localhost:{port}/offorigin.txt">off</a> <a href="mailto:x@y.z">m</a>
        <a href="javascript:void(0)">j</a> <a href="/doc.pdf">pdf</a> <a href="/redir-ok">r</a> <a href="/missing.pdf">gone</a></body></html>""",
    "/sub/page.html": b'<a href="deep/page.html">d</a>',
    "/sub/deep/page.html": b'<a href="d.txt">d</a>',
    "/sub/deep/d.txt": b"deep doc",
    "/a.txt": b"alpha text",
    "/doc.pdf": b"%PDF-1.4 fake",
    "/private/x.txt": b"secret stuff",
    "/offorigin.txt": b"off origin",
}
TYPES = {".html": "text/html", ".txt": "text/plain", ".pdf": "application/pdf"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        srv = self.server
        srv.hits.append((self.headers.get("Host", "").split(":")[0], self.path))
        p = self.path
        if p == "/robots.txt":
            if srv.robots_status:
                return self.send_error(srv.robots_status)
            if srv.robots is None:
                return self.send_error(404)
            return self.reply(srv.robots.encode(), "text/plain")
        if p == "/redir-ok":
            return self.redirect("/a.txt")
        if p == "/redir-meta":
            return self.redirect("http://169.254.169.254/latest/meta-data/")
        if p == "/redir-private":
            return self.redirect("http://10.0.0.1/")
        if p == "/redir-file":
            return self.redirect("file:///etc/passwd")
        if p == "/redir-off":
            return self.redirect(f"http://localhost:{srv.server_port}/a.txt")
        if p == "/loop":
            return self.redirect("/loop")
        if p == "/slow-header":
            self.wfile.write(b"HTTP/1.0 200 OK\r\n")
            try:
                for _ in range(100):
                    self.wfile.write(b"X-Pad: 1\r\n")
                    self.wfile.flush()
                    time.sleep(0.3)
            except OSError:
                pass
            return
        if p == "/utf8":  # UTF-8 links, no meta charset, no header charset
            return self.reply(
                '<a href="http://b\u00fccher.example/x">a</a><a href="/caf\u00e9.txt">b</a>'.encode(),
                "text/html",
            )
        if p == "/gz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", "3")
            self.end_headers()
            return self.wfile.write(b"abc")
        if p == "/many":
            links = "".join(f'<a href="/m/{i}.txt">x</a>' for i in range(10_000))
            return self.reply(f"<html>{links}</html>".encode(), "text/html")
        if p == "/bightml":
            return self.reply(b"<html>" + b"a" * (5 * 1024 * 1024 + 1), "text/html")
        if p == "/big":
            return self.reply(b"x" * 5000, "text/plain")
        if p == "/stream":  # no Content-Length: body ends at close
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            for _ in range(100):
                self.wfile.write(b"y" * 1000)
            return
        if p == "/slow":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"z")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass
            return
        body = PAGES.get(p)
        if body is None:
            return self.send_error(404)
        body = body.replace(b"{port}", str(srv.server_port).encode())
        self.reply(body, TYPES[p[p.rfind(".") :]], etag='"v1"' if p == "/a.txt" else None)

    def redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def reply(self, body, ct, etag=None):
        self.send_response(200)
        self.send_header("Content-Type", ct + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def srv():
    s = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    s.hits = []
    s.robots = "User-agent: *\nDisallow: /private/\n"
    s.robots_status = None
    s.base = f"http://127.0.0.1:{s.server_port}"
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


@pytest.fixture
def cfg(settings):
    return settings.model_copy(update={"connector_http_allow_cidrs": ["127.0.0.0/8"]})


@pytest.fixture
def pool(cfg):
    p = netguard.client(cfg)
    yield p
    p.close()


def crawl_items(srv, cfg, **kw):
    config = {"start_urls": [srv.base + "/index.html"], **kw}
    out = list(crawl.items(config, cfg))
    return {i.key.removeprefix(srv.base): i for i in out}


# --- guarded connect ---


def test_loopback_blocked_without_allowlist_allowed_with(srv, settings):
    plain = netguard.client(settings.model_copy(update={"connector_http_allow_cidrs": []}))
    with pytest.raises(Blocked):
        netguard.fetch(plain, srv.base + "/a.txt", 1000, 5)
    assert srv.hits == []  # never connected
    allowed = netguard.client(settings.model_copy(update={"connector_http_allow_cidrs": ["127.0.0.0/8"]}))
    r = netguard.fetch(allowed, srv.base + "/a.txt", 1000, 5)
    assert (r.status, r.content_type, r.etag) == (200, "text/plain", '"v1"')
    assert Path(r.path).read_bytes() == b"alpha text"


def test_metadata_and_mapped_literals_blocked_even_when_allowlisted(settings):
    p = netguard.client(
        settings.model_copy(update={"connector_http_allow_cidrs": ["169.254.0.0/16", "10.0.0.0/8"]})
    )
    for url in ("http://169.254.169.254/latest/meta-data/", "http://[::ffff:a9fe:a9fe]/",
                "http://0x7f.1/", "http://2130706433/", "http://[fe80::1]/"):  # fmt: skip
        with pytest.raises(Blocked):
            netguard.fetch(p, url, 1000, 5)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/", "http://u:p@example.com/", "http:///x"],
)
def test_bad_scheme_or_authority_rejected(pool, url):
    with pytest.raises(Blocked):
        netguard.fetch(pool, url, 1000, 5)


def test_redirects_revetted(srv, pool, settings):
    assert Path(netguard.fetch(pool, srv.base + "/redir-ok", 1000, 5).path).read_bytes() == b"alpha text"
    for path in ("/redir-meta", "/redir-private", "/redir-file", "/loop"):
        with pytest.raises(Blocked):
            netguard.fetch(pool, srv.base + path, 1000, 5)


def fake_dns(monkeypatch, answers):
    calls = []

    def gai(host, port, *a, **kw):
        if host not in answers:  # IP literal handed to the socket layer: no DNS involved
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in answers[host]]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", gai)
    return calls


def test_dns_rebinding_blocked_at_connect(srv, settings, monkeypatch):
    calls = fake_dns(
        monkeypatch, {"evil.example": ["127.0.0.1"], "mixed.example": ["93.184.216.34", "127.0.0.1"]}
    )
    p = netguard.client(settings.model_copy(update={"connector_http_allow_cidrs": []}))
    port = srv.server_port
    for host in ("evil.example", "mixed.example"):  # mixed answers fail closed
        with pytest.raises(Blocked):
            netguard.fetch(p, f"http://{host}:{port}/a.txt", 1000, 5)
    assert srv.hits == []
    assert calls == ["evil.example", "mixed.example"]  # one resolution per attempt, none before connect


def test_single_resolution_per_connection_and_allowlist(srv, settings, monkeypatch):
    calls = fake_dns(monkeypatch, {"intranet.example": ["127.0.0.1"]})
    p = netguard.client(settings.model_copy(update={"connector_http_allow_cidrs": ["127.0.0.0/8"]}))
    r = netguard.fetch(p, f"http://intranet.example:{srv.server_port}/a.txt", 1000, 5)
    assert r.status == 200 and calls == ["intranet.example"]
    assert srv.hits[0][0] == "intranet.example"  # Host header keeps the URL host


def test_size_and_deadline_caps(srv, pool, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(TooLarge):
        netguard.fetch(pool, srv.base + "/big", 1000, 5)  # Content-Length
    with pytest.raises(TooLarge):
        netguard.fetch(pool, srv.base + "/stream", 1000, 5)  # streamed, no length
    with pytest.raises(TimeoutError):
        netguard.fetch(pool, srv.base + "/slow", 10_000, 0.7)
    assert list(tmp_path.iterdir()) == []  # temp files cleaned on every abort
    assert netguard.fetch(pool, srv.base + "/nope", 1000, 5).path is None  # non-2xx: no body kept


def test_deadline_is_absolute_across_phases(srv, pool):
    for path in ("/slow-header", "/slow"):
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            netguard.fetch(pool, srv.base + path, 10_000, 1.0)
        assert time.monotonic() - t0 < 1.6, path


def test_content_encoding_skipped_and_html_cap(srv, cfg):
    with pytest.raises(netguard.Skipped, match="content-encoding"):
        netguard.fetch(netguard.client(cfg), srv.base + "/gz", 1000, 5)
    with pytest.raises(RuntimeError, match="TooLarge"):
        list(crawl.items({"start_urls": [srv.base + "/bightml"]}, cfg))  # html capped at 5 MiB
    assert [
        i.filename for i in crawl.items({"start_urls": [srv.base + "/bightml"], "ingest_html": True}, cfg)
    ]


def test_links_decode_utf8_by_default(srv, cfg):
    r = netguard.fetch(netguard.client(cfg), srv.base + "/utf8", 10_000, 5)
    got = crawl.links(r.path, r.url, r.charset)
    assert got == ["http://xn--bcher-kva.example/x", srv.base + "/caf%C3%A9.txt"]


def test_frontier_bounded_by_page_budget(srv, cfg):
    c = crawl.Crawl({"start_urls": [srv.base + "/many"], "max_pages": 5}, cfg, None)
    list(c.run())
    assert 0 < c.max_queue <= 5 and c.fetched == 5


def test_robots_5xx_disallows_all_4xx_allows(srv, cfg):
    srv.robots_status = 503
    assert crawl_items(srv, cfg) == {}
    srv.robots_status = 403
    assert "/a.txt" in crawl_items(srv, cfg)


def test_page_errors_reach_warn_callback(srv, cfg):
    msgs = []
    list(crawl.items({"start_urls": [srv.base + "/index.html"]}, cfg, None, msgs.append))
    assert any("/missing.pdf: HTTP 404" in m for m in msgs)


def test_idn_host_resolved_once_and_requested_ascii(srv, settings, monkeypatch):
    calls = fake_dns(monkeypatch, {"xn--bcher-kva.example": ["127.0.0.1"]})
    p = netguard.client(settings.model_copy(update={"connector_http_allow_cidrs": ["127.0.0.0/8"]}))
    r = netguard.fetch(p, f"http://b\u00fccher.example:{srv.server_port}/a.txt", 1000, 5)
    assert (
        r.status == 200 and calls == ["xn--bcher-kva.example"] and srv.hits[0][0] == "xn--bcher-kva.example"
    )


def test_no_proxy_env_used(srv, settings, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    p = netguard.client(settings.model_copy(update={"connector_http_allow_cidrs": ["127.0.0.0/8"]}))
    assert netguard.fetch(p, srv.base + "/a.txt", 1000, 5).status == 200
    assert isinstance(p, httpcore.ConnectionPool)


# --- crawl ---


def test_crawl_blocked_without_allowlist_fails_run_source(srv, settings):
    plain = settings.model_copy(update={"connector_http_allow_cidrs": []})
    with pytest.raises(RuntimeError, match="not publicly routable"):
        list(crawl.items({"start_urls": [srv.base + "/index.html"]}, plain))


def test_crawl_items_fields_and_same_origin(srv, cfg):
    got = crawl_items(srv, cfg)
    assert set(got) == {"/a.txt", "/doc.pdf"}  # html not ingested; /redir-ok lands on the seen /a.txt
    a = got["/a.txt"]
    assert (a.etag, a.filename, a.content_type, a.size) == ('"v1"', "a.txt", "text/plain", 10)
    assert not any(h[0] == "localhost" for h in srv.hits)  # off-origin link never fetched
    assert "/private/x.txt" not in [h[1] for h in srv.hits]  # robots
    assert crawl_items(srv, cfg, respect_robots=True)  # idempotent


def test_item_open_valid_until_advance_then_temp_gone(srv, cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    it = crawl.items({"start_urls": [srv.base + "/index.html"]}, cfg)
    first = next(it)
    with first.open() as f:
        assert f.read() in (b"alpha text", b"%PDF-1.4 fake")
    list(it)
    assert list(tmp_path.iterdir()) == []


def test_robots_off_and_missing(srv, cfg):
    assert "/private/x.txt" in crawl_items(srv, cfg, respect_robots=False)
    srv.robots = None  # 404 = allow all
    assert "/private/x.txt" in crawl_items(srv, cfg)


def test_depth_and_page_caps(srv, cfg):
    d = srv.base + "/sub/deep/d.txt"
    c = {"start_urls": [srv.base + "/sub/page.html"]}
    assert d not in [i.key for i in crawl.items({**c, "max_depth": 1}, cfg)]
    assert d in [i.key for i in crawl.items({**c, "max_depth": 2}, cfg)]
    pages = lambda: [h[1] for h in srv.hits if h[1] != "/robots.txt"]
    srv.hits.clear()
    list(crawl.items({"start_urls": [srv.base + "/index.html"], "max_pages": 2}, cfg))
    assert pages() == ["/index.html", "/a.txt"]
    srv.hits.clear()
    list(crawl.items({"start_urls": [srv.base + "/index.html"], "max_depth": 0}, cfg))
    assert pages() == ["/index.html"]


def test_max_pages_spans_seeds(srv, cfg):
    srv.hits.clear()
    seeds = [srv.base + "/a.txt", srv.base + "/doc.pdf", srv.base + "/sub/deep/d.txt"]
    keys = [i.key for i in crawl.items({"start_urls": seeds, "max_pages": 2}, cfg)]
    assert len(keys) == 2


def test_ingest_html_and_offorigin_redirect(srv, cfg):
    got = crawl_items(srv, cfg, ingest_html=True, max_depth=0)
    assert set(got) == {"/index.html"} and got["/index.html"].content_type == "text/html"
    assert got["/index.html"].filename == "index.html"
    with pytest.raises(RuntimeError, match="redirected off-origin"):
        crawl_items(srv, cfg, start_urls=[srv.base + "/redir-off"])
    assert not any(h[0] == "localhost" for h in srv.hits)  # the off-origin target is never requested


def test_run_deadline(srv, cfg):
    with pytest.raises(RunDeadline):
        list(crawl.items({"start_urls": [srv.base + "/index.html"]}, cfg, time.monotonic() - 1))


def test_filename_for():
    assert crawl.filename_for("http://h/", "text/html") == "index.html"
    assert crawl.filename_for("http://h/files/My%20Doc", "application/pdf") == "My Doc.pdf"
    assert crawl.filename_for("http://h/x/paper.pdf?dl=1", "application/pdf") == "paper.pdf"


# --- settings / API / run ---


def test_allow_cidrs_setting_parsing(monkeypatch):
    base = {"database_url": "postgresql+psycopg://x@h/d", "s3_endpoint": "http://s", "s3_access_key": "a",
            "s3_secret_key": "b", "oidc_issuer": "http://i", "oidc_jwks_url": "http://j"}  # fmt: skip
    monkeypatch.setenv("ROR_CONNECTOR_HTTP_ALLOW_CIDRS", "10.0.0.0/8, 192.168.0.0/16")
    assert Settings().connector_http_allow_cidrs == ["10.0.0.0/8", "192.168.0.0/16"]
    monkeypatch.setenv("ROR_CONNECTOR_HTTP_ALLOW_CIDRS", '["fd00::/8"]')
    assert Settings().connector_http_allow_cidrs == ["fd00::/8"]
    monkeypatch.setenv("ROR_CONNECTOR_HTTP_ALLOW_CIDRS", "10.0.0.0/8,notacidr")
    with pytest.raises(ValidationError):
        Settings()
    assert Settings.model_fields["connector_http_timeout_s"].default == 60 and base


@pytest.mark.parametrize(
    "cidr",
    [
        "2002::/16",
        "64:ff9b:1::/48",
        "::/0",
        "::/64",
        "2001::/32",
        "::ffff:10.0.0.0/104",
        "2002:a9fe:a9fe::/96",
        "2001:0:1::/96",
    ],
)
def test_wrapper_overlapping_cidr_must_be_slash96(cidr, monkeypatch):
    monkeypatch.setenv("ROR_CONNECTOR_HTTP_ALLOW_CIDRS", cidr)
    with pytest.raises(ValidationError, match="/96|cannot be allowlisted"):
        Settings()


@pytest.mark.parametrize("cidr", ["64:ff9b::/96", "64:ff9b:1:2::/96", "fd12:3456::/32", "10.0.0.0/8"])
def test_translator_prefix_and_plain_cidrs_accepted(cidr, monkeypatch):
    monkeypatch.setenv("ROR_CONNECTOR_HTTP_ALLOW_CIDRS", cidr)
    assert Settings().connector_http_allow_cidrs == [cidr]


def http_body(**cfg):
    return {
        "name": "web",
        "kind": "http",
        "visibility": "everyone",
        "config": {"start_urls": ["https://example.org/docs/"], **cfg},
    }


def test_api_http_config_validation(client, adm):
    ok = client.post("/admin/connectors", headers=adm, json=http_body())
    assert ok.status_code == 201
    assert ok.json()["config"] == {"start_urls": ["https://example.org/docs/"], "max_depth": 1, "max_pages": 100,
                                   "respect_robots": True, "ingest_html": False}  # fmt: skip
    bad = [
        {"start_urls": []},
        {"start_urls": [f"http://h{i}.example/" for i in range(21)]},
        {"start_urls": ["ftp://example.org/"]},
        {"start_urls": ["file:///etc/passwd"]},
        {"start_urls": ["http://user:pw@example.org/"]},
        {"start_urls": ["http:///x"]},
        {"max_depth": 4},
        {"max_depth": -1},
        {"max_pages": 0},
        {"max_pages": 1001},
        {"bogus": 1},
    ]
    for i, c in enumerate(bad):
        b = http_body(**c)
        b["name"] = f"bad{i}"
        assert client.post("/admin/connectors", headers=adm, json=b).status_code == 422, c
    cid = ok.json()["id"]
    r = client.patch(f"/admin/connectors/{cid}", headers=adm, json={"config": {"max_depth": 3}})
    assert r.status_code == 200 and r.json()["config"]["max_depth"] == 3
    assert (
        client.patch(f"/admin/connectors/{cid}", headers=adm, json={"config": {"max_depth": 9}}).status_code
        == 422
    )


def test_http_run_ingests_and_blocked_run_fails(db_session, session_factory, settings, store, owner, srv):
    def mk(name):
        c = Connector(name=name, kind="http", owner_id=owner.id, visibility="everyone",
                      config={"start_urls": [srv.base + "/index.html"]})  # fmt: skip
        db_session.add(c)
        db_session.commit()
        return c

    def run(c, s):
        handle_connector_run(
            db_session, {"connector_id": str(c.id)}, settings=s, store=store, session_factory=session_factory
        )
        db_session.rollback()
        return db_session.scalars(
            select(ConnectorRun).where(ConnectorRun.connector_id == c.id).order_by(ConnectorRun.id.desc())
        ).first()

    open_s = settings.model_copy(update={"connector_http_allow_cidrs": ["127.0.0.0/8"]})
    c = mk("web-ok")
    r = run(c, open_s)
    assert r.status == "partial" and r.stats["ingested"] == 2, (
        r.status,
        r.stats,
        r.log,
    )  # 404 link = warning
    assert "/missing.pdf: HTTP 404" in r.log
    assert db_session.scalars(select(Document).where(Document.connector_id == c.id)).all()
    r2 = run(c, open_s)
    assert r2.status == "partial" and r2.stats["ingested"] == 0 and r2.stats["unchanged"] >= 1, r2.stats
    bad = run(mk("web-blocked"), settings.model_copy(update={"connector_http_allow_cidrs": []}))
    assert bad.status == "failed" and "publicly routable" in bad.log
