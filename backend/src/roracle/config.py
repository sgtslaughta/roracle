import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROR_")

    database_url: str
    s3_endpoint: str
    s3_bucket: str = "roracle"
    s3_access_key: str
    s3_secret_key: str
    oidc_issuer: str
    oidc_audience: str = "roracle"
    oidc_jwks_url: str
    oidc_allowed_clients: Annotated[list[str], NoDecode] = ["roracle-web", "roracle-mcp"]
    max_upload_bytes: int = 524288000
    elastic_url: str = "http://localhost:9200"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "unused"
    embed_model: str = "embeddinggemma"
    embed_dims: int = 768
    embed_tokenizer: str = "sentence-transformers/all-MiniLM-L6-v2"
    summary_model: str = "qwen2.5:3b"
    docling_url: str = "http://localhost:5001"
    grobid_url: str = ""
    clamav_host: str = ""
    near_dup_threshold: float = 0.85

    @field_validator("oidc_allowed_clients", mode="before")
    @classmethod
    def _parse_clients(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v.startswith("[") else [c.strip() for c in v.split(",") if c.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
