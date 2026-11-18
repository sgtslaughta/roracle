# ruff: noqa: F811
"""Task 1: every /mcp caller is an agent (label gates, never admin); web JWTs are unchanged."""

import json
import pathlib
import re

import pytest
from api_seed import seed
from conftest import bearer
from mcp_helpers import call
from test_labels_mcp import label, mk
from test_mcp_tools import fes, llm, mc  # noqa: F401
from test_search_api import hit

import roracle

pytestmark = pytest.mark.usefixtures("_blob_store_override")
MISSING = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def test_mcp_jwt_blocked_doc_invisible(client, mc, as_ana, ana, admin, db_session, fes):
    sec = mk(client, admin, "NoAg", 3, block_agents=True)
    d, v = seed(db_session, ana, title="Hidden")
    label(db_session, d, sec)
    fes.hits = [hit(d, v, "SECRET")]
    assert call(mc, as_ana, "search", query="alpha")[1]["results"] == []
    err, text = call(mc, as_ana, "get_document", doc_id=str(d.id))
    err2, text2 = call(mc, as_ana, "get_document", doc_id=MISSING)
    assert err and err2 and text == text2


def test_mcp_jwt_exclude_llm_text_withheld(client, mc, as_ana, ana, admin, db_session, fes):
    nl = mk(client, admin, "NoLLM", 2, exclude_llm=True)
    d, v = seed(db_session, ana, title="T")
    label(db_session, d, nl)
    fes.hits = [hit(d, v, "SECRET")]
    out = call(mc, as_ana, "get_passages", chunk_ids=[f"{v.id}:0"])[1]["result"]
    assert "SECRET" not in json.dumps(out) and "NoLLM" in json.dumps(out)


def test_mcp_admin_jwt_is_not_admin(client, mc, admin, ana, db_session, fes):
    d, _ = seed(db_session, ana, title="Private", visibility="private")
    assert client.get(f"/documents/{d.id}", headers=admin).status_code == 200  # web admin sees it
    err, text = call(mc, admin, "get_document", doc_id=str(d.id))
    err2, text2 = call(mc, admin, "get_document", doc_id=MISSING)
    assert err and err2 and text == text2


def test_web_jwt_unchanged(client, as_ana, ana, admin, db_session):
    nl = mk(client, admin, "NoLLM", 2, exclude_llm=True)
    d, _ = seed(db_session, ana, title="T")
    label(db_session, d, nl)
    assert client.get(f"/documents/{d.id}", headers=as_ana).json()["summary"] == "sum"


def test_no_raw_pat_checks_left():
    src = pathlib.Path(roracle.__file__).parent
    allowed = {"ratelimit.py", "search_cache.py", "pats.py", "auth.py"}
    bad = [
        str(f)
        for f in src.rglob("*.py")
        if f.name not in allowed and re.search(r'\.via\s*[!=]=\s*"pat"', f.read_text())
    ]
    assert bad == []


# --- C1: OAuth tokens of an agent client (azp in oidc_agent_clients) are agents on REST too ---


@pytest.fixture
def as_agent_jwt(make_token):
    return bearer(
        make_token(sub="ana", username="ana", groups=("/Mathematics/Spectral Theory",), azp="roracle-mcp")
    )


@pytest.fixture
def agent_admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",), azp="roracle-mcp"))


def test_agent_client_jwt_search_hides_block_agents(
    client, as_agent_jwt, as_ana, ana, admin, db_session, fes
):
    sec = mk(client, admin, "NoAg", 3, block_agents=True)
    d, v = seed(db_session, ana, title="Hidden")
    label(db_session, d, sec)
    fes.hits = [hit(d, v, "SECRET")]
    assert client.get("/search", headers=as_agent_jwt, params={"q": "alpha"}).json()["results"] == []
    assert client.get(f"/documents/{d.id}", headers=as_agent_jwt).status_code == 404
    assert len(client.get("/search", headers=as_ana, params={"q": "alpha"}).json()["results"]) == 1


def test_agent_client_admin_jwt_is_not_admin(client, agent_admin, admin):
    assert client.get("/admin/audit", headers=agent_admin).status_code == 403
    assert client.get("/admin/audit", headers=admin).status_code == 200


def test_agent_client_jwt_cannot_mint_tokens(client, as_agent_jwt):
    r = client.post("/me/tokens", json={"name": "x", "scopes": ["manage"]}, headers=as_agent_jwt)
    assert r.status_code == 403


def test_agent_client_jwt_cannot_delete(client, as_agent_jwt, ana, db_session):
    d, _ = seed(db_session, ana, visibility="private")
    r = client.delete(f"/documents/{d.id}", headers=as_agent_jwt)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"
