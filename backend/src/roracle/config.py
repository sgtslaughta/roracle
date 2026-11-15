import ipaddress
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
    evidence_max_bytes: int = 1073741824  # notes evidence file cap (1 GiB)
    mcp_max_upload_bytes: int = 25 * 1024 * 1024  # MCP upload tool: decoded file cap (base64 body)
    public_base_url: str = ""  # externally visible origin, e.g. https://roracle.example.org
    pats_enabled: bool = True
    pat_max_identity_age_days: int = 30
    elastic_url: str = "http://localhost:9200"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "unused"
    llm_timeout_s: float = 120.0
    embed_model: str = "embeddinggemma"
    embed_dims: int = 768
    embed_tokenizer: str = "sentence-transformers/all-MiniLM-L6-v2"
    summary_model: str = "qwen2.5:3b"
    docling_url: str = "http://localhost:5001"
    docling_timeout_s: float = 300.0
    grobid_timeout_s: float = 120.0
    grobid_url: str = ""
    gotenberg_url: str = ""
    gotenberg_timeout_s: float = 300.0
    max_rendition_bytes: int = 524288000
    clamav_host: str = ""
    rerank_url: str = ""
    rerank_model: str = ""
    near_dup_threshold: float = 0.85
    near_dup_summary_threshold: float = 0.97
    chat_min_similarity: float = 0.35  # kNN floor for chat retrieval; 0 disables
    chat_models: str = "qwen2.5:14b"  # comma-separated; first is the default
    chat_passages: int = 8  # clamped 4-12 at use
    chat_note_min_upvotes: int = 3  # community notes reach the LLM only above these (own notes always)
    chat_note_min_ratio: float = 0.6
    rerank_depth: int = 50
    upload_quota_bytes: int = 0  # per user, sum of live version sizes; 0 = unlimited
    rate_limit_per_minute: int = 0  # stored only; enforced at ingress in P11
    connector_fs_roots: Annotated[list[str], NoDecode] = []  # fs connectors may only read under these
    connector_max_items: int = 1000  # per run
    connector_max_run_s: int = 3600
    connector_tick_s: int = 60
    connector_http_allow_cidrs: Annotated[
        list[str], NoDecode
    ] = []  # http crawl may reach these private ranges
    connector_http_timeout_s: int = 60  # per HTTP request (connect to last byte)

    @property
    def chat_model_list(self) -> list[str]:
        return [m.strip() for m in self.chat_models.split(",") if m.strip()]

    @field_validator("chat_models")
    @classmethod
    def _models_nonempty(cls, v: str) -> str:
        if not [m for m in v.split(",") if m.strip()]:
            raise ValueError("chat_models must list at least one model")
        return v

    @field_validator("connector_http_allow_cidrs")
    @classmethod
    def _cidrs(cls, v: list[str]) -> list[str]:
        for c in v:
            ipaddress.ip_network(c, strict=False)  # ValueError -> settings validation error
        return v

    @field_validator(
        "oidc_allowed_clients", "connector_fs_roots", "connector_http_allow_cidrs", mode="before"
    )
    @classmethod
    def _parse_clients(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v.startswith("[") else [c.strip() for c in v.split(",") if c.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
