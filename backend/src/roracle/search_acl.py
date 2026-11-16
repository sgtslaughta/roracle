"""Elastic ACL filter mirroring `roracle.access.visible_documents` (SQL)."""

from typing import Any

from roracle.access import org_ancestors, org_units
from roracle.auth import Principal


def acl_filter(p: Principal) -> dict[str, Any]:
    """ES filter for chunks visible to `p`. Use in filter context (`bool.filter`), not `must`. Callers must also add `is_latest: true`
    (and deleted docs have no chunks: deletion removes them from the index)."""
    if p.is_admin:
        return {"match_all": {}}
    uid = str(p.user_id)
    should: list[dict[str, Any]] = [
        {"term": {"visibility": "everyone"}},
        {"term": {"owner_id": uid}},
        {"term": {"share_users": uid}},
    ]
    if p.groups:
        should.append({"terms": {"share_groups": org_units(list(p.groups))}})
    should.append(
        {
            "bool": {
                "must": [
                    {"term": {"visibility": "org_unit"}},
                    {"terms": {"org_unit": org_ancestors(list(p.groups))}},
                ]
            }
        }
    )
    return {"bool": {"should": should, "minimum_should_match": 1}}
