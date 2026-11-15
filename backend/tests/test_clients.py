import pytest

from roracle import clients
from roracle.config import Settings

DEFAULTS = {
    "elastic_url": "http://localhost:9200",
    "llm_base_url": "http://localhost:11434/v1",
    "llm_api_key": "unused",
    "llm_timeout_s": 120.0,
    "embed_model": "embeddinggemma",
    "embed_dims": 768,
    "embed_tokenizer": "sentence-transformers/all-MiniLM-L6-v2",
    "summary_model": "qwen2.5:3b",
    "docling_url": "http://localhost:5001",
    "grobid_url": "",
    "clamav_host": "",
    "near_dup_threshold": 0.85,
}


def test_clients_use_settings(settings):
    s = settings.model_copy(
        update={
            "elastic_url": "http://es.test:9200",
            "llm_base_url": "http://llm.test/v1",
            "llm_api_key": "k",
        }
    )
    llm = clients.llm(s)
    assert str(llm.base_url).rstrip("/") == "http://llm.test/v1"
    assert llm.api_key == "k"
    assert (llm.timeout, llm.max_retries) == (s.llm_timeout_s, 0)
    es = clients.es(s)
    node = es.transport.node_pool.all()[0]
    assert (node.host, node.port) == ("es.test", 9200)
    assert es._request_timeout == 30


@pytest.mark.parametrize("name,default", DEFAULTS.items())
def test_ingest_setting_defaults(name, default):
    assert Settings.model_fields[name].default == default
