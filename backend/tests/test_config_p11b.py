import boto3
import pytest
from sqlalchemy.engine import make_url

from roracle import clients, storage
from roracle.config import Settings


def mk(settings, **kw):
    return Settings(**{**settings.model_dump(), **kw})


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u:p@h:5432/d",
        "postgres://u:p@h:5432/d",
        "postgresql+psycopg://u:p@h:5432/d",
    ],
)
def test_database_url_normalised(settings, raw):
    assert mk(settings, database_url=raw).database_url == "postgresql+psycopg://u:p@h:5432/d"


def test_database_password_injected_with_special_chars(settings):
    s = mk(settings, database_url="postgres://u@h/d", database_password="p@ss:/")
    u = make_url(s.database_url)
    assert u.drivername == "postgresql+psycopg" and u.password == "p@ss:/" and u.username == "u"


def test_no_database_password_leaves_url(settings):
    assert mk(settings, database_url="postgresql+psycopg://u:p@h/d").database_url.endswith("u:p@h/d")


def test_es_client_kwargs(settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(clients, "Elasticsearch", lambda *a, **kw: seen.update(kw))
    clients.es(mk(settings, elastic_username="u", elastic_password="p", elastic_ca_certs="/ca.pem"))
    assert seen["basic_auth"] == ("u", "p") and seen["ca_certs"] == "/ca.pem"
    seen.clear()
    clients.es(mk(settings, elastic_username="", elastic_ca_certs=""))
    assert "basic_auth" not in seen and "ca_certs" not in seen


def test_s3_verify(settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: seen.update(kw))
    storage.blob_store_from(mk(settings, s3_ca_bundle="/b.pem"))
    assert seen["verify"] == "/b.pem"
    storage.blob_store_from(mk(settings, s3_ca_bundle=""))
    assert seen["verify"] is None


def test_clamav_port_default(settings):
    assert settings.clamav_port == 3310


def test_password_without_username_rejected(settings):
    with pytest.raises(ValueError):
        mk(settings, database_url="postgresql:///d?host=/run/postgresql", database_password="pw")


def test_validation_errors_hide_secret_inputs(settings):
    with pytest.raises(ValueError) as e:
        mk(settings, database_url="postgresql:///d?host=/x", database_password="Sup3rSecretPw")
    assert "Sup3rSecretPw" not in str(e.value)
    with pytest.raises(ValueError) as e:
        mk(settings, elastic_password="Sup3rSecretPw", connector_max_items="not-an-int")
    assert "Sup3rSecretPw" not in str(e.value) and "not-an-int" not in str(e.value)
