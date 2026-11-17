"""Library-search request parameters (one model for REST and saved searches)."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from roracle.search_limits import LIST_MAX, Q_MAX, VALUE_MAX

Scope = Literal["anywhere", "title", "authors", "abstract", "fulltext"]
Sort = Literal[
    "relevance",
    "newest",
    "published_desc",
    "published_asc",
    "votes",
    "cited",
    "title",
    "first_author",
    "venue",
    "pages",
]
FACETS = ("tags", "author", "venue", "file_type", "org_unit", "label", "collection")
_LEGACY_SORTS = {"relevance", "newest", "votes", "title"}
_LEGACY_KEYS = {"q", "tags", "author", "file_type", "org_unit", "collection", "label", "sort", "page", "size"}
_LEGACY_KEYS |= {"after", "notes", "mode"}
_RANGES = (("year", 0, 3000), ("pages", 0, None), ("added", None, None))

Val = Annotated[str, StringConstraints(max_length=VALUE_MAX)]
Multi = Annotated[list[Val], Field(max_length=LIST_MAX)]
YesNo = Literal["yes", "no"] | None


def _uuids(vs: list[str]) -> list[str]:
    out = []
    for v in vs:
        try:
            out.append(str(UUID(v)))
        except ValueError:
            pass
    return out


class SearchParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    q: str = Field("", max_length=Q_MAX)
    in_: Scope = Field("anywhere", alias="in")
    tags: Multi = []
    tags_not: Multi = []
    author: Multi = []
    author_not: Multi = []
    venue: Multi = []
    venue_not: Multi = []
    file_type: Multi = []
    file_type_not: Multi = []
    org_unit: Multi = []
    org_unit_not: Multi = []
    label: Multi = []
    label_not: Multi = []
    collection: Multi = []
    collection_not: Multi = []
    tags_op: Literal["or", "and"] = "or"
    author_op: Literal["or", "and"] = "or"
    year_min: int | None = Field(None, ge=0, le=3000)
    year_max: int | None = Field(None, ge=0, le=3000)
    pages_min: int | None = Field(None, ge=0)
    pages_max: int | None = Field(None, ge=0)
    votes_min: int | None = None
    cited_min: int | None = Field(None, ge=1)
    added_min: date | None = None
    added_max: date | None = None
    has_doi: YesNo = None
    has_abstract: YesNo = None
    has_refs: YesNo = None
    owner: Literal["mine", "shared"] | None = None
    cites: UUID | None = None
    sort: Sort = "relevance"
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=50)
    after: str | None = Field(None, max_length=100)
    notes: bool = True
    mode: Literal["full", "quick"] = "full"

    @model_validator(mode="after")
    def _normalise(self):
        for name, _, _ in _RANGES:
            lo, hi = getattr(self, f"{name}_min"), getattr(self, f"{name}_max")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{name}_min must be <= {name}_max")
        self.label, self.label_not = _uuids(self.label), _uuids(self.label_not)
        for k in FACETS:
            out = set(getattr(self, f"{k}_not"))
            if out:
                setattr(self, k, [v for v in getattr(self, k) if v not in out])
        return self


def uses_new_params(f: SearchParams) -> bool:
    """True if anything beyond the legacy parameter set is set."""
    if f.sort not in _LEGACY_SORTS:
        return True
    dflt = SearchParams()
    return any(getattr(f, k) != getattr(dflt, k) for k in SearchParams.model_fields if k not in _LEGACY_KEYS)


def cache_tuple(f: SearchParams) -> tuple:
    """Canonical hashable form of every ES-relevant field."""
    skip = {"page", "size", "after", "notes", "mode", "sort"}
    d = f.model_dump(exclude=skip)
    return tuple((k, tuple(v) if isinstance(v, list) else v) for k, v in sorted(d.items()))
