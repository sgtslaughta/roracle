from conftest import REPO_ROOT


def test_healthz_reports_ok_and_repo_version(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": (REPO_ROOT / "VERSION").read_text().strip()}
