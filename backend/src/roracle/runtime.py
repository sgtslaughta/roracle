"""Runtime settings overrides: DB rows (`app_settings`) win over env. Imports only config/models."""

import logging
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.config import Settings
from roracle.models import AppSetting

log = logging.getLogger(__name__)
MiB = 1024 * 1024
MAXINT = 2**63 - 1
Name = Annotated[str, Field(min_length=1, max_length=200)]


class SettingsIn(BaseModel):
    """PUT body: every field optional; explicit null deletes the override."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    chat_models: Annotated[list[Name], Field(min_length=1, max_length=20)] | None = None
    chat_default_model: Name | None = None
    chat_passages: Annotated[StrictInt, Field(ge=4, le=12)] | None = None
    chat_note_min_upvotes: Annotated[StrictInt, Field(ge=0)] | None = None
    chat_note_min_ratio: Annotated[StrictInt | StrictFloat, Field(ge=0, le=1)] | None = None
    rerank_depth: Annotated[StrictInt, Field(ge=10, le=500)] | None = None
    near_dup_threshold: Annotated[StrictInt | StrictFloat, Field(ge=0.5, le=1)] | None = None
    near_dup_summary_threshold: Annotated[StrictInt | StrictFloat, Field(ge=0.5, le=1)] | None = None
    max_upload_bytes: Annotated[StrictInt, Field(ge=MiB, le=5 * 1024 * MiB)] | None = None
    upload_quota_bytes: Annotated[StrictInt, Field(ge=0, le=MAXINT)] | None = None
    rate_limit_per_minute: Annotated[StrictInt, Field(ge=0, le=MAXINT)] | None = None
    pats_enabled: StrictBool | None = None
    router: StrictBool | None = None

    @field_validator("chat_models")
    @classmethod
    def _no_commas(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any("," in m for m in v):
            raise ValueError("model names cannot contain commas")
        return None if v is None else list(dict.fromkeys(v))


KEYS = tuple(SettingsIn.model_fields)


def overrides(session: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for r in session.scalars(select(AppSetting)):
        if r.key not in KEYS:
            continue
        try:
            ok = SettingsIn.model_validate({r.key: r.value}).model_dump(exclude_none=True)
        except ValidationError:
            log.warning("ignoring invalid stored setting %s", r.key)
            continue
        out[r.key] = ok.get(r.key, r.value)
    return out


def apply(base: Settings, ov: dict[str, Any]) -> Settings:
    upd = {k: v for k, v in ov.items() if k not in ("chat_models", "chat_default_model")}
    models = list(ov.get("chat_models") or base.chat_model_list)
    if (d := ov.get("chat_default_model")) in models:
        models.insert(0, models.pop(models.index(d)))
    if "chat_models" in ov or "chat_default_model" in ov:
        upd["chat_models"] = ",".join(models)
    return base.model_copy(update=upd)


ACTIVE_EMBED_KEYS = ("embed_model", "embed_dims", "embed_index")  # set only by re-embed cutover (R4)


def active_embedding(session: Session) -> dict[str, Any]:
    rows = session.scalars(select(AppSetting).where(AppSetting.key.in_(ACTIVE_EMBED_KEYS)))
    return {r.key: r.value for r in rows}


def set_active_embedding(session: Session, model: str, dims: int, index: str) -> None:
    for k, v in zip(ACTIVE_EMBED_KEYS, (model, dims, index), strict=True):
        session.merge(AppSetting(key=k, value=v))
    session.flush()


def effective(session: Session, base: Settings) -> Settings:
    return apply(base, {**overrides(session), **active_embedding(session)})


def assert_embedding_unchanged(session: Session, settings: Settings) -> None:
    """Raise (retryable) if a re-embed cutover switched the model since `settings` was read."""
    now = effective(session, settings)
    if (now.embed_model, now.embed_dims, now.embed_index) != (
        settings.embed_model,
        settings.embed_dims,
        settings.embed_index,
    ):
        raise RuntimeError("embedding model changed during ingest; retrying with the new model")


def view(s: Settings) -> dict[str, Any]:
    """Effective values per key (chat_models as a list; default = first model)."""
    out = {k: getattr(s, k) for k in KEYS if k not in ("chat_models", "chat_default_model")}
    out["chat_models"] = s.chat_model_list
    out["chat_default_model"] = s.chat_model_list[0]
    return out
