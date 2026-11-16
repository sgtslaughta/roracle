import json
import subprocess
import uuid

import httpx
import pytest
from api_seed import seed

from roracle.auth import get_app_settings
from roracle.writing import build
from roracle.writing.ast_sanitize import Ref, sanitize
from roracle.writing.profiles import builtin

PROFILE = builtin()["journal-article"]


def _ok() -> bool:
    exe = build.pandoc_path()
    if exe is None:
        return False
    try:
        return (
            subprocess.run([exe, "--version"], capture_output=True, timeout=10, check=False).returncode == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


needs_pandoc = pytest.mark.skipif(not _ok(), reason="pandoc not available/executable")


def S(t):
    return {"t": "Str", "c": t}


def ast(*blocks, meta=None):
    return {
        "pandoc-api-version": [1, 23, 1],
        "meta": meta or {"header-includes": {"t": "MetaString", "c": "<script>"}},
        "blocks": list(blocks),
    }


def para(*inl):
    return {"t": "Para", "c": list(inl)}


def cite(*keys):
    return {
        "t": "Cite",
        "c": [
            [
                {
                    "citationId": k,
                    "citationPrefix": [],
                    "citationSuffix": [],
                    "citationMode": {"t": "NormalCitation"},
                    "citationNoteNum": 0,
                    "citationHash": 0,
                }
                for k in keys
            ],
            [S("@x")],
        ],
    }


def txt(a):
    return json.dumps(a)


# ---- sanitizer (hand-built AST) ----
def test_raw_dropped_and_meta_cleared():
    out = sanitize(
        ast(
            para(S("a"), {"t": "RawInline", "c": ["html", "<script>"]}),
            {"t": "RawBlock", "c": ["tex", "\\input{x}"]},
        ),
        [],
    )
    assert out["meta"] == {} and out["blocks"] == [para(S("a"))]


def test_image_and_link_policy():
    img = lambda u: {"t": "Image", "c": [["", [], []], [S("alt")], [u, ""]]}
    lnk = lambda u: {"t": "Link", "c": [["", [], []], [S("go")], [u, ""]]}
    uri = "data:image/png;base64,iVBORw0KGgo="
    out = sanitize(
        ast(
            para(
                img("http://x/a.png"),
                img("data:image/svg+xml;base64,AA=="),
                img(uri),
                lnk("javascript:alert(1)"),
                lnk(" javascript:x"),
                lnk("https://ok"),
                lnk("mailto:a@b"),
                lnk("#sec"),
                lnk("file:///etc/passwd"),
            )
        ),
        [],
    )
    inl = out["blocks"][0]["c"]
    assert [i["t"] for i in inl] == ["Str", "Str", "Image", "Str", "Str", "Link", "Link", "Link", "Str"]
    assert inl[2]["c"][2][0] == uri and inl[0]["c"] == "alt"
    assert [i["c"][2][0] for i in inl if i["t"] == "Link"] == ["https://ok", "mailto:a@b", "#sec"]


def test_attrs_keep_id_and_classes_only():
    bad = ["a b", ["c1", 'x"onmouseover'], [["style", "x"], ['onclick="', "y"]]]
    ok = ["sec:intro", ["note"], [["onload", "z"]]]
    out = sanitize(
        ast(
            {"t": "Header", "c": [1, bad, [S("H")]]},
            {"t": "Div", "c": [ok, [para(S("x"))]]},
            para({"t": "Span", "c": [ok, [S("s")]]}, {"t": "Code", "c": [ok, "c"]}),
        ),
        [],
    )
    h, d, p = out["blocks"]
    assert h["c"][1] == ["", ["c1"], []] and d["c"][0] == ["sec:intro", ["note"], []]
    assert p["c"][0]["c"][0] == ["sec:intro", ["note"], []] and p["c"][1]["c"][0][2] == []


def test_cite_numbering_numeric_and_references():
    a, b = f"doc:{uuid.uuid4()}", f"psg:{uuid.uuid4()}:3"
    refs = [Ref(a, "Alpha", ["Ann Lee"], 2020), Ref(b, "Beta", ["Bo, Kim", "C D"], None)]
    out = sanitize(ast(para(cite(b), cite(a, b), cite("doc:" + str(uuid.uuid4())))), refs)
    text = "".join(i["c"] if i["t"] == "Str" else " " for i in out["blocks"][0]["c"])
    assert text == "[1][2, 1][?]"
    assert out["blocks"][-1]["c"][0]["c"] == "[2]" and out["blocks"][1]["t"] == "Header"
    assert out["blocks"][1]["c"][1][0] == "references"


def test_cite_author_year_and_crossref_text():
    a = f"doc:{uuid.uuid4()}"
    out = sanitize(
        ast(para(cite(a), S("/"), cite("fig:x"))),
        [Ref(a, "Alpha", ["Ann Lee", "Bo Kim", "C D"], 2020)],
        "author-year",
    )
    inl = out["blocks"][0]["c"]
    s = "".join(i["c"] if i["t"] == "Str" else " " for i in inl)
    assert s == "(Lee et al. 2020)/fig:x"


# ---- pandoc-backed ----
def html(md, refs=()):
    return build.to_html(md, list(refs), PROFILE, 30)


@needs_pandoc
def test_html_strips_raw_remote_and_orders_csp():
    md = (
        "---\nheader-includes: <script>alert(1)</script>\ntitle: 'A <b>x</b> & y'\n---\n\n"
        "# Intro {#sec:a onclick=alert(1) style=color:red}\n\n<script>alert(1)</script>\n\n"
        "![alt](http://evil/x.png)\n\n![](../../etc/passwd)\n\n[bad](javascript:alert(1)) <img src=http://x onerror=y>\n\n"
        '```{=html}\n<iframe src=x></iframe>\n```\n\n::: {style="x" onmouseover="y"}\nbox\n:::\n\n$x^2$\n'
    )
    h = html(md)
    low = h.lower()
    for bad in (
        "<script",
        "<link",
        "<style>@import",
        "<iframe",
        'src="http',
        'href="javascript',
        "javascript:",
        'onerror="',
        'onclick="',
        'onmouseover="',
        'style="x',
        "color:red",
        "<img",
        "<body style",
        "<div style",
    ):
        assert bad not in low, bad
    head = h[h.index("<head") :]
    assert head.split(">", 1)[1].startswith('<meta http-equiv="Content-Security-Policy"')
    assert "<math" in h and "<b>x</b>" not in h and "&lt;b&gt;x" in h


@needs_pandoc
def test_data_image_kept_and_cites_rendered():
    d = f"doc:{uuid.uuid4()}"
    uri = "data:image/png;base64,iVBORw0KGgo="
    h = html(f"See [@{d}] and @fig:x.\n\n![cap]({uri}){{#fig:x}}\n", [Ref(d, "Alpha", ["Ann Lee"], 2020)])
    assert uri in h and "[1]" in h and "Alpha" in h and "fig:x" in h


@needs_pandoc
def test_pandoc_timeout_and_failure():
    with pytest.raises(build.BuildError) as e:
        build.run_pandoc(["-f", "markdown", "-t", "json"], b"x", 0.0001)
    assert e.value.status == 503
    with pytest.raises(build.BuildError) as e:
        build.run_pandoc(["-f", "nope"], b"x", 10)
    assert e.value.status == 422


# ---- Gotenberg (MockTransport) ----
class Cfg:
    draft_gotenberg_url = "http://g:3000"
    build_timeout_s = 5.0
    build_max_pdf_bytes = 100


def pdf(handler, **kw):
    b = build.PandocBuilder(Cfg(), httpx.MockTransport(handler))
    return b._pdf("<html></html>")


def test_gotenberg_ok_and_request_shape():
    seen = {}

    def h(r):
        seen["u"] = str(r.url)
        seen["body"] = r.content
        return httpx.Response(200, content=b"%PDF-1.7 x")

    assert pdf(h) == b"%PDF-1.7 x"
    assert seen["u"] == "http://g:3000/forms/chromium/convert/html" and b"index.html" in seen["body"]


@pytest.mark.parametrize(
    ("resp", "status"),
    [
        (httpx.Response(200, content=b"%PDF-" + b"x" * 200), 422),
        (httpx.Response(200, content=b"<html>"), 503),
        (httpx.Response(400, content=b"no"), 422),
        (httpx.Response(502, content=b"no"), 503),
    ],
)
def test_gotenberg_errors(resp, status):
    with pytest.raises(build.BuildError) as e:
        pdf(lambda r: resp)
    assert e.value.status == status


def test_gotenberg_timeout_and_down():
    def t(r):
        raise httpx.ReadTimeout("slow")

    def c(r):
        raise httpx.ConnectError("down")

    for h in (t, c):
        with pytest.raises(build.BuildError) as e:
            pdf(h)
        assert e.value.status == 503


def test_unconfigured_and_latex_disabled():
    class C(Cfg):
        draft_gotenberg_url = ""

    with pytest.raises(build.BuildError) as e:
        build.PandocBuilder(C()).build("x", [], PROFILE)
    assert (e.value.status, e.value.message) == (503, "build service not configured")
    with pytest.raises(build.BuildError) as e:
        build.get_builder(Cfg(), "latex").build("x", [], PROFILE)
    assert e.value.status == 501


# ---- route ----
@pytest.fixture
def gotenberg(client, settings, monkeypatch):
    s = settings.model_copy(update={"draft_gotenberg_url": "http://g:3000"})
    client.app.dependency_overrides[get_app_settings] = lambda: s
    seen = []

    def h(r):
        seen.append(r)
        return httpx.Response(200, content=b"%PDF-1.7 route")

    monkeypatch.setattr(build, "_transport", httpx.MockTransport(h))
    yield seen
    client.app.dependency_overrides.pop(get_app_settings, None)


def mk(client, h, body):
    r = client.post("/drafts", headers=h, json={"title": "T", "content": body})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_route_latex_501_and_unconfigured_503(client, as_ana, ana):
    i = mk(client, as_ana, "---\nformat: latex\n---\n# A\n")
    assert client.post(f"/drafts/{i}/build", headers=as_ana).status_code == 501
    j = mk(client, as_ana, "# A\n")
    r = client.post(f"/drafts/{j}/build", headers=as_ana)
    assert r.status_code == 503 and r.json()["detail"] == "build service not configured"


def test_route_stranger_404_admin_403(client, as_ana, as_bob, ana, bob, make_token):
    from conftest import bearer

    i = mk(client, as_ana, "# A\n")
    assert client.post(f"/drafts/{i}/build", headers=as_bob).status_code == 404
    adm = bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))
    assert client.post(f"/drafts/{i}/build", headers=adm).status_code == 403
    assert client.post(f"/drafts/{uuid.uuid4()}/build", headers=as_bob).status_code == 404


@needs_pandoc
def test_route_builds_with_acl_refs_and_coauthor(client, as_ana, as_bob, ana, bob, db_session, gotenberg):
    vis, _ = seed(db_session, ana, title="Visible Paper", visibility="everyone", authors=("Ann Lee",))
    sec, _ = seed(db_session, bob, title="SECRET TITLE", visibility="private")
    db_session.commit()
    i = mk(client, as_ana, f"# A\n\nClaim [@doc:{vis.id}; @doc:{sec.id}].\n")
    r = client.post(f"/drafts/{i}/build", headers=as_ana)
    assert (
        r.status_code == 200
        and r.headers["content-type"] == "application/pdf"
        and r.content.startswith(b"%PDF-")
    )
    body = gotenberg[0].content.decode()
    assert "Visible Paper" in body and "SECRET TITLE" not in body and "[1, ?]" in body
    assert body.index("Content-Security-Policy") < body.index("<body")
