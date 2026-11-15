"""Factories for external service clients (no network I/O at construction)."""

from elasticsearch import Elasticsearch
from openai import OpenAI

from roracle.config import Settings


def es(settings: Settings) -> Elasticsearch:
    return Elasticsearch(settings.elastic_url)


def llm(settings: Settings) -> OpenAI:
    return OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
