"""Writing profiles: shipped YAML + admin-managed DB overrides (versioned)."""

from functools import cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.access import normalize_path, org_ancestors
from roracle.models_writing import WritingProfile

MAX_YAML_BYTES = 64 * 1024
DEFAULT_PROFILE = "journal-article"
RuleName = Literal[
    "structure",
    "limits",
    "metadata",
    "notation",
    "acronyms",
    "crossrefs",
    "alt_text",
    "si_units",
    "ai_disclosure",
    "citations",
    "style",
]
Severity = Literal["blocking", "warning", "off"]
_SOFT = {"notation": "warning", "acronyms": "warning", "si_units": "warning", "style": "warning"}
RULE_DEFAULTS: dict[str, str] = {r: _SOFT.get(r, "blocking") for r in RuleName.__args__}  # type: ignore[attr-defined]


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    words_max: int | None = Field(None, ge=1)
    abstract_words_max: int | None = Field(None, ge=1)
    title_chars_max: int | None = Field(None, ge=1)
    figures_max: int | None = Field(None, ge=1)
    references_max: int | None = Field(None, ge=1)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9-]{1,60}$")
    title: str
    doc_types: list[str] = []
    org_units: list[str] = []
    template: str = ""
    required_sections: list[str] = []
    required_metadata: list[str] = []
    limits: Limits = Limits()
    citation_style: Literal["numeric", "author-year"] = "numeric"
    acronym_allowlist: list[str] = []
    style_guidance: str = ""
    review_required: bool = False
    rules: dict[RuleName, Severity] = Field(default={}, validate_default=True)

    @field_validator("org_units")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return [normalize_path(x) for x in v]

    @field_validator("rules", mode="before")
    @classmethod
    def _yaml_off(cls, v: object) -> object:
        # YAML 1.1 parses a bare `off` as False
        return {k: "off" if x is False else x for k, x in v.items()} if isinstance(v, dict) else v

    @field_validator("rules")
    @classmethod
    def _defaults(cls, v: dict) -> dict:
        return {**RULE_DEFAULTS, **v}


def parse(yaml_text: str) -> Profile:
    if len(yaml_text.encode()) > MAX_YAML_BYTES:
        raise ValueError("profile YAML too large (max 64 KiB)")
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {str(e).splitlines()[0]}") from e
    if not isinstance(data, dict):
        raise ValueError("profile must be a YAML mapping")  # noqa: TRY004
    try:
        return Profile.model_validate(data)
    except ValidationError as e:
        err = e.errors()[0]
        loc = ".".join(str(x) for x in err["loc"])
        raise ValueError(f"{loc}: {err['msg']}" if loc else err["msg"]) from e


@cache
def builtin() -> dict[str, Profile]:
    out: dict[str, Profile] = {}
    for f in sorted(resources.files(__package__).joinpath("profiles").iterdir(), key=lambda f: f.name):
        if f.name.endswith(".yaml"):
            p = parse(f.read_text(encoding="utf-8"))
            out[p.name] = p
    return out


def _latest_db(session: Session) -> dict[str, WritingProfile]:
    """Newest active version per name; names with no active row are absent."""
    rows = session.scalars(
        select(WritingProfile).where(WritingProfile.active).order_by(WritingProfile.version)
    ).all()
    return {r.name: r for r in rows}


def _db_result(r: WritingProfile) -> tuple[Profile, int, str]:
    return Profile.model_validate(r.spec), r.version, "db"


def named(
    session: Session, name: str, db: dict[str, WritingProfile] | None = None
) -> tuple[Profile, int, str]:
    db = _latest_db(session) if db is None else db
    if name in db:
        return _db_result(db[name])
    if name in builtin():
        return builtin()[name], 0, "builtin"
    raise KeyError(name)


def resolve(
    session: Session, name: str | None, org_unit: str, doc_type: str | None
) -> tuple[Profile, int, str]:
    """R4: explicit name; else newest matching DB profile; else journal-article."""
    db = _latest_db(session)
    if name:
        return named(session, name, db)
    ancestors = set(org_ancestors([org_unit] if org_unit else []))
    hits = [
        r
        for r in db.values()
        if doc_type in r.spec.get("doc_types", []) and ancestors & set(r.spec.get("org_units", []))
    ]
    if hits:
        return _db_result(max(hits, key=lambda r: r.id))
    return named(session, DEFAULT_PROFILE, db)


def list_effective(session: Session) -> list[dict]:
    db = _latest_db(session)
    out = []
    for n in sorted(set(builtin()) | set(db)):
        p, v, src = named(session, n, db)
        out.append({"name": n, "title": p.title, "version": v, "source": src, "active": True})
    return out


def yaml_text(session: Session, name: str) -> str:
    """Source YAML of the effective profile `name` (KeyError if unknown)."""
    db = _latest_db(session)
    if name in db:
        return db[name].yaml
    if name in builtin():
        return resources.files(__package__).joinpath("profiles", f"{name}.yaml").read_text(encoding="utf-8")
    raise KeyError(name)
