"""Factories for external service clients (no network I/O at construction)."""

from elasticsearch import Elasticsearch
from openai import OpenAI

from roracle.config import Settings


def es(settings: Settings) -> Elasticsearch:
    kw = {}
    if settings.elastic_username:
        kw["basic_auth"] = (settings.elastic_username, settings.elastic_password)
    if settings.elastic_ca_certs:
        kw["ca_certs"] = settings.elastic_ca_certs
    return Elasticsearch(settings.elastic_url, request_timeout=30, **kw)


def llm(settings: Settings) -> OpenAI:
    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_s,
        max_retries=0,
    )
