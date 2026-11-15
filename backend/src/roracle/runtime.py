"""Runtime settings overrides: DB rows (`app_settings`) win over env. Imports only config/models."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.config import Settings
from roracle.models import AppSetting

MiB = 1024 * 1024
Name = Annotated[str, Field(min_length=1, max_length=200)]


class SettingsIn(BaseModel):
    """PUT body: every field optional; explicit null deletes the override."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chat_models: Annotated[list[Name], Field(min_length=1, max_length=20)] | None = None
    chat_default_model: Name | None = None
    chat_passages: Annotated[StrictInt, Field(ge=4, le=12)] | None = None
    rerank_depth: Annotated[StrictInt, Field(ge=10, le=200)] | None = None
    near_dup_threshold: Annotated[StrictInt | StrictFloat, Field(ge=0.5, le=1)] | None = None
    near_dup_summary_threshold: Annotated[StrictInt | StrictFloat, Field(ge=0.5, le=1)] | None = None
    max_upload_bytes: Annotated[StrictInt, Field(ge=MiB, le=5 * 1024 * MiB)] | None = None
    upload_quota_bytes: Annotated[StrictInt, Field(ge=0)] | None = None
    rate_limit_per_minute: Annotated[StrictInt, Field(ge=0)] | None = None
    pats_enabled: StrictBool | None = None

    @field_validator("chat_models")
    @classmethod
    def _no_commas(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any("," in m for m in v):
            raise ValueError("model names cannot contain commas")
        return v


KEYS = tuple(SettingsIn.model_fields)


def overrides(session: Session) -> dict[str, Any]:
    return {r.key: r.value for r in session.scalars(select(AppSetting)) if r.key in KEYS}


def apply(base: Settings, ov: dict[str, Any]) -> Settings:
    upd = {k: v for k, v in ov.items() if k not in ("chat_models", "chat_default_model")}
    models = list(ov.get("chat_models") or base.chat_model_list)
    if (d := ov.get("chat_default_model")) in models:
        models.insert(0, models.pop(models.index(d)))
    if "chat_models" in ov or "chat_default_model" in ov:
        upd["chat_models"] = ",".join(models)
    return base.model_copy(update=upd)


def effective(session: Session, base: Settings) -> Settings:
    return apply(base, overrides(session))


def view(s: Settings) -> dict[str, Any]:
    """Effective values per key (chat_models as a list; default = first model)."""
    out = {k: getattr(s, k) for k in KEYS if k not in ("chat_models", "chat_default_model")}
    out["chat_models"] = s.chat_model_list
    out["chat_default_model"] = s.chat_model_list[0]
    return out
