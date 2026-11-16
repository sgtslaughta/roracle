import pytest
from conftest import bearer
from fastapi import HTTPException
from fastapi.testclient import TestClient

from roracle import ratelimit
from roracle.auth import Principal, get_app_settings
from roracle.ratelimit import TokenBucket, check


@pytest.fixture(autouse=True)
def _fresh():
    ratelimit.LIMITER._b.clear()
    yield
    ratelimit.LIMITER._b.clear()


def limited(app, settings, n):
    s = settings.model_copy(update={"rate_limit_per_minute": n})
    app.dependency_overrides[get_app_settings] = lambda: s


def test_bucket_allows_n_then_retry_after_then_refills():
    b = TokenBucket()
    assert [b.take("k", 0.0, 3) for _ in range(3)] == [None] * 3
    wait = b.take("k", 0.0, 3)
    assert wait == pytest.approx(20.0)  # 3/min -> one token per 20 s
    assert b.take("k", 19.0, 3) is not None
    assert b.take("k", 20.0, 3) is None


def test_bucket_zero_disables_and_keys_are_independent():
    b = TokenBucket()
    assert all(b.take("k", 0.0, 0) is None for _ in range(100))
    assert b.take("a", 0.0, 1) is None and b.take("a", 0.0, 1) is not None
    assert b.take("b", 0.0, 1) is None


def test_idle_buckets_are_evicted():
    b = TokenBucket()
    for i in range(50):
        b.take(f"k{i}", 0.0, 5)
    b.take("late", ratelimit.IDLE_S + ratelimit.SWEEP_EVERY_S, 5)
    assert len(b) == 1


def test_check_raises_429_with_retry_after_header():
    p = Principal(user_id="u1", sub="s", username="u", groups=(), is_admin=False)  # type: ignore[arg-type]
    check(p, 1, now=0.0)
    with pytest.raises(HTTPException) as e:
        check(p, 1, now=0.0)
    assert e.value.status_code == 429 and e.value.headers == {"Retry-After": "60"}


def test_endpoint_429_after_limit(app, client, settings, as_ana):
    limited(app, settings, 2)
    assert [client.get("/me", headers=as_ana).status_code for _ in range(3)] == [200, 200, 429]
    r = client.get("/me", headers=as_ana)
    assert r.json() == {"detail": "rate limited"} and int(r.headers["retry-after"]) >= 1


def test_limit_zero_is_off(app, client, settings, as_ana):
    limited(app, settings, 0)
    assert all(client.get("/me", headers=as_ana).status_code == 200 for _ in range(10))


def test_pat_and_jwt_same_user_have_separate_buckets(app, client, settings, as_ana):
    tok = bearer(client.post("/me/tokens", json={"name": "cli"}, headers=as_ana).json()["token"])
    limited(app, settings, 1)
    assert client.get("/me", headers=as_ana).status_code == 200
    assert client.get("/me", headers=as_ana).status_code == 429
    assert client.get("/me", headers=tok).status_code == 200
    assert client.get("/me", headers=tok).status_code == 429


def test_health_metrics_never_limited(app, client, settings):
    limited(app, settings, 1)
    for path in ("/healthz", "/readyz", "/metrics"):
        codes = {client.get(path).status_code for _ in range(5)}
        assert 429 not in codes, path


def test_unauthenticated_is_401_not_counted(app, client, settings, as_ana):
    limited(app, settings, 1)
    assert [client.get("/me").status_code for _ in range(3)] == [401] * 3
    assert client.get("/me", headers=as_ana).status_code == 200


def test_mcp_bearer_path_is_limited(app, settings, as_ana):
    limited(app, settings, 1)
    with TestClient(app) as mc:
        h = {**as_ana, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        body = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        assert mc.post("/mcp", headers=h, content=body).status_code == 200
        r = mc.post("/mcp", headers=h, content=body)
        assert r.status_code == 429 and r.json() == {"detail": "rate limited"}
        assert int(r.headers["retry-after"]) >= 1 and "www-authenticate" not in r.headers


def test_runtime_setting_drives_the_limit(client, as_ana, make_token):
    adm = bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))
    r = client.put("/admin/settings", json={"rate_limit_per_minute": 2}, headers=adm)
    assert r.status_code == 200, r.text
    ratelimit.LIMITER._b.clear()
    assert [client.get("/me", headers=as_ana).status_code for _ in range(3)] == [200, 200, 429]


@pytest.mark.usefixtures("_blob_store_override")
def test_upload_429_before_any_tmp_object(app, client, settings, as_ana, s3):
    limited(app, settings, 1)
    assert client.get("/me", headers=as_ana).status_code == 200
    r = client.post("/documents", headers=as_ana, files={"file": ("a.pdf", b"%PDF-1", "application/pdf")})
    assert r.status_code == 429
    assert not [k for k in s3.keys() if k.startswith("tmp/")]  # noqa: SIM118
