"""Disjunctive facet aggregations and the case-insensitive find box (pure body builders/readers)."""

import re
from typing import Any, Literal

from roracle.labels.policy import Policy, label_json
from roracle.search_filters import _FIELD, facet_clauses
from roracle.search_limits import FACET_MAX
from roracle.search_params import FACETS, SearchParams

_LUCENE_SPECIAL = re.compile(r'([.?+*|{}\[\]()"\\#@&<>~])')


def _bool(f: SearchParams, skip: str) -> dict:
    flt, nn = facet_clauses(f, skip=skip)
    return {"bool": {"filter": flt, "must_not": nn}}


def facet_aggs(f: SearchParams, colls: dict[str, tuple[str, list[str]]]) -> dict:
    """One `f_<name>` filter agg per facet, filtered by every OTHER facet (disjunctive counts)."""
    out: dict[str, Any] = {}
    for k in (*FACETS, "year"):
        if k == "collection" and not colls:
            continue  # ES rejects an empty `filters` agg
        if k == "year":
            inner = {"histogram": {"field": "year", "interval": 1, "min_doc_count": 1}}
        elif k == "collection":
            inner = {
                "filters": {"filters": {cid: {"terms": {"doc_id": ids}} for cid, (_, ids) in colls.items()}}
            }
        else:
            inner = {"terms": {"field": _FIELD[k], "size": FACET_MAX}}
        out[f"f_{k}"] = {"filter": _bool(f, k), "aggs": {"v": inner}}
    return out


def read_facets(aggs: dict, policy: Policy, colls: dict[str, tuple[str, list[str]]]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for k in (*FACETS, "year"):
        v = aggs.get(f"f_{k}", {}).get("v", {})
        if k == "collection":
            rows = [
                {"value": cid, "count": b["doc_count"], "label": colls[cid][0]}
                for cid, b in v.get("buckets", {}).items()
                if cid in colls and b["doc_count"] > 0
            ]
        else:
            rows = [{"value": str(b["key"]), "count": b["doc_count"]} for b in v.get("buckets", [])]
            if k == "year":
                rows = [{**r, "value": str(int(float(r["value"])))} for r in rows]
            elif k == "label":
                rows = [
                    {**r, "label": label_json(policy.get(r["value"]))}
                    for r in rows
                    if _known(policy, r["value"])
                ]
        out[k] = sorted(
            rows, key=(lambda r: int(r["value"])) if k == "year" else lambda r: (-r["count"], r["value"])
        )
    return out


def _known(policy: Policy, label_id: str) -> bool:
    try:
        policy.get(label_id)
    except KeyError:
        return False
    return True


def ci_regex(s: str) -> str:
    """Lucene regex matching `s` as a case-insensitive literal substring (never a pattern)."""
    parts = []
    for c in s:
        lo, up = c.lower(), c.upper()
        parts.append(
            f"[{lo}{up}]" if lo != up and len(lo) == len(up) == 1 else _LUCENE_SPECIAL.sub(r"\\\1", c)
        )
    return ".*" + "".join(parts) + ".*"


def find_aggs(name: Literal["author", "venue", "tags"], find: str, f: SearchParams) -> dict:
    return {
        "filter": _bool(f, name),
        "aggs": {"v": {"terms": {"field": _FIELD[name], "include": ci_regex(find), "size": FACET_MAX}}},
    }
