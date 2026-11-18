"""Elastic ACL filter mirroring `roracle.access.visible_documents` (SQL)."""

from typing import Any

from roracle.access import org_ancestors, org_units
from roracle.auth import Principal
from roracle.labels.policy import Policy


def acl_filter(p: Principal, policy: Policy) -> dict[str, Any]:
    """ES filter for chunks visible to `p`. Use in filter context (`bool.filter`), not `must`. Callers must also add `is_latest: true`
    (and deleted docs have no chunks: deletion removes them from the index).
    PAT principals also get gate (b): `must_not label_id in block_agents` (admins too). Gate sets resolve at query time."""
    base = _visibility(p)
    blocked = sorted(policy.ids("block_agents")) if p.is_agent else []
    if not blocked:
        return base
    return {"bool": {"filter": [base], "must_not": [{"terms": {"label_id": blocked}}]}}


def _visibility(p: Principal) -> dict[str, Any]:
    if p.is_admin:
        return {"match_all": {}}
    uid = str(p.user_id)
    groups = org_units(list(p.groups))
    should: list[dict[str, Any]] = [
        {"term": {"visibility": "everyone"}},
        {"term": {"owner_id": uid}},
        {"term": {"coauthor_users": uid}},  # published-paper co-authors: any visibility
    ]
    # shares grant access only while visibility == "shared" (stale rows under other visibilities are inert)
    grants: list[dict[str, Any]] = [{"term": {"share_users": uid}}]
    if groups:
        grants.append({"terms": {"share_groups": groups}})
    should.append(
        {
            "bool": {
                "must": [{"term": {"visibility": "shared"}}],
                "filter": [{"bool": {"should": grants, "minimum_should_match": 1}}],
            }
        }
    )
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
