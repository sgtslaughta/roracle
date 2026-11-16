import pytest
from conftest import bearer
from sqlalchemy import select

from roracle.models import AuditLog
from roracle.writing import profiles

URL = "/admin/writing-profiles"
MIN = "name: {n}\ntitle: T\ndoc_types: [{dt}]\norg_units: [{ou}]\n"


def _yaml(n="custom", dt="journal-article", ou="/"):
    return MIN.format(n=n, dt=dt, ou=ou)


def test_all_shipped_parse():
    b = profiles.builtin()
    assert set(b) == {
        "dissertation",
        "journal-article",
        "conference-paper",
        "technical-report",
        "grant-proposal",
        "literature-review",
        "preregistration",
    }
    for p in b.values():
        assert p.template.startswith("---") and "ai_assistance" in p.template and p.required_sections
    assert b["dissertation"].review_required and not b["journal-article"].review_required
    assert b["journal-article"].rules["structure"] == "blocking"
    assert b["journal-article"].rules["acronyms"] == "warning"
    assert b["journal-article"].rules["style"] == "warning"


@pytest.mark.parametrize(
    "text",
    [
        "a: [",
        "- 1",
        "name: x\ntitle: T\nbogus: 1",
        "name: Bad Name\ntitle: T",
        "name: x\ntitle: T\nrules: {nope: blocking}",
        "name: x\ntitle: T\nrules: {style: loud}",
        "name: x\ntitle: T\nlimits: {words_max: 0}",
        "x: " + "a" * 70000,
    ],
)
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        profiles.parse(text)


def test_rules_override_merges_defaults():
    p = profiles.parse("name: x\ntitle: T\nrules: {style: blocking, citations: off}")
    assert (p.rules["style"], p.rules["citations"], p.rules["structure"]) == ("blocking", "off", "blocking")


def test_admin_crud(client, adm, db_session):
    r = client.get(URL, headers=adm).json()["items"]
    assert {
        "name": "journal-article",
        "title": "Journal article",
        "version": 0,
        "source": "builtin",
        "active": True,
    } in r
    y = _yaml("journal-article")
    assert client.post(URL, headers=adm, json={"yaml": y}).json() == {"name": "journal-article", "version": 1}
    assert client.post(URL, headers=adm, json={"yaml": y}).json()["version"] == 2
    got = client.get(f"{URL}/journal-article", headers=adm).json()
    assert [v["version"] for v in got["versions"]] == [2, 1, 0]
    ja = next(i for i in client.get(URL, headers=adm).json()["items"] if i["name"] == "journal-article")
    assert (ja["version"], ja["source"]) == (2, "db")
    assert client.delete(f"{URL}/journal-article", headers=adm).status_code == 200
    ja = next(i for i in client.get(URL, headers=adm).json()["items"] if i["name"] == "journal-article")
    assert (ja["version"], ja["source"]) == (0, "builtin")
    assert client.delete(f"{URL}/journal-article", headers=adm).status_code == 404
    assert client.post(URL, headers=adm, json={"yaml": y}).json()["version"] == 3
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "admin.writing_profile")).all()
    assert [r.detail for r in rows] == [
        {"name": "journal-article", "version": 1},
        {"name": "journal-article", "version": 2},
        {"name": "journal-article", "deactivated": True},
        {"name": "journal-article", "version": 3},
    ]


def test_new_name_and_unknown(client, adm):
    assert client.post(URL, headers=adm, json={"yaml": _yaml("lab-note")}).json()["version"] == 1
    assert client.get(f"{URL}/lab-note", headers=adm).status_code == 200
    assert client.get(f"{URL}/nope", headers=adm).status_code == 404
    assert client.delete(f"{URL}/preregistration", headers=adm).status_code == 404


@pytest.mark.parametrize("y", ["a: [", "name: x\ntitle: T\nbogus: 1", "x: " + "a" * 70000])
def test_post_invalid_422(client, adm, y):
    assert client.post(URL, headers=adm, json={"yaml": y}).status_code == 422


def test_forbidden(client, as_ana, adm):
    assert client.get(URL, headers=as_ana).status_code == 403
    assert client.post(URL, headers=as_ana, json={"yaml": _yaml()}).status_code == 403
    pat = client.post("/me/tokens", headers=adm, json={"name": "t", "scopes": ["read"]}).json()
    assert client.get(URL, headers=bearer(pat["token"])).status_code == 403


def test_resolve(db_session, client, adm):
    s = db_session
    assert profiles.resolve(s, None, "/", None)[2:] == ("builtin",)
    assert profiles.resolve(s, None, "/", None)[0].name == "journal-article"
    client.post(URL, headers=adm, json={"yaml": _yaml("physics-note", "lab-note", "/sci")})
    s.expire_all()
    p, v, src = profiles.resolve(s, None, "/sci/physics", "lab-note")
    assert (p.name, v, src) == ("physics-note", 1, "db")
    assert profiles.resolve(s, None, "/hum", "lab-note")[0].name == "journal-article"
    assert profiles.resolve(s, None, "/sci", "other")[0].name == "journal-article"
    assert profiles.resolve(s, "dissertation", "/", None)[0].review_required
    with pytest.raises(KeyError):
        profiles.resolve(s, "nope", "/", None)
