import ipaddress
import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# IPv6 prefixes that wrap or translate an IPv4 address (mapped, compat, NAT64, 6to4, Teredo).
WRAPPERS = [
    ipaddress.ip_network(n)
    for n in (
        "::/96",
        "::ffff:0:0/96",
        "::ffff:0:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2002::/16",
        "2001::/32",
    )
]
# 6to4/Teredo embed their IPv4 outside the low 32 bits: never allowlistable, whatever the prefix length.
UNALLOWABLE = [ipaddress.ip_network("2002::/16"), ipaddress.ip_network("2001::/32")]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROR_", hide_input_in_errors=True)

    database_url: str
    database_password: str = ""  # injected into database_url when set (keeps the secret out of the URL)
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
    draft_max_bytes: int = 2 * 1024 * 1024  # P9 draft Markdown cap (UTF-8)
    mcp_max_upload_bytes: int = 25 * 1024 * 1024  # MCP upload tool: decoded file cap (base64 body)
    public_base_url: str = ""  # externally visible origin, e.g. https://roracle.example.org
    pats_enabled: bool = True
    pat_max_identity_age_days: int = 30
    elastic_url: str = "http://localhost:9200"
    elastic_username: str = ""
    elastic_password: str = ""
    elastic_ca_certs: str = ""  # path to a PEM CA bundle for ES TLS
    s3_ca_bundle: str = ""  # path to a PEM CA bundle for S3 TLS
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
    clamav_port: int = 3310
    rerank_url: str = ""
    rerank_model: str = ""
    rerank_api: Literal["cohere", "tei"] = (
        "cohere"  # tei: HF text-embeddings-inference {query, texts} -> [{index, score}]
    )
    rerank_api_key: str = ""  # optional; sent as `Authorization: Bearer` (never logged)
    rerank_timeout_s: float = 3.0  # exceeded -> fused order (RERANK_FALLBACK{timeout})
    rerank_max_chars: int = 2000  # per passage sent to the reranker
    rerank_max_batch: int = Field(256, ge=1)  # texts per rerank request (<= server --max-client-batch-size)
    chat_candidates: int = Field(200, ge=20, le=500)  # BM25 and kNN list size on the chat path
    near_dup_threshold: float = 0.85
    near_dup_summary_threshold: float = 0.97
    chat_min_similarity: float = 0.35  # kNN floor for chat retrieval; 0 disables
    search_min_similarity: float = (
        0.35  # kNN cosine floor for library search; 0 disables (relevant ~0.4-0.75, unrelated <=0.3)
    )
    chat_models: str = "qwen2.5:14b"  # comma-separated; first is the default
    writing_max_citations: int = 200  # distinct citation keys a draft may use
    writing_validate_budget_s: float = 10  # deterministic checks wall-clock budget (fail closed)
    draft_gotenberg_url: str = ""  # separate Chromium-enabled Gotenberg for draft PDF builds; empty = 503
    build_timeout_s: float = 120.0  # per pandoc call and for the Gotenberg request
    build_concurrency: int = 2  # concurrent draft builds per api process; more = 503 busy
    build_max_pdf_bytes: int = 100 * 1024 * 1024
    latex_builder: Literal["disabled"] = "disabled"  # `disabled` -> format: latex builds return 501
    writing_model: str = ""  # P9 style check / citation judge; empty = first chat model
    chat_passages: int = 8  # clamped 4-12 at use
    chat_note_min_upvotes: int = 3  # community notes reach the LLM only above these (own notes always)
    chat_note_min_ratio: float = 0.6
    rerank_depth: int = 200
    eval_judge_model: str = ""  # P13 LLM judge; empty = first chat model
    eval_judge_sample: int = 100  # (query x principal) pairs judged per run
    eval_on_reindex: bool = True  # enqueue `eval_run` on the reference set after a re-embed cutover
    context_notes: bool = False  # P13 T5: LLM situating note per chunk (flips on a PASS in docs/eval/)
    context_model: str = ""  # empty = notes off (needs an Ollama variant with num_ctx >= 16384)
    context_doc_chars: int = 24000  # document window sent in the stable prefix
    context_max_chunks: int = 400  # per document; later chunks get the heading path (bounds one job)
    context_max_tokens: int = 120  # hard cap per note
    raptor: bool = (
        False  # P13 T6: per-document section/document summary units (flips on a PASS in docs/eval/)
    )
    raptor_model: str = ""  # empty = summary_model
    raptor_section_chars: int = 12000  # text per summary call (longer: windows, then summary of summaries)
    raptor_max_sections: int = Field(64, ge=1)  # section units per document (adjacent sections are folded)
    router: bool = False  # P13 T7: query router + global map-reduce (flips on a PASS in docs/eval/)
    router_llm: bool = (
        False  # ambiguous questions: one small-model call; off = heuristic only (no hot-path LLM)
    )
    router_model: str = ""  # empty = summary_model
    router_timeout_s: float = 1.5
    global_max_docs: int = 40
    global_concurrency: int = 8
    global_timeout_s: float = 90.0
    global_doc_chars: int = 6000  # one document's own text sent per map call
    global_map_tokens: int = 200  # hard cap per map note
    global_reduce_tokens: int = 1200  # hard cap on the streamed reduce answer
    upload_quota_bytes: int = 0  # per user, sum of live version sizes; 0 = unlimited
    db_connect_timeout_s: int = (
        5  # PG connect timeout (api/worker/readyz); a ?connect_timeout= in the URL wins
    )
    rate_limit_per_minute: int = 0  # per-token, per replica; 0 = off (ratelimit.py)
    connector_fs_roots: Annotated[list[str], NoDecode] = []  # fs connectors may only read under these
    connector_max_items: int = 1000  # per run
    connector_max_run_s: int = 3600
    connector_tick_s: int = 60
    connector_http_allow_cidrs: Annotated[
        list[str], NoDecode
    ] = []  # http crawl may reach these private ranges
    connector_http_timeout_s: int = 60  # per HTTP request (connect to last byte)
    worker_health_port: int = 8001  # worker /healthz + /metrics; 0 = off
    retention_deleted_days: int = 0  # purge soft-deleted documents after N days; 0 = never
    maintenance_interval_s: int = 86400  # periodic purge + blob gc + tag reconcile
    read_alias: str = "chunks"  # exclusive read alias re-embed swaps; only tests override it
    embed_index: str = ""  # active ES index; set only by re-embed cutover (runtime override), "" = derived

    @model_validator(mode="after")
    def _database_url(self):
        try:
            u = make_url(self.database_url)
        except ArgumentError:  # not a URL (e.g. a placeholder): leave for the engine to reject
            return self
        if u.drivername in ("postgresql", "postgres"):
            u = u.set(drivername="postgresql+psycopg")
        if self.database_password:
            if not u.username:
                raise ValueError("database_password requires a username in database_url")
            u = u.set(password=self.database_password)
        self.database_url = u.render_as_string(hide_password=False)
        return self

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
            net = ipaddress.ip_network(c, strict=False)  # ValueError -> settings validation error
            if net.version == 6 and any(net.overlaps(u) for u in UNALLOWABLE):
                raise ValueError(f"{c}: 6to4 (2002::/16) and Teredo (2001::/32) ranges cannot be allowlisted")
            if net.version == 6 and net.prefixlen != 96 and any(net.overlaps(w) for w in WRAPPERS):
                raise ValueError(
                    f"{c}: an IPv6 allowlist CIDR overlapping a mapped/translator/tunnel range "
                    "(::/96, ::ffff:0:0/96, 64:ff9b::/96, 64:ff9b:1::/48, 2002::/16, 2001::/32) must be a /96 translator prefix"
                )
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
