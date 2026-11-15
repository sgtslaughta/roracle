from roracle import clients
from roracle.config import Settings


def test_clients_use_settings(settings):
    s = settings.model_copy(
        update={
            "elastic_url": "http://es.test:9200",
            "llm_base_url": "http://llm.test/v1",
            "llm_api_key": "k",
        }
    )
    assert str(clients.llm(s).base_url).rstrip("/") == "http://llm.test/v1"
    assert clients.llm(s).api_key == "k"
    node = clients.es(s).transport.node_pool.all()[0]
    assert (node.host, node.port) == ("es.test", 9200)


def test_ingest_setting_defaults(settings):
    assert isinstance(settings, Settings)
    assert settings.llm_base_url == "http://localhost:11434/v1"
    assert (settings.embed_model, settings.embed_dims) == ("embeddinggemma", 768)
    assert settings.near_dup_threshold == 0.85
    assert settings.grobid_url == "" and settings.clamav_host == ""
