"""Document visibility rule. P3 mirrors this for Elastic filters."""

import re

from sqlalchemy import and_, exists, false, or_, select, true
from sqlalchemy.sql.elements import ColumnElement

from roracle.auth import Principal
from roracle.models import Document, DocumentShare


def _share(kind: str, principals: list[str]) -> ColumnElement[bool]:
    return exists(
        select(1).where(
            DocumentShare.document_id == Document.id,
            DocumentShare.principal_kind == kind,
            DocumentShare.principal.in_(principals),
        )
    )


def normalize_path(s: str) -> str:
    """R7: leading "/", no "//", no trailing "/" (except root)."""
    s = re.sub(r"/{2,}", "/", s.strip())
    if not s.startswith("/"):
        raise ValueError("path must start with '/'")
    return s.rstrip("/") or "/"


def group_path(s: str) -> str:
    """normalize_path for a share/co-author group: "/" (everyone) is not a group."""
    s = normalize_path(s)
    if s == "/":
        raise ValueError("group must not be the root")
    return s


def org_units(groups: list[str]) -> list[str]:
    """Rooted, normalized org units from token groups; blank/root-only groups are skipped (never widen to "/")."""
    out: list[str] = []
    for g in groups:
        g = g.strip()
        if not g.strip("/"):
            continue
        u = normalize_path(g if g.startswith("/") else "/" + g)
        if u not in out:
            out.append(u)
    return out


ORG_UNIT_ERROR = "cannot determine org unit; choose another visibility"


def org_unit_error(visibility: str, org_unit: str) -> str | None:
    """Fail closed: `org_unit` visibility needs a usable (non-root, non-empty) org unit."""
    return ORG_UNIT_ERROR if visibility == "org_unit" and not org_units([org_unit]) else None


def org_ancestors(groups: list[str]) -> list[str]:
    """Each org unit plus all its ancestor paths, plus "/" (whole org); sorted, deduped."""
    out = {"/"}
    for g in org_units(groups):
        while g:
            out.add(g)
            g = g.rpartition("/")[0]
    return sorted(out)


def visible_documents(p: Principal) -> ColumnElement[bool]:
    live = Document.deleted_at.is_(None)
    if p.is_admin:
        return and_(live, true())
    groups = list(p.groups)
    org = and_(Document.visibility == "org_unit", Document.org_unit.in_(org_ancestors(groups)))
    return and_(
        live,
        or_(
            Document.visibility == "everyone",
            Document.owner_id == p.user_id,
            _share("user", [str(p.user_id)]),
            _share("group", groups) if groups else false(),
            org,
        ),
    )
