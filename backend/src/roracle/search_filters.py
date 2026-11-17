"""ES filter clauses for library search; ACL (`acl_filter`) is added by the caller."""

from roracle.auth import Principal
from roracle.search_params import FACETS, SearchParams
from roracle.search_scope import DocScope

_FIELD = {
    "tags": "tags",
    "author": "authors",
    "venue": "venue",
    "file_type": "file_type",
    "org_unit": "org_unit",
    "label": "label_id",
    "year": "year",
}
Clauses = tuple[list[dict], list[dict]]


def _rng(name: str, lo, hi, conv=lambda v: v) -> dict | None:
    r = {k: conv(v) for k, v in (("gte", lo), ("lte", hi)) if v is not None}
    return {"range": {name: r}} if r else None


def facet_clauses(f: SearchParams, skip: str | None = None) -> Clauses:
    flt: list[dict] = []
    nn: list[dict] = []
    for k in FACETS:
        if k == "collection" or k == skip:
            continue
        fld = _FIELD[k]
        vals, out = getattr(f, k), getattr(f, f"{k}_not")
        if vals:
            if getattr(f, f"{k}_op", "or") == "and":
                flt += [{"term": {fld: v}} for v in vals]
            else:
                flt.append({"terms": {fld: vals}})
        if out:
            nn.append({"terms": {fld: out}})
    if skip != "year" and (r := _rng("year", f.year_min, f.year_max)):
        flt.append(r)
    return flt, nn


def plain_clauses(f: SearchParams, p: Principal, scope: DocScope) -> Clauses:
    flt: list[dict] = []
    nn: list[dict] = []
    for r in (
        _rng("page_count", f.pages_min, f.pages_max),
        _rng("added_at", f.added_min, f.added_max, lambda d: d.isoformat()),
    ):
        if r:
            flt.append(r)
    for k, fld in (("has_doi", "has_doi"), ("has_abstract", "has_abstract"), ("has_refs", "has_refs")):
        v = getattr(f, k)
        if v == "yes":
            flt.append({"term": {fld: True}})
        elif v == "no":  # false or missing
            nn.append({"term": {fld: True}})
    me = {"term": {"owner_id": str(p.user_id)}}
    if f.owner == "mine":
        flt.append(me)
    elif f.owner == "shared":
        nn.append(me)
    if scope.include is not None:
        flt.append({"terms": {"doc_id": scope.include}})
    if scope.exclude:
        nn.append({"terms": {"doc_id": scope.exclude}})
    return flt, nn


def head_clauses(f: SearchParams) -> list[dict]:
    out = [{"term": {"doc_head": True}}, {"term": {"searchable": True}}]
    if f.votes_min is not None:
        out.append({"range": {"votes_net": {"gte": f.votes_min}}})
    if f.cited_min is not None:
        out.append({"range": {"cited_by_global": {"gte": f.cited_min}}})
    return out
