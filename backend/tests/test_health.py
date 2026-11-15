import roracle


def test_healthz_reports_ok_and_package_version(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": roracle.__version__}
